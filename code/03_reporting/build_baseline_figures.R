suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(haven)
  library(ggplot2)
  library(scales)
})

data.table::setDTthreads(1L)
source_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
source(file.path(source_root, "code", "read_run_spec.R"))
dataset_path <- spec$paths$calibration_data
database_path <- spec$paths$work_database
figure_dir <- run_path("results/figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

base <- as.data.table(read_dta(dataset_path))
setorder(base, reuniao)
if (anyDuplicated(base$reuniao) || !identical(as.integer(base$reuniao), 203:281)) {
  stop("Baseline figures require exactly meetings 203:281")
}
base[, date_daily := as.Date(date_daily)]
base[, after_knowledge_cutoff := date_daily > as.Date(spec$cutoff)]
base[, model := spec$model]
cutoff_date <- as.Date(spec$cutoff)
cutoff_region_end <- as.Date(Inf, origin = "1970-01-01")
cutoff_caption <- "After model knowledge cutoff (2026-02-16). Shading identifies meeting dates."
qa_subtitle <- if (spec$design_type == "production") NULL else "Synthetic QA fixture"

mixture_columns <- c(
  "forecast_anchored", "forecast_unanchored", "wc_orig",
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
    `LLM anchored` = forecast_anchored,
    `LLM unanchored` = forecast_unanchored
  )],
  id.vars = "date", variable.name = "series", value.name = "expectation"
)
agents[, series := factor(series, levels = c("Focus", "LLM anchored", "LLM unanchored"))]
figure_agents <- ggplot(agents, aes(date, expectation, colour = series, linetype = series)) +
  annotate("rect", xmin = cutoff_date, xmax = cutoff_region_end, ymin = -Inf, ymax = Inf,
           fill = "grey75", alpha = 0.22) +
  geom_vline(xintercept = cutoff_date, linetype = "dashed", colour = "grey45", linewidth = 0.4) +
  geom_line(linewidth = 0.72, na.rm = TRUE) +
  scale_dates() +
  scale_y_continuous(
    name = "12-month-ahead IPCA expectation (%)",
    labels = label_number(accuracy = 0.1)
  ) +
  scale_colour_manual(values = palette, drop = FALSE) +
  scale_linetype_manual(values = linetypes, drop = FALSE) +
  labs(x = NULL, caption = cutoff_caption, subtitle = qa_subtitle) + theme_journal()
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
  annotate("rect", xmin = cutoff_date, xmax = cutoff_region_end, ymin = -Inf, ymax = Inf,
           fill = "grey75", alpha = 0.22) +
  geom_vline(xintercept = cutoff_date, linetype = "dashed", colour = "grey45", linewidth = 0.4) +
  geom_line(linewidth = 0.72, na.rm = TRUE) +
  scale_dates() +
  scale_y_continuous(
    name = "12-month-ahead IPCA expectation (%)",
    labels = label_number(accuracy = 0.1)
  ) +
  scale_colour_manual(values = palette, drop = FALSE) +
  scale_linetype_manual(values = linetypes, drop = FALSE) +
  labs(x = NULL, caption = cutoff_caption, subtitle = qa_subtitle) + theme_journal()
save_figure(figure_composite, "rr_focus_composite_current.pdf")

connection <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(connection), add = TRUE)
tone <- as.data.table(dbGetQuery(
  connection,
  paste(
    "SELECT meeting_number AS reuniao, iteration, role, 100.0 * score AS tone",
    paste0("FROM tone_scores WHERE run_id = ", dbQuoteString(connection, spec$run_ids$main_text)),
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
  annotate("rect", xmin = cutoff_date, xmax = cutoff_region_end, ymin = -Inf, ymax = Inf,
           fill = "grey75", alpha = 0.22) +
  geom_vline(xintercept = cutoff_date, linetype = "dashed", colour = "grey45", linewidth = 0.4) +
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
  labs(x = NULL, caption = cutoff_caption, subtitle = qa_subtitle) + theme_journal()
save_figure(figure_tone, "rr_counterfactual_tone_25draw.pdf")

tone_plot_data <- merge(tone_lines, base[, .(date = date_daily, meeting_number = reuniao,
  after_knowledge_cutoff, model)], by = "date", all.x = TRUE)
tone_plot_data[, calibration_type := "lexicon_tone"]
if (anyNA(tone_plot_data)) stop("Tone plotting dates do not match the canonical panel")
fwrite(tone_plot_data, run_path("outputs/tables/tone_figure_data.csv"))

fwrite(
  base[, .(reuniao, date_daily, after_knowledge_cutoff, model,
           calibration_type = "full_79_and_leave_one_meeting_out", inflacao12m,
           forecast_anchored, forecast_unanchored, full_sample_composite, loo_composite)],
  run_path("outputs/tables/baseline_figure_data.csv")
)

# This figure uses the separate 69-meeting frozen fit, never either fitted line above.
predictions <- fread(run_path(file.path("outputs/tables", paste0("knowledge_cutoff_predictions_", result_stamp, ".csv"))))
framing <- fread(run_path(file.path("outputs/tables", paste0("knowledge_cutoff_framing_meetings_", result_stamp, ".csv"))))
summary <- fread(run_path(file.path("outputs/tables", paste0("knowledge_cutoff_framing_summary_", result_stamp, ".csv"))))
predictions[, date_daily := as.Date(date_daily)]
framing[, date_daily := as.Date(date_daily)]
predictions[, model := spec$model]
predictions[, calibration_type := fifelse(series == "frozen_calibrated_composite", "frozen_69",
  fifelse(series == "focus_persistence", "previous_meeting_focus", "raw_original_path"))]
framing[, after_knowledge_cutoff := date_daily > cutoff_date]
framing[, model := spec$model]
framing[, calibration_type := fifelse(forecaster_type == "composite", "frozen_69_contrast", "raw_common_prior_contrast")]
fwrite(predictions, run_path("outputs/tables/knowledge_cutoff_agreement_figure_data.csv"))
fwrite(framing, run_path("outputs/tables/knowledge_cutoff_framing_figure_data.csv"))
if (nrow(predictions) != 40L || anyDuplicated(predictions[, .(meeting_number, series)]) ||
    !identical(sort(unique(predictions$meeting_number)), 272:281)) stop("Incomplete cutoff predictions")
actual <- unique(predictions[, .(meeting_number, date_daily, expectation = focus)])
actual[, series := "Focus"]
frozen <- predictions[series == "frozen_calibrated_composite",
                      .(meeting_number, date_daily, expectation = prediction, series = "Frozen 69-meeting calibration")]
agreement <- rbindlist(list(actual, frozen), use.names = TRUE)
figure_agreement <- ggplot(agreement, aes(date_daily, expectation, colour = series)) +
  annotate("rect", xmin = cutoff_date, xmax = cutoff_region_end, ymin = -Inf, ymax = Inf, fill = "grey75", alpha = 0.22) +
  geom_vline(xintercept = cutoff_date, linetype = "dashed", colour = "grey45", linewidth = 0.4) +
  geom_line(linewidth = 0.65) + geom_point(size = 1.7) +
  scale_colour_manual(values = c("Focus" = "#222222", "Frozen 69-meeting calibration" = "#0072B2")) +
  scale_x_date(date_breaks = "3 months", date_labels = "%b\n%Y") +
  labs(x = NULL, y = "12-month-ahead IPCA expectation (%)", subtitle = qa_subtitle) + theme_journal()
figure_errors <- ggplot(predictions, aes(date_daily, signed_error, colour = series, shape = series)) +
  annotate("rect", xmin = cutoff_date, xmax = cutoff_region_end, ymin = -Inf, ymax = Inf, fill = "grey75", alpha = 0.22) +
  geom_vline(xintercept = cutoff_date, linetype = "dashed", colour = "grey45", linewidth = 0.4) +
  geom_hline(yintercept = 0, colour = "grey55", linewidth = 0.3) + geom_point(size = 1.7) +
  scale_colour_manual(values = c(focus_persistence = "#222222", frozen_calibrated_composite = "#0072B2",
    raw_anchored = "#56B4E9", raw_unanchored = "#D55E00"),
    labels = c(focus_persistence = "Focus persistence", frozen_calibrated_composite = "Frozen composite",
      raw_anchored = "Raw anchored", raw_unanchored = "Raw unanchored")) +
  scale_shape_discrete(labels = c(focus_persistence = "Focus persistence", frozen_calibrated_composite = "Frozen composite",
    raw_anchored = "Raw anchored", raw_unanchored = "Raw unanchored")) +
  scale_x_date(date_breaks = "3 months", date_labels = "%b\n%Y") +
  labs(x = NULL, y = "Prediction minus Focus (pp)",
       caption = paste0(cutoff_caption, "\nFive meetings per window. Descriptive Focus agreement.")) +
  theme_journal()
cutoff_agreement <- cowplot::plot_grid(figure_agreement, figure_errors, ncol = 1, align = "v")
ggsave(run_path("results/figures/rr_knowledge_cutoff_agreement.pdf"), cutoff_agreement,
       width = 8.8, height = 6.8, units = "in", device = grDevices::cairo_pdf)

# Draws were averaged within each meeting before the equally weighted window mean.
framing[, forecaster_type := factor(forecaster_type, levels = c("anchored", "unanchored", "composite"))]
framing[, contrast := factor(contrast, levels = c("dove_high", "dove_low", "hawk_low", "hawk_high"))]
figure_framing <- ggplot(framing, aes(date_daily, contrast_pp, colour = contrast, shape = contrast)) +
  annotate("rect", xmin = cutoff_date, xmax = cutoff_region_end, ymin = -Inf, ymax = Inf, fill = "grey75", alpha = 0.22) +
  geom_vline(xintercept = cutoff_date, linetype = "dashed", colour = "grey45", linewidth = 0.4) +
  geom_hline(yintercept = 0, colour = "grey55", linewidth = 0.3) + geom_point(size = 1.8) +
  geom_segment(data = summary,
               aes(x = as.Date(ifelse(window == "pre", min(framing[window == "pre", date_daily]),
                                      min(framing[window == "post", date_daily])), origin = "1970-01-01"),
                   xend = as.Date(ifelse(window == "pre", max(framing[window == "pre", date_daily]),
                                         max(framing[window == "post", date_daily])), origin = "1970-01-01"),
                   y = mean_contrast_pp, yend = mean_contrast_pp, colour = contrast),
               inherit.aes = FALSE, linewidth = 0.55, linetype = "dashed") +
  facet_wrap(~forecaster_type, ncol = 1, scales = "free_y") +
  scale_colour_manual(values = c(dove_high = "#0072B2", dove_low = "#56B4E9", hawk_low = "#E69F00", hawk_high = "#D55E00"),
    breaks = c("dove_high", "dove_low", "hawk_low", "hawk_high"), labels = c("High dove", "Low dove", "Low hawk", "High hawk")) +
  scale_shape_discrete(breaks = c("dove_high", "dove_low", "hawk_low", "hawk_high"),
    labels = c("High dove", "Low dove", "Low hawk", "High hawk")) +
  scale_x_date(date_breaks = "3 months", date_labels = "%b\n%Y") +
  labs(x = NULL, y = "Arm minus neutral forecast (pp)", subtitle = qa_subtitle,
       caption = paste0(cutoff_caption, "\nPoints are meeting means. Dashed segments are equal-meeting window means (n=5).")) +
  theme_journal()
ggsave(run_path("results/figures/rr_knowledge_cutoff_framing.pdf"), figure_framing,
       width = 8.8, height = 8.0, units = "in", device = grDevices::cairo_pdf)
if (spec$design_type != "production") {
  dir.create(run_path("results/qa"), recursive = TRUE, showWarnings = FALSE)
  ggsave(run_path("results/qa/knowledge_cutoff_agreement.png"), cutoff_agreement, width = 8.8, height = 6.8, dpi = 150)
  ggsave(run_path("results/qa/knowledge_cutoff_framing.png"), figure_framing, width = 8.8, height = 8.0, dpi = 150)
}
