suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(haven)
  library(ggplot2)
  library(scales)
})

data.table::setDTthreads(1L)
root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
dataset_path <- file.path(root, "outputs", "work", "gpt55_calibration_data.dta")
database_path <- file.path(root, "outputs", "work", "prompt_iv_replication.sqlite")
figure_dir <- file.path(root, "results", "figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

base <- as.data.table(read_dta(dataset_path))
setorder(base, reuniao)
base <- unique(base, by = "reuniao")
base[, date_daily := as.Date(date_daily)]

mixture_columns <- c(
  "forecast_anchored_gpt55", "forecast_unanchored_gpt55", "wc_orig",
  "ibcbr_agro", "ibcbr_service", "ibcbr_industry"
)
formula <- as.formula(paste("inflacao12m ~", paste(mixture_columns, collapse = " + ")))
mixture_model <- lm(formula, data = base)
base[, full_sample_composite := as.numeric(predict(mixture_model, newdata = base))]
base[, loo_composite := vapply(seq_len(.N), function(index) {
  fit <- lm(formula, data = base[-index])
  as.numeric(predict(fit, newdata = base[index]))
}, numeric(1L))]

palette <- c(
  "Focus" = "#222222",
  "LLM anchored" = "#0072B2",
  "LLM unanchored" = "#D55E00",
  "LLM composite, full-sample fit" = "#0072B2",
  "LLM composite, leave-one-out" = "#D55E00",
  "Original tone" = "#222222",
  "Hawkish mean" = "#D55E00",
  "Dovish mean" = "#0072B2"
)
linetypes <- c(
  "Focus" = "solid",
  "LLM anchored" = "dotdash",
  "LLM unanchored" = "longdash",
  "LLM composite, full-sample fit" = "solid",
  "LLM composite, leave-one-out" = "longdash",
  "Original tone" = "solid",
  "Hawkish mean" = "longdash",
  "Dovish mean" = "dashed"
)

theme_journal <- function() {
  theme_classic(base_size = 9.5, base_family = "sans") +
    theme(
      axis.line = element_line(linewidth = 0.35, colour = "#222222"),
      axis.ticks = element_line(linewidth = 0.35, colour = "#222222"),
      axis.ticks.length = grid::unit(2.5, "pt"),
      panel.grid.major.y = element_line(colour = "#E3E3E3", linewidth = 0.3),
      panel.grid.minor = element_blank(),
      legend.position = "bottom",
      legend.title = element_blank(),
      legend.key.width = grid::unit(19, "pt"),
      plot.margin = margin(6, 8, 4, 6)
    )
}

scale_dates <- function() {
  scale_x_date(
    breaks = breaks_width("2 years"),
    labels = label_date("%Y"),
    expand = expansion(mult = c(0.01, 0.01))
  )
}

save_figure <- function(plot, filename, width = 7.1, height = 4.0) {
  ggsave(
    file.path(figure_dir, filename), plot = plot, width = width, height = height,
    units = "in", device = grDevices::cairo_pdf
  )
}

agents <- melt(
  base[, .(
    date = date_daily,
    Focus = inflacao12m,
    `LLM anchored` = forecast_anchored_gpt55,
    `LLM unanchored` = forecast_unanchored_gpt55
  )],
  id.vars = "date", variable.name = "series", value.name = "expectation"
)
agents[, series := factor(series, levels = c("Focus", "LLM anchored", "LLM unanchored"))]
figure_agents <- ggplot(agents, aes(date, expectation, colour = series, linetype = series)) +
  geom_line(linewidth = 0.72, na.rm = TRUE) +
  scale_dates() +
  scale_y_continuous(
    name = "12-month-ahead IPCA expectation (%)",
    labels = label_number(accuracy = 0.1)
  ) +
  scale_colour_manual(values = palette, drop = FALSE) +
  scale_linetype_manual(values = linetypes, drop = FALSE) +
  labs(x = NULL) + theme_journal()
save_figure(figure_agents, "rr_focus_agents_current.pdf")

composite <- melt(
  base[, .(
    date = date_daily,
    Focus = inflacao12m,
    `LLM composite, full-sample fit` = full_sample_composite,
    `LLM composite, leave-one-out` = loo_composite
  )],
  id.vars = "date", variable.name = "series", value.name = "expectation"
)
composite[, series := factor(
  series,
  levels = c("Focus", "LLM composite, full-sample fit", "LLM composite, leave-one-out")
)]
figure_composite <- ggplot(
  composite, aes(date, expectation, colour = series, linetype = series)
) +
  geom_line(linewidth = 0.72, na.rm = TRUE) +
  scale_dates() +
  scale_y_continuous(
    name = "12-month-ahead IPCA expectation (%)",
    labels = label_number(accuracy = 0.1)
  ) +
  scale_colour_manual(values = palette, drop = FALSE) +
  scale_linetype_manual(values = linetypes, drop = FALSE) +
  labs(x = NULL) + theme_journal()
save_figure(figure_composite, "rr_focus_composite_current.pdf")

connection <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(connection), add = TRUE)
tone <- as.data.table(dbGetQuery(
  connection,
  paste(
    "SELECT meeting_number AS reuniao, iteration, role, 100.0 * score AS tone",
    "FROM tone_scores WHERE run_id = 'v10_referee_d5_25draws_20260620'",
    "AND role IN ('hawk_text', 'dove_text')"
  )
))
tone[, series := fifelse(role == "hawk_text", "Hawkish mean", "Dovish mean")]
tone_summary <- tone[, .(
  mean = mean(tone),
  p10 = quantile(tone, 0.10, names = FALSE),
  p90 = quantile(tone, 0.90, names = FALSE)
), by = .(reuniao, series)]
tone_summary <- merge(
  tone_summary,
  base[, .(reuniao, date = date_daily)],
  by = "reuniao", all.x = TRUE
)
tone_lines <- rbindlist(list(
  base[, .(date = date_daily, series = "Original tone", tone = score_orig)],
  tone_summary[, .(date, series, tone = mean)]
))
tone_lines[, series := factor(series, levels = c("Original tone", "Hawkish mean", "Dovish mean"))]

figure_tone <- ggplot() +
  geom_ribbon(
    data = tone_summary[series == "Hawkish mean"],
    aes(date, ymin = p10, ymax = p90),
    fill = palette[["Hawkish mean"]], alpha = 0.13, colour = NA
  ) +
  geom_ribbon(
    data = tone_summary[series == "Dovish mean"],
    aes(date, ymin = p10, ymax = p90),
    fill = palette[["Dovish mean"]], alpha = 0.13, colour = NA
  ) +
  geom_line(
    data = tone_lines,
    aes(date, tone, colour = series, linetype = series),
    linewidth = 0.72, na.rm = TRUE
  ) +
  scale_dates() +
  scale_y_continuous(
    name = "Lexicon tone (percentage points)",
    labels = label_number(accuracy = 0.1)
  ) +
  scale_colour_manual(values = palette, drop = FALSE) +
  scale_linetype_manual(values = linetypes, drop = FALSE) +
  labs(x = NULL) + theme_journal()
save_figure(figure_tone, "rr_counterfactual_tone_25draw.pdf")

fwrite(
  base[, .(reuniao, date_daily, inflacao12m, full_sample_composite, loo_composite)],
  file.path(root, "outputs", "tables", "baseline_figure_data.csv")
)
