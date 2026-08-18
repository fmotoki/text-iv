suppressPackageStartupMessages({
  library(data.table)
  library(DBI)
  library(fixest)
  library(ggplot2)
  library(haven)
  library(jsonlite)
  library(patchwork)
  library(RSQLite)
  library(scales)
})

parse_cli_args <- function(defaults) {
  args <- commandArgs(trailingOnly = TRUE)
  out <- defaults
  index <- 1L
  while (index <= length(args)) {
    key <- sub("^--", "", args[[index]])
    if (!key %in% names(out) || index == length(args)) {
      stop(sprintf("Unknown or incomplete argument: %s", args[[index]]))
    }
    out[[key]] <- args[[index + 1L]]
    index <- index + 2L
  }
  out
}

args <- parse_cli_args(list(
  database = "outputs/work/prompt_iv_replication.sqlite",
  run_id = "arm_specific_prior_dose_6arm_5draws_v1_20260714",
  dataset = "outputs/work/gpt55_calibration_data.dta",
  stamp = "20260714",
  output_dir = "outputs/tables",
  figure_dir = "results/figures",
  qa_figure_dir = "results/qa",
  paper_table_dir = "results/tables",
  figure_stem = "rr_arm_specific_prior_dose_gradient",
  publish_paper = "true",
  expected_meetings = "71",
  expected_draws = "5"
))

publish_paper <- tolower(args$publish_paper) %in% c("true", "1", "yes")
expected_meetings <- as.integer(args$expected_meetings)
expected_draws <- as.integer(args$expected_draws)
dir.create(args$output_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(args$figure_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(args$qa_figure_dir, recursive = TRUE, showWarnings = FALSE)
if (publish_paper) {
  dir.create(args$paper_table_dir, recursive = TRUE, showWarnings = FALSE)
}

con <- dbConnect(SQLite(), args$database)
on.exit(dbDisconnect(con), add = TRUE)

task_status <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT",
    "  COUNT(*) AS tasks,",
    "  SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END) AS completed,",
    "  SUM(CASE WHEN r.status IS NULL THEN 1 ELSE 0 END) AS missing,",
    "  SUM(CASE WHEN r.status IS NOT NULL AND r.status != 'completed' THEN 1 ELSE 0 END) AS noncompleted",
    "FROM agent_tasks AS t",
    "LEFT JOIN agent_results_latest AS r",
    "  ON r.run_id = t.run_id AND r.task_id = t.task_id",
    "WHERE t.run_id = ?",
    sep = "\n"
  ),
  params = list(args$run_id)
))
if (nrow(task_status) != 1L || task_status$tasks == 0L ||
    task_status$completed != task_status$tasks) {
  stop(sprintf(
    "Arm-specific run is incomplete: %s/%s completed",
    task_status$completed,
    task_status$tasks
  ))
}

agent <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT",
    "  t.run_id,",
    "  t.task_id,",
    "  t.meeting_number,",
    "  t.corpus_iteration,",
    "  t.source_role,",
    "  t.forecaster_type,",
    "  t.state_group,",
    "  t.sequence_index,",
    "  t.initial_expectation,",
    "  t.pair_id,",
    "  t.source_run_id,",
    "  t.source_text_hash,",
    "  t.design,",
    "  t.prompt_version,",
    "  r.previous_expectation,",
    "  r.new_expectation,",
    "  r.forecast_revision,",
    "  100.0 * ts.score AS tone_pp",
    "FROM agent_tasks AS t",
    "JOIN agent_results_latest AS r",
    "  ON r.run_id = t.run_id AND r.task_id = t.task_id",
    "JOIN tone_scores AS ts",
    "  ON ts.run_id = t.source_run_id",
    " AND ts.meeting_number = t.meeting_number",
    " AND ts.iteration = t.corpus_iteration",
    " AND ts.role = t.source_role",
    "WHERE t.run_id = ? AND r.status = 'completed'",
    "ORDER BY t.state_group, t.forecaster_type, t.sequence_index",
    sep = "\n"
  ),
  params = list(args$run_id)
))

expected_roles <- c(
  "dove_high", "dove_low", "original", "neutral", "hawk_low", "hawk_high"
)
if (!setequal(unique(agent$source_role), expected_roles)) {
  stop("The six expected textual arms were not recovered")
}
if (uniqueN(agent$meeting_number) != expected_meetings ||
    uniqueN(agent$corpus_iteration) != expected_draws ||
    uniqueN(agent$forecaster_type) != 2L ||
    nrow(agent) != expected_meetings * expected_draws * 6L * 2L) {
  stop("The arm-specific panel does not match the requested dimensions")
}
if (any(agent$design != "arm_specific_prior_path") ||
    any(agent$prompt_version != "forecaster_common_prior_v1")) {
  stop("Unexpected design or forecaster prompt version")
}
if (max(abs(
  agent$forecast_revision -
    (agent$new_expectation - agent$previous_expectation)
)) > 1e-9) {
  stop("Forecast-revision identities do not hold")
}

setorder(agent, state_group, forecaster_type, sequence_index)
agent[, expected_prior := shift(new_expectation), by = .(state_group, forecaster_type)]
agent[sequence_index == 1L, expected_prior := initial_expectation]
if (anyNA(agent$expected_prior) ||
    max(abs(agent$previous_expectation - agent$expected_prior)) > 1e-9) {
  stop("An arm-specific recursive transition is broken")
}
agent[, assigned_dose := fifelse(
  source_role == "dove_high", -2,
  fifelse(
    source_role == "dove_low", -1,
    fifelse(
      source_role == "neutral", 0,
      fifelse(source_role == "hawk_low", 1,
              fifelse(source_role == "hawk_high", 2, NA_real_))
    )
  )
)]

mixture_columns <- c(
  "forecast_anchored_gpt55",
  "forecast_unanchored_gpt55",
  "wc_orig",
  "ibcbr_agro",
  "ibcbr_service",
  "ibcbr_industry"
)
baseline <- as.data.table(read_dta(args$dataset))
working <- baseline[
  complete.cases(baseline[, c("inflacao12m", mixture_columns), with = FALSE])
]
mixture_model <- lm(
  inflacao12m ~ forecast_anchored_gpt55 + forecast_unanchored_gpt55 + wc_orig +
    ibcbr_agro + ibcbr_service + ibcbr_industry,
  data = working
)
weights <- coef(mixture_model)
anchored_weight <- unname(weights[["forecast_anchored_gpt55"]])
unanchored_weight <- unname(weights[["forecast_unanchored_gpt55"]])

wide <- dcast(
  agent,
  meeting_number + corpus_iteration + source_role + assigned_dose + tone_pp ~
    forecaster_type,
  value.var = c("previous_expectation", "new_expectation", "forecast_revision")
)
required_wide <- c(
  "previous_expectation_anchored", "previous_expectation_unanchored",
  "new_expectation_anchored", "new_expectation_unanchored",
  "forecast_revision_anchored", "forecast_revision_unanchored"
)
expected_cells <- expected_meetings * expected_draws * 6L
if (anyNA(wide[, ..required_wide]) || nrow(wide) != expected_cells) {
  stop("Composite path panel is incomplete")
}
wide[, `:=`(
  level = anchored_weight * new_expectation_anchored +
    unanchored_weight * new_expectation_unanchored,
  prior_level = anchored_weight * previous_expectation_anchored +
    unanchored_weight * previous_expectation_unanchored,
  revision = anchored_weight * forecast_revision_anchored +
    unanchored_weight * forecast_revision_unanchored,
  pair_id = factor(paste(meeting_number, corpus_iteration, sep = "::")),
  path_id = factor(paste(corpus_iteration, source_role, sep = "::")),
  sample = "Composite"
)]

make_agent_sample <- function(forecaster, label) {
  data <- copy(agent[forecaster_type == forecaster])
  data[, `:=`(
    level = new_expectation,
    prior_level = previous_expectation,
    revision = forecast_revision,
    pair_id = factor(paste(meeting_number, corpus_iteration, sep = "::")),
    path_id = factor(paste(corpus_iteration, source_role, sep = "::")),
    sample = label
  )]
  data
}
samples <- list(
  Composite = wide,
  Anchored = make_agent_sample("anchored", "Anchored"),
  Unanchored = make_agent_sample("unanchored", "Unanchored")
)

arm_terms <- c("hawk_high", "hawk_low", "neutral", "dove_low", "dove_high")
coefficient_row <- function(model, term) {
  table <- coeftable(model)
  if (!term %in% rownames(table)) {
    stop(sprintf("Missing coefficient %s", term))
  }
  p_column <- grep("^Pr", colnames(table), value = TRUE)[1]
  data.table(
    estimate = unname(table[term, "Estimate"]),
    std_error = unname(table[term, "Std. Error"]),
    t_statistic = unname(table[term, "t value"]),
    p_value = unname(table[term, p_column])
  )
}

estimate_arm_model <- function(data, sample_label) {
  model_data <- copy(data)
  for (arm in arm_terms) {
    model_data[, (arm) := as.integer(source_role == arm)]
  }
  model <- feols(
    level ~ dove_high + dove_low + neutral + hawk_low + hawk_high | pair_id,
    data = model_data,
    vcov = ~meeting_number + path_id,
    notes = FALSE
  )
  meeting_model <- feols(
    level ~ dove_high + dove_low + neutral + hawk_low + hawk_high | pair_id,
    data = model_data,
    vcov = ~meeting_number,
    notes = FALSE
  )
  rows <- rbindlist(lapply(arm_terms, function(term) {
    primary <- coefficient_row(model, term)
    meeting_only <- coefficient_row(meeting_model, term)
    primary[, `:=`(
      meeting_only_std_error = meeting_only$std_error,
      meeting_only_p_value = meeting_only$p_value,
      sample = sample_label,
      arm = term,
      observations = nobs(model),
      pair_fe_groups = uniqueN(model_data$pair_id),
      meeting_clusters = uniqueN(model_data$meeting_number),
      trajectory_clusters = uniqueN(model_data$path_id),
      draws = uniqueN(model_data$corpus_iteration),
      trajectories = uniqueN(paste(model_data$corpus_iteration, model_data$source_role))
    )]
    primary
  }))
  setcolorder(rows, c(
    "sample", "arm", "estimate", "std_error", "t_statistic", "p_value",
    "meeting_only_std_error", "meeting_only_p_value", "observations",
    "pair_fe_groups", "meeting_clusters", "trajectory_clusters", "draws",
    "trajectories"
  ))
  list(model = model, meeting_model = meeting_model, result = rows)
}

arm_models <- lapply(names(samples), function(label) {
  estimate_arm_model(samples[[label]], label)
})
names(arm_models) <- names(samples)
arm_effects <- rbindlist(lapply(arm_models, `[[`, "result"))

estimate_trend <- function(data, sample_label) {
  generated <- copy(data[source_role != "original"])
  model <- feols(
    level ~ assigned_dose | pair_id,
    data = generated,
    vcov = ~meeting_number + path_id,
    notes = FALSE
  )
  meeting_model <- feols(
    level ~ assigned_dose | pair_id,
    data = generated,
    vcov = ~meeting_number,
    notes = FALSE
  )
  result <- coefficient_row(model, "assigned_dose")
  meeting_only <- coefficient_row(meeting_model, "assigned_dose")
  result[, `:=`(
    meeting_only_std_error = meeting_only$std_error,
    meeting_only_p_value = meeting_only$p_value,
    sample = sample_label,
    observations = nobs(model),
    pair_fe_groups = uniqueN(generated$pair_id),
    meeting_clusters = uniqueN(generated$meeting_number),
    trajectory_clusters = uniqueN(generated$path_id),
    draws = uniqueN(generated$corpus_iteration)
  )]
  result
}
path_trends <- rbindlist(lapply(names(samples), function(label) {
  estimate_trend(samples[[label]], label)
}))
setcolorder(path_trends, c(
  "sample", "estimate", "std_error", "t_statistic", "p_value",
  "meeting_only_std_error", "meeting_only_p_value", "observations",
  "pair_fe_groups", "meeting_clusters", "trajectory_clusters", "draws"
))

text_cells <- unique(agent[, .(
  meeting_number,
  corpus_iteration,
  source_role,
  assigned_dose,
  tone_pp
)])
if (nrow(text_cells) != expected_cells) {
  stop("Text-cell panel is incomplete")
}
tone_cells <- rbindlist(lapply(arm_terms, function(arm) {
  cell <- text_cells[source_role == arm]
  model <- feols(tone_pp ~ 1, data = cell, vcov = ~meeting_number, notes = FALSE)
  coefficient_row(model, "(Intercept)")[, arm := arm]
}))
tone_trend_model <- feols(
  tone_pp ~ assigned_dose | meeting_number^corpus_iteration,
  data = text_cells[source_role != "original"],
  vcov = ~meeting_number,
  notes = FALSE
)
tone_trend <- coefficient_row(tone_trend_model, "assigned_dose")

arm_effects_path <- file.path(
  args$output_dir,
  paste0("arm_specific_prior_dose_arm_effects_", args$stamp, ".csv")
)
path_trends_path <- file.path(
  args$output_dir,
  paste0("arm_specific_prior_dose_trends_", args$stamp, ".csv")
)
fwrite(arm_effects, arm_effects_path)
fwrite(path_trends, path_trends_path)

arm_labels <- c(
  dove_high = "High-intensity dove",
  dove_low = "Low-intensity dove",
  neutral = "Neutral rewrite",
  hawk_low = "Low-intensity hawk",
  hawk_high = "High-intensity hawk"
)
format_p_stars <- function(p_value) {
  fifelse(
    is.na(p_value), "",
    fifelse(
      p_value < 0.01, "***",
      fifelse(p_value < 0.05, "**", fifelse(p_value < 0.10, "*", ""))
    )
  )
}
format_estimate <- function(estimate, p_value) {
  paste0(sprintf("%.4f", estimate), format_p_stars(p_value))
}

write_arm_table <- function(path) {
  columns <- c("Composite", "Anchored", "Unanchored")
  lines <- c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    "\\caption{Persistent synthetic forecast gaps under arm-specific recursive priors}",
    "\\label{tab:arm-specific-prior-dose-arms}",
    "\\begin{footnotesize}",
    "\\begin{tabular}{lccc}",
    "\\toprule",
    " & (1) & (2) & (3) \\\\",
    " & Composite & Anchored & Unanchored \\\\",
    "\\midrule"
  )
  for (arm_name in arm_terms) {
    rows <- lapply(columns, function(label) {
      arm_effects[sample == label & get("arm") == arm_name]
    })
    lines <- c(
      lines,
      paste0(
        arm_labels[[arm_name]], " & ",
        paste(vapply(rows, function(x) format_estimate(x$estimate, x$p_value), ""),
              collapse = " & "),
        " \\\\"
      ),
      paste0(
        " & ",
        paste(vapply(rows, function(x) sprintf("(%.4f)", x$std_error), ""),
              collapse = " & "),
        " \\\\"
      )
    )
  }
  first_rows <- lapply(columns, function(label) arm_effects[sample == label][1])
  lines <- c(
    lines,
    "\\midrule",
    "Original-text trajectory (reference arm) & Yes & Yes & Yes \\\\",
    "Meeting-by-regeneration fixed effects & Yes & Yes & Yes \\\\",
    "Two-way clustered standard errors & Yes & Yes & Yes \\\\",
    paste0(
      "Observations & ",
      paste(vapply(first_rows, function(x) format(x$observations, big.mark = ","), ""),
            collapse = " & "),
      " \\\\"
    ),
    paste0(
      "Meeting-by-regeneration groups & ",
      paste(vapply(first_rows, function(x) format(x$pair_fe_groups, big.mark = ","), ""),
            collapse = " & "),
      " \\\\"
    ),
    paste0(
      "Recursive trajectory clusters & ", expected_draws * 6L, " & ",
      expected_draws * 6L, " & ", expected_draws * 6L, " \\\\"
    ),
    paste0(
      "Meeting clusters & ", expected_meetings, " & ", expected_meetings,
      " & ", expected_meetings, " \\\\"
    ),
    paste0(
      "Regeneration replicates & ", expected_draws, " & ", expected_draws,
      " & ", expected_draws, " \\\\"
    ),
    "\\bottomrule",
    "\\end{tabular}",
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste0(
      "\\item \\textit{Notes:} Each coefficient is the within-meeting-by-regeneration ",
      "difference between the synthetic forecast level under the indicated recursive ",
      "textual path and the forecast level under the recursive original-text path. ",
      "Every path starts from 3.80 percent at the first meeting; thereafter, each arm ",
      "receives its own previous forecast as the next prior. The same frozen forecast-only ",
      "prompt is used in all arms. These are cumulative path comparisons, not one-shot ",
      "common-prior effects. Standard errors in parentheses use two-way clustering by ",
      "Copom meeting and recursive trajectory (regeneration replicate by textual arm). ",
      "Meeting-only clustered standard errors are archived in the machine-readable output. ",
      "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
    ),
    "\\end{tablenotes}",
    "\\end{footnotesize}",
    "\\end{threeparttable}",
    "\\end{table}"
  )
  writeLines(lines, path, useBytes = TRUE)
}

table_output <- file.path(
  args$output_dir,
  paste0("arm_specific_prior_dose_arm_effects_", args$stamp, ".tex")
)
write_arm_table(table_output)
paper_table_output <- NULL
if (publish_paper) {
  paper_table_output <- file.path(
    args$paper_table_dir,
    "rr_arm_specific_prior_dose_arm_effects.tex"
  )
  write_arm_table(paper_table_output)
}

gradient_order <- c("hawk_high", "hawk_low", "neutral", "dove_low", "dove_high")
gradient_labels <- c(
  hawk_high = "High\nhawk",
  hawk_low = "Low\nhawk",
  neutral = "Neutral",
  dove_low = "Low\ndove",
  dove_high = "High\ndove"
)
arm_colors <- c(
  dove_high = "#0072B2",
  dove_low = "#56B4E9",
  neutral = "#777777",
  hawk_low = "#E69F00",
  hawk_high = "#D55E00"
)
theme_journal <- function() {
  theme_classic(base_size = 10, base_family = "serif") +
    theme(
      axis.line = element_line(linewidth = 0.35, colour = "#222222"),
      axis.ticks = element_line(linewidth = 0.35, colour = "#222222"),
      panel.grid.major.y = element_line(colour = "#E3E3E3", linewidth = 0.3),
      panel.grid.minor = element_blank(),
      plot.margin = margin(7, 9, 5, 7),
      plot.title = element_blank()
    )
}
format_plot_p <- function(p_value) {
  if (is.na(p_value)) return("p unavailable")
  if (p_value < 0.001) "p < 0.001" else sprintf("p = %.3f", p_value)
}

tone_plot_data <- tone_cells[arm %in% gradient_order]
tone_plot_data[, `:=`(
  arm = factor(arm, levels = gradient_order),
  ci_low = estimate - 1.96 * std_error,
  ci_high = estimate + 1.96 * std_error,
  value_label = sprintf("%.2f", estimate)
)]
path_plot_data <- arm_effects[
  sample == "Composite" & arm %in% gradient_order,
  .(arm, estimate = 100 * estimate, std_error = 100 * std_error)
]
path_plot_data[, `:=`(
  arm = factor(arm, levels = gradient_order),
  ci_low = estimate - 1.96 * std_error,
  ci_high = estimate + 1.96 * std_error,
  value_label = sprintf("%+.2f", estimate)
)]

gradient_scale <- scale_colour_manual(
  values = arm_colors,
  breaks = gradient_order,
  guide = "none"
)
gradient_x <- scale_x_discrete(
  name = NULL,
  labels = gradient_labels,
  drop = FALSE
)
tone_trend_label <- sprintf(
  "+%.3f tone p.p. per step toward hawkishness\n%s",
  tone_trend$estimate,
  format_plot_p(tone_trend$p_value)
)
composite_trend <- path_trends[sample == "Composite"]
path_trend_label <- sprintf(
  "+%.2f bp per step toward hawkishness\n%s\n1 bp = 0.01 p.p.",
  100 * composite_trend$estimate,
  format_plot_p(composite_trend$p_value)
)

tone_plot <- ggplot(tone_plot_data, aes(x = arm, y = estimate, group = 1)) +
  geom_line(colour = "#555555", linewidth = 0.7) +
  geom_errorbar(
    aes(ymin = ci_low, ymax = ci_high, colour = arm),
    width = 0.10,
    linewidth = 0.45
  ) +
  geom_point(aes(colour = arm), size = 2.6) +
  geom_text(
    aes(label = value_label),
    vjust = -1.45,
    size = 2.8,
    family = "serif",
    colour = "#222222"
  ) +
  gradient_scale +
  gradient_x +
  scale_y_continuous(
    name = paste0(
      "Tone measured in generated rewrite\n",
      "(hawk-minus-dove word share, p.p.)"
    ),
    labels = label_number(accuracy = 0.1),
    expand = expansion(mult = c(0.05, 0.16))
  ) +
  annotate(
    "text", x = 1, y = Inf, label = tone_trend_label,
    hjust = 0, vjust = 1.25, size = 2.35,
    family = "serif", lineheight = 0.95
  ) +
  theme_journal()

path_plot <- ggplot(path_plot_data, aes(x = arm, y = estimate, group = 1)) +
  geom_hline(yintercept = 0, colour = "#999999", linewidth = 0.35) +
  geom_line(colour = "#555555", linewidth = 0.7) +
  geom_errorbar(
    aes(ymin = ci_low, ymax = ci_high, colour = arm),
    width = 0.10,
    linewidth = 0.45
  ) +
  geom_point(aes(colour = arm), size = 2.6) +
  geom_text(
    aes(label = value_label),
    vjust = -1.45,
    size = 2.8,
    family = "serif",
    colour = "#222222"
  ) +
  gradient_scale +
  gradient_x +
  scale_y_continuous(
    name = paste0(
      "Persistent forecast gap vs. original-text path\n",
      "(bp; each arm uses its own lagged prior)"
    ),
    labels = label_number(accuracy = 0.5),
    expand = expansion(mult = c(0.08, 0.17))
  ) +
  annotate(
    "text", x = 1, y = Inf, label = path_trend_label,
    hjust = 0, vjust = 1.25, size = 2.35,
    family = "serif", lineheight = 0.95
  ) +
  theme_journal()

gradient_plot <- tone_plot + path_plot +
  plot_annotation(tag_levels = "A") &
  theme(
    plot.tag = element_text(face = "bold", family = "serif", size = 10),
    plot.tag.position = c(0.09, 0.99)
  )

pdf_path <- file.path(args$figure_dir, paste0(args$figure_stem, ".pdf"))
png_path <- file.path(args$qa_figure_dir, paste0(args$figure_stem, ".png"))
ggsave(
  pdf_path,
  plot = gradient_plot,
  device = grDevices::cairo_pdf,
  width = 7.1,
  height = 3.8,
  units = "in",
  bg = "white"
)
ggsave(
  png_path,
  plot = gradient_plot,
  device = ragg::agg_png,
  dpi = 220,
  width = 7.1,
  height = 3.8,
  units = "in",
  bg = "white"
)

paper_section_output <- NULL
if (publish_paper) {
  section_arm_order <- c(
    "hawk_high", "hawk_low", "neutral", "dove_low", "dove_high"
  )
  composite_effects <- arm_effects[sample == "Composite"]
  section_bp <- setNames(
    vapply(section_arm_order, function(arm_name) {
      100 * composite_effects[get("arm") == arm_name, estimate][[1]]
    }, numeric(1)),
    section_arm_order
  )
  section_trend <- path_trends[sample == "Composite"][1]
  format_section_p <- function(p_value) {
    if (is.na(p_value)) {
      return("p unavailable")
    }
    if (p_value < 0.001) {
      "$p<0.001$"
    } else {
      sprintf("$p=%.3f$", p_value)
    }
  }
  monotonicity_text <- if (all(diff(section_bp) <= 0)) {
    paste0(
      "The five composite means are monotone in the intended direction, ",
      "from high hawk to high dove. "
    )
  } else {
    paste0(
      "The five composite means are not monotone at every adjacent step, so the ",
      "linear coefficient is interpreted as an average path gradient rather than ",
      "as evidence that every neighboring intensity is separately ordered. "
    )
  }
  paper_section_output <- file.path(
    args$paper_table_dir,
    "rr_arm_specific_prior_dose_section.tex"
  )
  section_lines <- c(
    "\\subsubsection[Recursive arm-specific prior paths]{\\color{revisionred}Recursive arm-specific prior paths}",
    "\\label{subsec:arm-specific-prior-dose}",
    "{\\color{revisionred}",
    paste0(
      "Figure~\\ref{fig:arm-specific-prior-dose-gradient} and ",
      "Table~\\ref{tab:arm-specific-prior-dose-arms} report the complementary ",
      "recursive-path exercise. Unlike the common-prior design, only the initial ",
      "3.80 percent forecast is shared. From the second meeting onward, each ",
      "textual arm receives the forecast generated by that same arm in the ",
      "preceding meeting; the original text follows its own recursive reference ",
      "path. The comparison therefore captures accumulated synthetic state ",
      "dependence rather than the one-shot effect of the current communication."
    ),
    "\\par}",
    "\\begin{figure}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    paste0(
      "\\includegraphics[width=\\textwidth]{graficos/",
      args$figure_stem,
      ".pdf}"
    ),
    paste0(
      "\\caption{Generated-text tone and persistent synthetic forecast paths ",
      "under arm-specific recursive priors}"
    ),
    "\\label{fig:arm-specific-prior-dose-gradient}",
    "\\begin{minipage}{0.95\\textwidth}",
    "\\footnotesize",
    paste0(
      "\\textit{Notes:} Panel A reports the tone score measured in each ",
      "LLM-generated rewrite, not the tone of the published Copom minute. ",
      "Panel B reports the calibrated synthetic forecast-level gap relative to ",
      "the recursive original-text path. Every arm starts from 3.80 percent at ",
      "the first meeting and then uses only its own lagged forecast as its prior. ",
      "One basis point equals 0.01 percentage point. Whiskers are 95 percent ",
      "confidence intervals based on two-way clustered standard errors by Copom ",
      "meeting and recursive trajectory (regeneration replicate by textual arm)."
    ),
    "\\end{minipage}",
    "\\end{figure}",
    "{\\color{revisionred}",
    paste0(
      "Relative to the recursive original-text path, the composite forecast gaps ",
      "are $", sprintf("%+.2f", section_bp[["hawk_high"]]), "$ bp under high hawk, ",
      "$", sprintf("%+.2f", section_bp[["hawk_low"]]), "$ bp under low hawk, ",
      "$", sprintf("%+.2f", section_bp[["neutral"]]), "$ bp under neutral, ",
      "$", sprintf("%+.2f", section_bp[["dove_low"]]), "$ bp under low dove, and ",
      "$", sprintf("%+.2f", section_bp[["dove_high"]]), "$ bp under high dove. "
    ),
    paste0(
      "The linear path gradient is $",
      sprintf("%+.2f", 100 * section_trend$estimate),
      "$ bp per step toward hawkishness (two-way clustered standard error ",
      sprintf("%.2f", 100 * section_trend$std_error),
      "; ",
      format_section_p(section_trend$p_value),
      "). ",
      monotonicity_text,
      "Table~\\ref{tab:arm-specific-prior-dose-arms} reports the anchored and ",
      "unanchored components using the same specification."
    ),
    "\\par}",
    "\\input{tabelas/rr_arm_specific_prior_dose_arm_effects.tex}",
    "{\\color{revisionred}",
    paste0(
      "Because the prior is endogenous to earlier synthetic responses within each ",
      "path, these coefficients are persistent model-trajectory comparisons. They ",
      "are deliberately reported alongside, and not substituted for, the immediate ",
      "common-prior Prompt-IV estimand."
    ),
    "\\par}",
    "\\FloatBarrier"
  )
  writeLines(section_lines, paper_section_output, useBytes = TRUE)
}

summary <- list(
  run_id = args$run_id,
  tasks = unname(task_status$tasks),
  meetings = uniqueN(agent$meeting_number),
  draws = uniqueN(agent$corpus_iteration),
  roles = sort(unique(agent$source_role)),
  forecasters = sort(unique(agent$forecaster_type)),
  mixture_weights = list(
    anchored = anchored_weight,
    unanchored = unanchored_weight
  ),
  tone_trend = as.list(tone_trend[1]),
  path_trends = split(path_trends, path_trends$sample),
  arm_effects = split(arm_effects, arm_effects$sample),
  outputs = list(
    arm_effects_csv = arm_effects_path,
    trends_csv = path_trends_path,
    table_tex = table_output,
    paper_table_tex = paper_table_output,
    paper_section_tex = paper_section_output,
    figure_pdf = pdf_path,
    figure_png = png_path
  )
)
json_path <- file.path(
  args$output_dir,
  paste0("arm_specific_prior_dose_analysis_", args$stamp, ".json")
)
write_json(summary, json_path, auto_unbox = TRUE, pretty = TRUE, digits = NA)
cat(toJSON(summary, auto_unbox = TRUE, pretty = TRUE, digits = 8), "\n")
