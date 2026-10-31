suppressPackageStartupMessages({
  library(data.table)
  library(ggplot2)
  library(scales)
})

root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
source_dir <- file.path(root, "outputs", "tables")
table_dir <- file.path(root, "results", "tables")
figure_dir <- file.path(root, "results", "figures")
dir.create(table_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

read_result <- function(filename) {
  path <- file.path(source_dir, filename)
  if (!file.exists(path)) stop("Missing reporting input: ", path)
  fread(path)
}

write_tex <- function(filename, lines) {
  writeLines(lines, file.path(table_dir, filename), useBytes = TRUE)
}

fmt <- function(x, digits = 4) {
  ifelse(is.na(x), "", formatC(x, format = "f", digits = digits))
}

stars <- function(p) {
  ifelse(is.na(p), "", ifelse(p < 0.01, "\\sym{***}", ifelse(p < 0.05, "\\sym{**}", ifelse(p < 0.10, "\\sym{*}", ""))))
}

coef_cell <- function(estimate, p, digits = 4) paste0(fmt(estimate, digits), stars(p))
se_cell <- function(se, digits = 4) paste0("(", fmt(se, digits), ")")
ci_cell <- function(lo, hi, digits = 3) paste0("[", fmt(lo, digits), ", ", fmt(hi, digits), "]")
mc <- function(x) paste0("\\mc{", x, "}")
p_cell <- function(p, digits = 3) {
  if (length(p) != 1L || !is.finite(p)) return("--")
  if (p < 0.001) return("$<0.001$")
  paste0("$", fmt(p, digits), "$")
}
tex_percent <- function(x, accuracy = 0.1) {
  gsub("%", "\\%", percent(x, accuracy = accuracy), fixed = TRUE)
}

table_header <- function(caption, label, size = "scriptsize") {
  c(
    "\\begin{table}[htbp]",
    "\\color{revisionred}",
    "\\centering",
    "\\begin{threeparttable}",
    paste0("\\caption{", caption, "}"),
    paste0("\\label{", label, "}"),
    paste0("\\begin{", size, "}")
  )
}

table_footer <- function(notes, size = "scriptsize") {
  c(
    "\\begin{tablenotes}[flushleft]",
    "\\scriptsize",
    paste0("\\item \\textit{Notes:} ", notes),
    "\\end{tablenotes}",
    paste0("\\end{", size, "}"),
    "\\end{threeparttable}",
    "\\end{table}"
  )
}

# Constrained calibration: keep this separate from the baseline regression table.
d6 <- read_result("referee_d6_constrained_calibration_20260715.csv")
d6_h <- read_result("referee_d6_hierarchical_dirichlet_20260715.csv")
d6_eb <- read_result("referee_d6_empirical_bayes_20260715.csv")

get_spec <- function(pattern) d6[grepl(pattern, specification, fixed = TRUE)][1]
ols_nowc <- get_spec("Unrestricted OLS")
convex <- get_spec("Convex QP")
h_a <- d6_h[parameter == "Anchored weight"][1]
h_u <- d6_h[parameter == "Unanchored weight"][1]
eb <- d6_eb[1]

calibration_lines <- c(
  table_header("Calibration weights after removing word count", "tab:calibration-restrictions"),
  "\\setlength{\\tabcolsep}{3.8pt}",
  "\\begin{tabular}{ldddd}",
  "\\toprule",
  paste0(" & ", paste(mc(paste0("(", 1:4, ")")), collapse = " & "), " \\\\"),
  paste0(" & ", paste(mc(c("OLS", "Convex shares", "Bayesian shares", "Ridge shrinkage")), collapse = " & "), " \\\\"),
  "\\midrule",
  paste0("Anchored weight & ", fmt(ols_nowc$anchored_weight), " & ", fmt(ifelse(abs(convex$anchored_weight) < 5e-8, 0, convex$anchored_weight)), " & ", fmt(h_a$mean), " & ", fmt(eb$anchored_weight), " \\\\"),
  paste0("95\\% interval & ", paste(mc(c(ci_cell(ols_nowc$anchored_ci_low, ols_nowc$anchored_ci_high, 4), ci_cell(convex$anchored_ci_low, convex$anchored_ci_high, 4), ci_cell(h_a$q025, h_a$q975, 4), ci_cell(eb$anchored_ci_low, eb$anchored_ci_high, 4))), collapse = " & "), " \\\\"),
  "\\addlinespace",
  paste0("Unanchored weight & ", fmt(ols_nowc$unanchored_weight), " & ", fmt(convex$unanchored_weight), " & ", fmt(h_u$mean), " & ", fmt(eb$unanchored_weight), " \\\\"),
  paste0("95\\% interval & ", paste(mc(c(ci_cell(ols_nowc$unanchored_ci_low, ols_nowc$unanchored_ci_high, 4), ci_cell(convex$unanchored_ci_low, convex$unanchored_ci_high, 4), ci_cell(h_u$q025, h_u$q975, 4), ci_cell(eb$unanchored_ci_low, eb$unanchored_ci_high, 4))), collapse = " & "), " \\\\"),
  "\\midrule",
  paste0("Weights sum to one & ", paste(mc(c("No", "Yes", "Yes", "Yes")), collapse = " & "), " \\\\"),
  paste0("Weights restricted to $[0,1]$ & ", paste(mc(c("No", "Yes", "Yes", "No")), collapse = " & "), " \\\\"),
  paste0("Word count included & ", paste(mc(rep("No", 4)), collapse = " & "), " \\\\"),
  paste0("Observations & ", paste(mc(rep("71", 4)), collapse = " & "), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "The outcome is the median twelve-month-ahead Focus IPCA expectation.",
    "Every specification includes a constant and the three IBC-Br controls.",
    "Column (1) reports OLS estimates and HC2 confidence intervals.",
    "Column (2) restricts both weights to the unit interval and their sum to one; its confidence intervals use percentiles from 1,999 meeting bootstrap replications.",
    "Column (3) reports posterior means and 95 percent credible intervals under a symmetric Dirichlet prior with concentration estimated from the data.",
    "Column (4) reports empirical Bayes ridge estimates centered on equal weights; the weights sum to one and may fall outside the unit interval.",
    "Its confidence intervals use percentiles from 1,999 meeting bootstrap replications."
  ))
)
write_tex("rr_calibration_restrictions.tex", calibration_lines)

# Pre-meeting orthogonality: the outcome is the realized hawk-minus-dove tone gap.
d2 <- read_result("referee_d2_tone_gap_orthogonality_20260715.csv")
d2_joint <- read_result("referee_d2_joint_orthogonality_20260715.csv")[1]
d2_terms <- c(
  "z_ipca15_change_pre", "z_ibc_change_pre", "z_brl_return_5d_pre",
  "z_focus_drift_7d_pre", "z_selic_surprise"
)
d2_labels <- c(
  "IPCA-15 change", "IBC-Br growth", "BRL return, five days",
  "Focus drift, seven days", "Selic surprise"
)
d2_rows <- d2[match(d2_terms, term)]
if (nrow(d2_rows) != length(d2_terms) || anyNA(d2_rows$term)) {
  stop("D2 orthogonality table is incomplete")
}

orthogonality_lines <- c(
  table_header("Pre-meeting covariates and the realized treatment-induced tone gap", "tab:preassignment-orthogonality", "footnotesize"),
  "\\begin{tabular}{ld}",
  "\\toprule",
  paste0(" & ", mc("Hawk-minus-dove tone gap"), " \\\\"),
  "\\midrule",
  unlist(Map(function(label, estimate, se, p) {
    c(
      paste0(label, " & ", coef_cell(estimate, p), " \\\\"),
      paste0(" & ", se_cell(se), " \\\\")
    )
  }, d2_labels, d2_rows$estimate, d2_rows$std_error, d2_rows$p_value)),
  "\\midrule",
  paste0("Joint $F$ statistic & ", mc(fmt(d2_joint$statistic, 3)), " \\\\"),
  paste0("Joint-test $p$ value & ", mc(fmt(d2_joint$p_value, 3)), " \\\\"),
  paste0("Meetings & ", mc(d2_joint$observations), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "The dependent variable is the meeting-level mean realized hawkish-minus-dovish lexicon-tone gap across the 25 full-report regenerations.",
    "Continuous pre-meeting covariates are standardized. Assignment itself is exactly balanced because every meeting-regeneration pair contains both arms.",
    "The joint null that all five slopes are zero is not rejected ($p=0.368$), although the IBC-Br coefficient is individually significant at the five-percent level.",
    "Heteroskedasticity-robust standard errors are in parentheses. * $p<0.10$, ** $p<0.05$, *** $p<0.01$."
  ), "footnotesize")
)
write_tex("rr_preassignment_orthogonality.tex", orthogonality_lines)

# Generator uncertainty: keep text tone, forecast levels, and revisions in separate tables.
d5_forecasters <- read_result("referee_d5_cross_draw_forecasters_20260715.csv")
d5_composite <- read_result("referee_d5_cross_draw_composite_20260715.csv")
d5_value <- function(role_filter, forecaster_filter, field) {
  selected <- d5_forecasters[source_role == role_filter & forecaster_type == forecaster_filter]
  if (nrow(selected) != 1L) stop("D5 forecaster selector must return exactly one row")
  selected[[field]][1]
}
d5_comp_value <- function(role_filter, field) {
  selected <- d5_composite[source_role == role_filter]
  if (nrow(selected) != 1L) stop("D5 composite selector must return exactly one row")
  selected[[field]][1]
}

crossdraw_tone_lines <- c(
  table_header("Generator variation in realized text tone", "tab:crossdraw-tone", "footnotesize"),
  "\\begin{tabular}{lcc}",
  "\\toprule",
  "Statistic & Dovish rewrite & Hawkish rewrite \\\\",
  "\\midrule",
  paste0("Mean tone & ", fmt(d5_comp_value("dove", "mean_tone")), " & ", fmt(d5_comp_value("hawk", "mean_tone")), " \\\\"),
  paste0("Between-regeneration SD of mean tone & ", fmt(d5_comp_value("dove", "between_draw_sd_tone")), " & ", fmt(d5_comp_value("hawk", "between_draw_sd_tone")), " \\\\"),
  paste0("Mean within-cell SD & ", fmt(d5_comp_value("dove", "mean_within_cell_sd_tone")), " & ", fmt(d5_comp_value("hawk", "mean_within_cell_sd_tone")), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer("Tone is the hawkish-minus-dovish lexicon share in percentage points. Between-regeneration SD measures dispersion of the regeneration-specific cross-meeting mean; within-cell SD is first computed across regenerations inside each meeting arm and then averaged across meetings.", "footnotesize")
)
write_tex("rr_crossdraw_tone.tex", crossdraw_tone_lines)

forecast_columns <- list(
  c("dove", "anchored"), c("dove", "unanchored"),
  c("hawk", "anchored"), c("hawk", "unanchored")
)
forecast_labels <- c("Dove anchored", "Dove unanchored", "Hawk anchored", "Hawk unanchored")
forecast_metric <- function(field) vapply(forecast_columns, function(key) d5_value(key[[1]], key[[2]], field), numeric(1))

crossdraw_level_lines <- c(
  table_header("Generator variation in synthetic forecast levels", "tab:crossdraw-levels"),
  "\\setlength{\\tabcolsep}{3.2pt}",
  "\\begin{tabular}{lcccc}",
  "\\toprule",
  paste0("Statistic & ", paste(forecast_labels, collapse = " & "), " \\\\"),
  "\\midrule",
  paste0("Mean forecast level & ", paste(fmt(forecast_metric("mean_forecast_level")), collapse = " & "), " \\\\"),
  paste0("Between-regeneration SD & ", paste(fmt(forecast_metric("between_draw_sd_forecast_level")), collapse = " & "), " \\\\"),
  paste0("Mean within-cell SD & ", paste(fmt(forecast_metric("mean_within_cell_sd_forecast_level")), collapse = " & "), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer("Forecast levels are twelve-month-ahead IPCA expectations in percentage points. Between-regeneration SD measures dispersion of regeneration-specific cross-meeting means. Mean within-cell SD measures stochastic forecast dispersion across the 25 full-report regenerations within the same meeting, arm, and forecaster profile.")
)
write_tex("rr_crossdraw_forecast_levels.tex", crossdraw_level_lines)

revision_columns <- c("Dove A", "Dove U", "Hawk A", "Hawk U", "Dove comp.", "Hawk comp.")
revision_mean <- c(forecast_metric("mean_forecast_revision"), d5_comp_value("dove", "mean_forecast_revision"), d5_comp_value("hawk", "mean_forecast_revision"))
revision_between <- c(forecast_metric("between_draw_sd_forecast_revision"), d5_comp_value("dove", "between_draw_sd_forecast_revision"), d5_comp_value("hawk", "between_draw_sd_forecast_revision"))
revision_within <- c(forecast_metric("mean_within_cell_sd_forecast_revision"), d5_comp_value("dove", "mean_within_cell_sd_forecast_revision"), d5_comp_value("hawk", "mean_within_cell_sd_forecast_revision"))
crossdraw_revision_lines <- c(
  table_header("Generator variation in synthetic forecast revisions", "tab:crossdraw-revisions"),
  "\\setlength{\\tabcolsep}{2.1pt}",
  "\\begin{tabular}{lcccccc}",
  "\\toprule",
  paste0("Statistic & ", paste(revision_columns, collapse = " & "), " \\\\"),
  "\\midrule",
  paste0("Mean revision & ", paste(fmt(revision_mean), collapse = " & "), " \\\\"),
  paste0("Between-regeneration SD & ", paste(fmt(revision_between), collapse = " & "), " \\\\"),
  paste0("Mean within-cell SD & ", paste(fmt(revision_within), collapse = " & "), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer("Revisions are new minus pre-treatment twelve-month-ahead IPCA expectations in percentage points. A and U denote the anchored and unanchored profiles; comp. denotes their manuscript calibration. The table reports generator dispersion, not regression standard errors.")
)
write_tex("rr_crossdraw_forecast_revisions.tex", crossdraw_revision_lines)

# Regime stability: report every object requested by the editor, with four
# columns per panel so that estimates and uncertainty remain legible.
d7 <- read_result("referee_d7_regime_estimates_20260715.csv")
calendar_order <- c("Pre-Campos Neto", "Campos Neto onward", "Pre-2021", "2021 onward")
state_order <- c(
  "Below-median realized inflation", "Above-median realized inflation",
  "Markov state 1", "Markov state 2"
)

regime_panel <- function(panel_title, regime_order, regime_labels) {
  rows <- d7[match(regime_order, regime)]
  c(
    paste0("\\multicolumn{5}{l}{\\textit{", panel_title, "}} \\\\"),
    "\\addlinespace",
    paste0(" & ", paste(mc(regime_labels), collapse = " & "), " \\\\"),
    "\\midrule",
    paste0("Anchored weight $\\widehat\\phi_A$ & ", paste(mapply(coef_cell, rows$anchored_weight, rows$anchored_weight_p), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(rows$anchored_weight_se_hc2, se_cell, character(1)), collapse = " & "), " \\\\"),
    paste0("Unanchored weight $\\widehat\\phi_U$ & ", paste(mapply(coef_cell, rows$unanchored_weight, rows$unanchored_weight_p), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(rows$unanchored_weight_se_hc2, se_cell, character(1)), collapse = " & "), " \\\\"),
    "\\addlinespace",
    paste0("Prompt-IV $\\widehat\\beta^{IV}$ & ", paste(mapply(coef_cell, rows$prompt_iv, rows$prompt_iv_p), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(rows$prompt_iv_se, se_cell, character(1)), collapse = " & "), " \\\\"),
    paste0("First-stage $F$ & ", paste(fmt(rows$first_stage_f, 1), collapse = " & "), " \\\\"),
    "\\addlinespace",
    paste0("Focus tone $\\widehat\\beta^{\\mathrm{Focus}}$ & ", paste(mapply(coef_cell, rows$focus_tone, rows$focus_tone_p), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(rows$focus_tone_se_hac, se_cell, character(1)), collapse = " & "), " \\\\"),
    paste0("Focus persistence $\\widehat\\rho^{\\mathrm{Focus}}$ & ", paste(mapply(coef_cell, rows$focus_rho, rows$focus_rho_p), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(rows$focus_rho_se_hac, se_cell, character(1)), collapse = " & "), " \\\\"),
    paste0("Meetings & ", paste(mc(rows$meetings), collapse = " & "), " \\\\")
  )
}

calendar_ranges <- d7[match(calendar_order, regime)]
calendar_note <- paste(
  paste0(
    "Pre-Campos covers ", format(as.Date(calendar_ranges$start_date[1]), "%Y-%m-%d"),
    "--", format(as.Date(calendar_ranges$end_date[1]), "%Y-%m-%d"),
    "; Campos+ covers ", format(as.Date(calendar_ranges$start_date[2]), "%Y-%m-%d"),
    "--", format(as.Date(calendar_ranges$end_date[2]), "%Y-%m-%d"), "."
  ),
  paste0(
    "Pre-2021 covers ", format(as.Date(calendar_ranges$start_date[3]), "%Y-%m-%d"),
    "--", format(as.Date(calendar_ranges$end_date[3]), "%Y-%m-%d"),
    "; 2021+ covers ", format(as.Date(calendar_ranges$start_date[4]), "%Y-%m-%d"),
    "--", format(as.Date(calendar_ranges$end_date[4]), "%Y-%m-%d"), "."
  )
)

regime_lines <- c(
  table_header("Calibration, Prompt-IV, and Focus persistence across regimes", "tab:regime-promptiv"),
  "\\setlength{\\tabcolsep}{4.0pt}",
  "\\begin{tabular}{ldddd}",
  "\\toprule",
  regime_panel("Panel A. Calendar partitions", calendar_order, c("Pre-Campos", "Campos+", "Pre-2021", "2021+")),
  "\\midrule",
  regime_panel("Panel B. Inflation and latent-state partitions", state_order, c("At/below-median IPCA", "Above-median IPCA", "State 1", "State 2")),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "Each column re-estimates three descriptive objects on the named meeting partition.",
    "Calibration weights come from the unrestricted Focus projection and have HC2 robust standard errors.",
    "Prompt-IV is pair-fixed-effect 2SLS with standard errors clustered by Copom meeting.",
    "The Focus tone coefficient and lag persistence come from an observational AR(1) projection with Newey--West HAC standard errors using up to two lags.",
    calendar_note,
    "The inflation split uses the latest realized twelve-month IPCA rate at each meeting: at or below versus above its sample median.",
    "Markov states are maximum-smoothed-probability classifications from a two-state Focus-on-tone AR(1) projection with switching coefficients and variance; state labels are nominal.",
    "The 2015 reference date and the 2011--2012 Bonomo episode are outside the November 2016--September 2025 sample; the inflation and Markov partitions are noncontiguous.",
    "Weights are predictive coefficients, not population shares. * $p<0.10$, ** $p<0.05$, *** $p<0.01$."
  ))
)
write_tex("rr_regime_promptiv.tex", regime_lines)

# Focus Top-5 calibration, one benchmark outcome with horizons in columns.
d9_top <- read_result("referee_d9_top5_calibration_20260715.csv")
top_horizons <- c("Top-5 short horizon", "Top-5 medium horizon", "Top-5 long horizon")
top_label <- c("Short", "Medium", "Long")
top_value <- function(benchmark_filter, term_filter, field) {
  selected <- d9_top[benchmark == benchmark_filter & term == term_filter]
  if (nrow(selected) != 1L) {
    stop("Top-5 selector must return exactly one row: ", benchmark_filter, " / ", term_filter)
  }
  selected[[field]][1]
}

top_lines <- c(
  table_header("Calibration against Focus Top-5 benchmarks", "tab:top5-calibration"),
  "\\begin{tabular}{lddd}",
  "\\toprule",
  paste0(" & ", paste(mc(paste0("(", 1:3, ")")), collapse = " & "), " \\\\"),
  paste0(" & ", paste(mc(top_label), collapse = " & "), " \\\\"),
  "\\midrule",
    paste0("Anchored forecast & ", paste(vapply(top_horizons, function(h) coef_cell(top_value(h, "forecast_anchored_gpt55", "estimate"), top_value(h, "forecast_anchored_gpt55", "p_value")), character(1)), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(top_horizons, function(h) se_cell(top_value(h, "forecast_anchored_gpt55", "std_error")), character(1)), collapse = " & "), " \\\\"),
    paste0("Unanchored forecast & ", paste(vapply(top_horizons, function(h) coef_cell(top_value(h, "forecast_unanchored_gpt55", "estimate"), top_value(h, "forecast_unanchored_gpt55", "p_value")), character(1)), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(top_horizons, function(h) se_cell(top_value(h, "forecast_unanchored_gpt55", "std_error")), character(1)), collapse = " & "), " \\\\"),
  "\\midrule",
    paste0("Observations & ", paste(mc(vapply(top_horizons, function(h) top_value(h, "forecast_unanchored_gpt55", "observations"), numeric(1))), collapse = " & "), " \\\\"),
    paste0("$R^2$ & ", paste(mc(vapply(top_horizons, function(h) fmt(top_value(h, "forecast_unanchored_gpt55", "r_squared"), 3), character(1))), collapse = " & "), " \\\\"),
  paste0("Activity controls & ", paste(mc(rep("Yes", 3)), collapse = " & "), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "The dependent variables are the official BCB Focus Top-5 IPCA benchmarks at short, medium, and long horizons.",
    "Each column regresses one benchmark on the two original-minute synthetic forecasts and the IBC-Br activity controls used in the baseline calibration.",
    "Heteroskedasticity-robust standard errors are in parentheses. Institution-level respondent panels are confidential and are not claimed here.",
    "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
  ))
)
write_tex("rr_top5_calibration.tex", top_lines)

# Distribution and moment diagnostics remain descriptive and separate from regressions.
d9_dist <- read_result("referee_d9_distribution_summary_20260715.csv")[1]
d8_loo <- read_result("referee_d8_loo_summary_20260715.csv")[1]
d8_metrics <- read_result("referee_d8_forecast_metrics_20260715.csv")
d8_composite <- d8_metrics[model == "composite"][1]
d8_ar1 <- d8_metrics[model == "focus_ar1"][1]
d8_anchored <- d8_metrics[model == "anchored_agent"][1]

focus_summary_lines <- c(
  table_header("Calibration fit, recursive forecasts, and dispersion", "tab:focus-calibration-summary", "footnotesize"),
  "\\begin{tabular}{lcc}",
  "\\toprule",
  "Assessment & Synthetic estimate & Focus comparison \\\\",
  "\\midrule",
  "Full sample $R^2$ & 0.805 &  \\\\",
  paste0("Leave one meeting out $R^2$ & ", fmt(d8_loo$r2_cv, 3), " &  \\\\"),
  paste0("Leave one meeting out RMSE (pp) & ", fmt(d8_loo$rmse, 3), " &  \\\\"),
  "\\addlinespace",
  paste0("Expanding window RMSE, composite (pp) & ", fmt(d8_composite$rmse, 3), " & ", fmt(d8_ar1$rmse, 3), " \\\\"),
  paste0("Expanding window RMSE, anchored profile (pp) & ", fmt(d8_anchored$rmse, 3), " & ", fmt(d8_ar1$rmse, 3), " \\\\"),
  "\\addlinespace",
  paste0("Mean cross-sectional SD (pp) & ", fmt(d9_dist$mean_synthetic_sd, 3), " & ", fmt(d9_dist$mean_focus_sd, 3), " \\\\"),
  paste0("Synthetic-to-Focus SD ratio & ", fmt(d9_dist$mean_synthetic_to_focus_sd_ratio, 3), " & 1.000 \\\\"),
  paste0("Correlation of meeting dispersions & ", fmt(d9_dist$correlation_date_level_dispersion, 3), " &  \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "The Focus comparison for expanding-window RMSE is a recursively estimated Focus AR(1).",
    "The expanding window contains 47 forecasts after an initial 24 meetings.",
    "Leave-one-meeting-out statistics use all 71 meetings.",
    "Focus dispersion is the standard deviation across survey respondents. Synthetic dispersion is the standard deviation across the two profiles."
  ), "footnotesize")
)
write_tex("rr_focus_calibration_summary.tex", focus_summary_lines)

dist_lines <- c(
  table_header("Focus dispersion and the synthetic cross section", "tab:focus-dispersion", "footnotesize"),
  "\\begin{tabular}{ld}",
  "\\toprule",
  "Statistic & Estimate \\\\",
  "\\midrule",
  paste0("Mean Focus respondents per meeting & ", fmt(d9_dist$mean_focus_respondents, 1), " \\\\"),
  paste0("Mean Focus cross-sectional SD (p.p.) & ", fmt(d9_dist$mean_focus_sd, 3), " \\\\"),
  paste0("Mean normalized Focus dispersion & ", fmt(d9_dist$mean_normalized_focus_dispersion, 3), " \\\\"),
  paste0("Mean synthetic two-profile SD (p.p.) & ", fmt(d9_dist$mean_synthetic_sd, 3), " \\\\"),
  paste0("Synthetic-to-Focus SD ratio & ", fmt(d9_dist$mean_synthetic_to_focus_sd_ratio, 3), " \\\\"),
  paste0("Correlation of meeting-level dispersions & ", fmt(d9_dist$correlation_date_level_dispersion, 3), " \\\\"),
  paste0("Meetings & ", d9_dist$meetings, " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "Focus dispersion uses the BCB aggregate distribution around each Copom meeting.",
    "Synthetic dispersion is the cross-sectional standard deviation of the two original-minute forecaster profiles and is therefore a deliberately sparse benchmark.",
    "The ratio shows that two profiles reproduce less than half of the observed survey dispersion."
  ), "footnotesize")
)
write_tex("rr_focus_dispersion.tex", dist_lines)

d12 <- read_result("referee_d12_moment_battery_20260715.csv")
moment_lines <- c(
  table_header("Moment validation of the synthetic calibration", "tab:moment-battery", "footnotesize"),
  "\\begin{tabular}{lcc}",
  "\\toprule",
  "Moment & Estimate & HAC SE \\\\",
  "\\midrule",
  paste0(vapply(seq_len(nrow(d12)), function(i) paste0(d12$statistic[i], " & ", fmt(d12$estimate[i], 3), " & ", fmt(d12$std_error[i], 3), " \\\\"), character(1))),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "Forecast-error and residual persistence use AR(1) regressions with HAC standard errors.",
    "Excess kurtosis and dispersion are descriptive moments. Leave-one-out $R^2$ re-estimates the calibration after omitting each meeting.",
    "The table is a moment-matching diagnostic rather than a test that the synthetic panel reproduces the full Focus distribution."
  ), "footnotesize")
)
write_tex("rr_moment_battery.tex", moment_lines)

# Macro correlates of the tone in the published Copom minutes.
d22_macro <- read_result("referee_d22_macro_correlates_20260715.csv")
d22_terms <- c(
  "z_selic_target_pre", "z_ibc_change_pre", "z_brl_volatility_20d_pre",
  "z_ipca15_change_pre", "z_focus_drift_7d_pre", "post_campos_neto",
  "post_2021", "lula_fiscal_regime"
)
d22_labels <- c(
  "Selic target before meeting", "IBC-Br growth", "BRL volatility, 20 days",
  "IPCA-15 change", "Focus drift, seven days", "Campos Neto period",
  "2021 onward", "2023 fiscal regime"
)
d22_rows <- d22_macro[match(d22_terms, term)]
if (nrow(d22_rows) != length(d22_terms) || anyNA(d22_rows$term)) {
  stop("D22 macro-correlates table is incomplete")
}

macro_lines <- c(
  table_header("Macro correlates of tone in the published Copom minutes", "tab:macro-tone-correlates", "footnotesize"),
  "\\begin{tabular}{lc}",
  "\\toprule",
  paste0(" & ", mc("Original-minute tone"), " \\\\"),
  "\\midrule",
  unlist(Map(function(label, estimate, se, p) {
    c(
      paste0(label, " & ", coef_cell(estimate, p), " \\\\"),
      paste0(" & ", se_cell(se), " \\\\")
    )
  }, d22_labels, d22_rows$estimate, d22_rows$std_error, d22_rows$p_value)),
  "\\midrule",
  paste0("Observations & ", mc(d22_rows$observations[[1]]), " \\\\"),
  paste0("$R^2$ & ", mc(fmt(d22_rows$r_squared[[1]], 3)), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "The dependent variable is lexicon tone in the unmodified Copom minutes.",
    "Continuous covariates are standardized and measured before each meeting; period indicators are unstandardized.",
    "HC2 heteroskedasticity-robust standard errors are in parentheses. The table is descriptive and does not give the observed text a causal interpretation.",
    "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
  ), "footnotesize")
)
write_tex("rr_macro_tone_correlates.tex", macro_lines)

# Branch-McGough-Zhu analogue.
d10_order <- read_result("referee_d10_sunspot_order_bootstrap_20260715.csv")
sun_order <- c("anchored", "unanchored", "equal_weight", "hierarchical_simplex")
s10 <- d10_order[match(sun_order, synthetic_aggregate)]
sun_labels <- c("Anchored", "Unanchored", "Equal weight", "Bayesian shares")

sunspot_lines <- c(
  table_header("Tone-as-sunspot variance-ordering diagnostic", "tab:sunspot-ordering"),
  "\\setlength{\\tabcolsep}{3.6pt}",
  "\\begin{tabular}{ldddd}",
  "\\toprule",
  paste0(" & ", paste(sun_labels, collapse = " & "), " \\\\"),
  "\\midrule",
  paste0("Quasi-REE proxy variance & ", paste(fmt(s10$quasi_ree_variance, 4), collapse = " & "), " \\\\"),
  paste0("Assigned-tone variance & ", paste(fmt(s10$tone_variance, 4), collapse = " & "), " \\\\"),
  paste0("Neutral-rewrite proxy variance & ", paste(fmt(s10$fundamentals_variance, 4), collapse = " & "), " \\\\"),
  "\\midrule",
  paste0("BMZ ordering in point estimates & ", paste(ifelse(s10$bmz_order_point, "Yes", "No"), collapse = " & "), " \\\\"),
  paste0("Bootstrap share with BMZ ordering & ", paste(tex_percent(s10$bmz_order_bootstrap_share), collapse = " & "), " \\\\"),
  paste0("Bootstrap share with reverse ordering & ", paste(tex_percent(s10$reverse_order_bootstrap_share), collapse = " & "), " \\\\"),
  "Bootstrap draws & 1,999 & 1,999 & 1,999 & 1,999 \\\\",
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "This is an empirical laboratory analogue of the Branch--McGough--Zhu variance ordering, not a structural restricted-perceptions-equilibrium estimate.",
    "Entries are variances of meeting-level mean forecast revisions, in squared percentage points; regeneration replicates are averaged within meeting-arm-profile cells before aggregation.",
    "The quasi-REE proxy is the original-minute response, assigned tone uses the hawk and dove arms, and the neutral-rewrite proxy imperfectly approximates the requested fundamentals-only benchmark.",
    "The unanchored, Bayesian share, and equal-weight aggregates reject the BMZ ordering in point estimates; the exact paired assignment also has no serial assignment state and lies outside the diagnostic existence region."
  ))
)
write_tex("rr_sunspot_ordering.tex", sunspot_lines)

# Bayesian model tournaments are kept in two separate tables.
d11 <- read_result("referee_d11_posterior_tournament_20260715.csv")
write_tournament <- function(panel_name, filename, caption, label, model_order, model_labels, extra_note = NULL) {
  z <- d11[panel == panel_name][match(model_order, model)]
  lines <- c(
    table_header(caption, label),
    "\\setlength{\\tabcolsep}{4.2pt}",
    "\\begin{tabular}{lccc}",
    "\\toprule",
    "Model & Log marginal likelihood & Posterior probability & $2\\log BF$ vs. best \\\\",
    "\\midrule",
    vapply(seq_len(nrow(z)), function(i) paste0(model_labels[i], " & ", fmt(z$log_marginal_likelihood[i], 2), " & ", fmt(z$posterior_model_probability[i], 3), " & ", fmt(z$two_log_bf_best_over_model[i], 2), " \\\\"), character(1)),
    "\\bottomrule",
    "\\end{tabular}",
    table_footer(paste(c(
      "Models use proper standard-normal priors on standardized predictors and a half-$t$ residual-scale prior.",
      "Posterior model probabilities are normalized within the displayed tournament under equal prior model probabilities.",
      "Larger $2\\log BF$ values indicate evidence against the row model relative to the best model.",
      extra_note
    ), collapse = " "))
  )
  write_tex(filename, lines)
}

write_tournament(
  "Focus projection",
  "rr_posterior_tournament_focus.tex",
  "Bayesian model tournament for the Focus projection",
  "tab:tournament-focus",
  c("No tone, AR(1)", "Tone and AR(1)", "Tone and AR(2)", "Tone, AR(1), and activity controls", "Tone, no persistence"),
  c("No tone, AR(1)", "Tone and AR(1)", "Tone and AR(2)", "Tone, AR(1), activity", "Tone, no persistence")
)

write_tournament(
  "Synthetic calibration",
  "rr_posterior_tournament_calibration.tex",
  "Bayesian model tournament for the synthetic calibration",
  "tab:tournament-calibration",
  c("Unanchored agent plus fundamentals", "Unconstrained two-agent projection", "Hierarchical Dirichlet constrained projection", "Equal-weight constrained projection", "Anchored agent plus fundamentals", "Fundamentals only"),
  c("Unanchored plus fundamentals", "Unconstrained two-agent", "Bayesian share model", "Equal weight", "Anchored plus fundamentals", "Fundamentals only"),
  "All synthetic-calibration candidates omit original-minute word count so that the tournament compares agent restrictions on a common predictor set; the unrestricted candidate here is therefore not the paper's full baseline calibration."
)

# Topic preservation.
d15 <- read_result("referee_d15_topic_similarity_20260715.csv")
topic_roles <- c("dove_high", "dove_low", "neutral", "hawk_low", "hawk_high")
t15 <- d15[match(topic_roles, source_role)]
role_labels <- c("High dove", "Low dove", "Neutral", "Low hawk", "High hawk")

topic_lines <- c(
  table_header("Structural-topic similarity to the original Copom minutes", "tab:topic-preservation"),
  "\\begin{tabular}{lccc}",
  "\\toprule",
  "Generated role & Topic cosine & Jensen--Shannon distance & Meetings \\\\",
  "\\midrule",
  vapply(seq_along(topic_roles), function(i) paste0(role_labels[i], " & ", fmt(t15$mean_cosine_similarity[i], 5), " & ", fmt(t15$mean_jensen_shannon[i], 5), " & ", t15$meetings[i], " \\\\"), character(1)),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "Topic proportions come from an eight-topic structural topic model estimated on 1,846 Portuguese documents after deduplicating the original minutes.",
    "Each generated arm is compared with the original minute from the same meeting.",
    "Similarity is extremely high in magnitude, but a joint MANOVA rejects exact invariance of the seven non-tone topic shares ($p<0.001$).",
    "The correct conclusion is near-preservation with small detectable shifts, not that tone is the only changed semantic dimension."
  ))
)
write_tex("rr_topic_preservation.tex", topic_lines)

# Embedding and token preservation for the hawkish and dovish rewrites.
d16_pres <- read_result("referee_d16_embedding_preservation_20260715.csv")
pres_metrics <- c("document_cosine_similarity", "symmetric_sentence_similarity", "subword_jensen_shannon", "subword_count_ratio", "sentence_count_ratio")
pres_labels <- c("Document cosine similarity", "Symmetric sentence similarity", "Subword Jensen--Shannon distance", "Subword-count ratio", "Sentence-count ratio")
pres_value <- function(role_filter, metric_filter, field = "estimate") {
  selected <- d16_pres[
    run_id == "v10_referee_d5_25draws_20260620" &
      source_role == role_filter &
      metric == metric_filter
  ]
  if (nrow(selected) != 1L) {
    stop("Embedding selector must return exactly one row: ", role_filter, " / ", metric_filter)
  }
  selected[[field]][1]
}

embedding_lines <- c(
  table_header("Embedding and token similarity in the hawkish and dovish rewrites", "tab:embedding-preservation"),
  "\\begin{tabular}{lcc}",
  "\\toprule",
  "Metric relative to original & Dovish rewrite & Hawkish rewrite \\\\",
  "\\midrule",
  vapply(seq_along(pres_metrics), function(i) paste0(pres_labels[i], " & ", fmt(pres_value("dove", pres_metrics[i]), 4), " & ", fmt(pres_value("hawk", pres_metrics[i]), 4), " \\\\"), character(1)),
  "\\midrule",
  "Rewrite observations & 1,775 & 1,775 \\\\",
  "Meeting clusters & 71 & 71 \\\\",
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "Each column contains 1,775 generated minutes: 25 versions of each of 71 source minutes.",
    "We compare every generated minute with its source minute.",
    "Document and sentence similarities use the multilingual paraphrase-MiniLM-L12-v2 sentence transformer model.",
    "Subword measures use the same tokenizer. Count ratios divide the rewrite count by the source-minute count."
  ))
)
write_tex("rr_embedding_preservation.tex", embedding_lines)

# Regression-format alternative-tone table for the binary IV.
d16_iv <- read_result("referee_d16_alternative_tone_iv_20260715.csv")
binary_measures <- c("lexicon_tone_pp", "embedding_tone_loo", "lexicon_t_perp_raw_embedding", "lexicon_t_perp_content_embedding")
measure_labels <- c("Lexicon", "LOO embedding", "Raw $T_{\\perp}$", "Content $T_{\\perp}$")

write_iv_panel <- function(outcome_name) {
  z <- d16_iv[sample == "D5 common-prior 25-draw binary design" & outcome == outcome_name][match(binary_measures, tone_measure)]
  c(
    paste0("Second-stage coefficient & ", paste(mapply(coef_cell, z$iv_estimate, z$iv_p_value), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(z$iv_std_error, se_cell, character(1)), collapse = " & "), " \\\\"),
    paste0("Effect per measure SD & ", paste(fmt(z$iv_effect_per_measure_sd, 4), collapse = " & "), " \\\\"),
    "\\addlinespace",
    paste0("First-stage coefficient & ", paste(fmt(z$first_stage, 4), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(z$first_stage_se, se_cell, character(1)), collapse = " & "), " \\\\"),
    paste0("First-stage $F$ & ", paste(fmt(z$first_stage_f, 1), collapse = " & "), " \\\\ ")
  )
}

alternative_iv_lines <- c(
  table_header("Prompt-IV under alternative measures of realized tone", "tab:alternative-tone-iv"),
  "\\setlength{\\tabcolsep}{3.8pt}",
  "\\begin{tabular}{lcccc}",
  "\\toprule",
  paste0(" & ", paste(mc(paste0("(", 1:4, ")")), collapse = " & "), " \\\\"),
  paste0(" & ", paste(mc(measure_labels), collapse = " & "), " \\\\"),
  "\\midrule",
  "\\multicolumn{5}{l}{\\textit{Panel A. Unrestricted descriptive calibration}} \\\\",
  write_iv_panel("current_unrestricted"),
  "\\addlinespace",
  "\\multicolumn{5}{l}{\\textit{Panel B. Bayesian share calibration}} \\\\",
  write_iv_panel("hierarchical_simplex"),
  "\\midrule",
  paste0("Pair fixed effects & ", paste(mc(rep("Yes", 4)), collapse = " & "), " \\\\"),
  paste0("SE clustered by meeting & ", paste(mc(rep("Yes", 4)), collapse = " & "), " \\\\"),
  paste0("Observations & ", paste(mc(rep("3,550", 4)), collapse = " & "), " \\\\"),
  paste0("Meeting clusters & ", paste(mc(rep("71", 4)), collapse = " & "), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "Each column instruments the named realized-tone measure with the assigned hawkish instruction in the same 25-regeneration paired panel.",
    "The LOO embedding direction is trained leaving each meeting out. Raw $T_{\\perp}$ residualizes lexicon tone on 36 embedding principal components; content $T_{\\perp}$ removes only the instrument-predictable content component.",
    "Coefficients differ in scale, so the effect per one within-sample standard deviation is reported for comparison.",
    "We cluster standard errors by Copom meeting. * $p<0.10$, ** $p<0.05$, *** $p<0.01$."
  ))
)
write_tex("rr_alternative_tone_iv.tex", alternative_iv_lines)

# Dose IV with one column per alternative tone measure.
dose_measures <- c("lexicon_tone_pp", "semantic_score")
dose_labels <- c("Lexicon", "Direct rating")
write_dose_panel <- function(outcome_name) {
  z <- d16_iv[sample == "Common-prior five-level assigned-dose design" & outcome == outcome_name][match(dose_measures, tone_measure)]
  c(
    paste0("Second-stage coefficient & ", paste(mapply(coef_cell, z$iv_estimate, z$iv_p_value), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(z$iv_std_error, se_cell, character(1)), collapse = " & "), " \\\\"),
    paste0("Effect per measure SD & ", paste(fmt(z$iv_effect_per_measure_sd, 4), collapse = " & "), " \\\\"),
    "\\addlinespace",
    paste0("First-stage coefficient & ", paste(fmt(z$first_stage, 4), collapse = " & "), " \\\\"),
    paste0(" & ", paste(vapply(z$first_stage_se, se_cell, character(1)), collapse = " & "), " \\\\"),
    paste0("First-stage $F$ & ", paste(fmt(z$first_stage_f, 1), collapse = " & "), " \\\\ ")
  )
}

dose_iv_lines <- c(
  table_header("Dose-response IV estimates using two tone measures", "tab:dose-tone-iv"),
  "\\begin{tabular}{ldd}",
  "\\toprule",
  paste0(" & ", paste(mc(paste0("(", 1:2, ")")), collapse = " & "), " \\\\"),
  paste0(" & ", paste(mc(dose_labels), collapse = " & "), " \\\\"),
  "\\midrule",
  "\\multicolumn{3}{l}{\\textit{Panel A. Unrestricted descriptive calibration}} \\\\",
  write_dose_panel("current_unrestricted"),
  "\\addlinespace",
  "\\multicolumn{3}{l}{\\textit{Panel B. Bayesian share calibration}} \\\\",
  write_dose_panel("hierarchical_simplex"),
  "\\midrule",
  paste0("Pair fixed effects & ", paste(mc(rep("Yes", 2)), collapse = " & "), " \\\\"),
  paste0("SE clustered by meeting & ", paste(mc(rep("Yes", 2)), collapse = " & "), " \\\\"),
  paste0("Observations & ", paste(mc(rep("1,775", 2)), collapse = " & "), " \\\\"),
  paste0("Meeting clusters & ", paste(mc(rep("71", 2)), collapse = " & "), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "Assigned doses are $-2,-1,0,1,2$ for high intensity dove, low intensity dove, neutral, low intensity hawk, and high intensity hawk.",
    "For the direct rating, GPT-5.5 scores each text without its assignment label from $-1$ for very dovish to $1$ for very hawkish.",
    "The two measures use different scales.",
    "Standard errors are clustered by Copom meeting. * $p<0.10$, ** $p<0.05$, *** $p<0.01$."
  ))
)
write_tex("rr_dose_tone_iv.tex", dose_iv_lines)

# Average dose slopes for the appendix.
dose_slopes <- read_result("common_prior_dose_slopes_20260714.csv")
dose_tone_summary <- d16_iv[
  sample == "Common-prior five-level assigned-dose design" &
    outcome == "current_unrestricted"
][match(c("lexicon_tone_pp", "semantic_score"), tone_measure)]
dose_tone_summary[, first_stage_p := stats::pf(
  first_stage_f,
  df1 = 1,
  df2 = 70,
  lower.tail = FALSE
)]
dose_forecast_summary <- dose_slopes[
  match(c("Composite", "Anchored", "Unanchored"), sample)
]
if (anyNA(dose_tone_summary$first_stage) ||
    anyNA(dose_forecast_summary$estimate)) {
  stop("Dose-response summary inputs are incomplete")
}

dose_summary_lines <- c(
  table_header("Average changes per assigned dose", "tab:app-dose-slopes", size = "footnotesize"),
  "\\setlength{\\tabcolsep}{5.2pt}",
  "\\begin{tabular}{lddd}",
  "\\toprule",
  "\\multicolumn{4}{l}{\\textit{Panel A. Tone}} \\\\",
  " & \\mc{Lexicon} & \\mc{Direct rating} & \\\\",
  paste0(
    "Change per assigned dose & ",
    paste(mapply(
      coef_cell,
      dose_tone_summary$first_stage,
      dose_tone_summary$first_stage_p,
      MoreArgs = list(digits = 4)
    ), collapse = " & "),
    " & \\\\"
  ),
  paste0(
    " & ",
    paste(vapply(dose_tone_summary$first_stage_se, se_cell, character(1)), collapse = " & "),
    " & \\\\"
  ),
  paste0(
    "First-stage $F$ statistic & ",
    paste(fmt(dose_tone_summary$first_stage_f, 1), collapse = " & "),
    " & \\\\"
  ),
  "\\addlinespace",
  "\\multicolumn{4}{l}{\\textit{Panel B. Synthetic forecast revision}} \\\\",
  " & \\mc{Composite} & \\mc{Anchored} & \\mc{Unanchored} \\\\",
  paste0(
    "Change per assigned dose (bp) & ",
    paste(mapply(
      coef_cell,
      100 * dose_forecast_summary$estimate,
      dose_forecast_summary$p_value,
      MoreArgs = list(digits = 2)
    ), collapse = " & "),
    " \\\\"
  ),
  paste0(
    " & ",
    paste(vapply(100 * dose_forecast_summary$std_error, se_cell, character(1), digits = 2), collapse = " & "),
    " \\\\"
  ),
  "\\midrule",
  "Meeting and regeneration fixed effects & \\mc{Yes} & \\mc{Yes} & \\mc{Yes} \\\\",
  "Observations & \\mc{1,775} & \\mc{1,775} & \\mc{1,775} \\\\",
  "Meeting clusters & \\mc{71} & \\mc{71} & \\mc{71} \\\\",
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "The assigned doses are $-2,-1,0,1,2$, running from high intensity dove to high intensity hawk.",
    "Panel A reports the change in each tone measure for a one level increase in the assigned dose.",
    "Lexicon tone is the difference between hawkish and dovish expression counts per 100 words.",
    "GPT-5.5 rates each text from $-1$ for very dovish to $1$ for very hawkish without seeing its assignment.",
    "Panel B reports the change in the synthetic twelve-month-ahead inflation forecast in basis points.",
    "One basis point equals 0.01 percentage point.",
    "We cluster standard errors by meeting. * $p<0.10$, ** $p<0.05$, *** $p<0.01$."
  ), size = "footnotesize")
)
write_tex("rr_dose_response_summary.tex", dose_summary_lines)

# Dose means for the main text.
dose_tone_cells <- read_result("common_prior_dose_tone_cells_20260714.csv")
dose_arm_effects <- read_result("common_prior_dose_arm_effects_20260714.csv")
dose_arm_order <- c("dove_high", "dove_low", "neutral", "hawk_low", "hawk_high")
dose_arm_labels <- c(
  dove_high = "High intensity dove",
  dove_low = "Low intensity dove",
  neutral = "Neutral",
  hawk_low = "Low intensity hawk",
  hawk_high = "High intensity hawk"
)
dose_values <- c(dove_high = -2, dove_low = -1, neutral = 0, hawk_low = 1, hawk_high = 2)

dose_full_lines <- c(
  table_header(
    "Tone and forecast revisions by assigned dose",
    "tab:dose-response",
    size = "footnotesize"
  ),
  "\\setlength{\\tabcolsep}{6pt}",
  "\\begin{tabular}{lcddd}",
  "\\toprule",
  "\\multicolumn{5}{l}{\\textit{Panel A. Tone}} \\\\",
  "Assigned prompt & Dose & \\mc{Lexicon tone (pp)} & \\mc{Direct rating} & \\\\",
  "\\midrule"
)
for (arm in dose_arm_order) {
  arm_name <- arm
  rows <- dose_tone_cells[
    get("arm") == arm_name & measure %in% c("tone_pp", "semantic_score")
  ][match(c("tone_pp", "semantic_score"), measure)]
  if (nrow(rows) != 2L || anyNA(rows$estimate)) stop("Dose tone lookup failed")
  dose_full_lines <- c(
    dose_full_lines,
    paste0(
      dose_arm_labels[[arm]], " & ", dose_values[[arm]], " & ",
      paste(fmt(rows$estimate, 4), collapse = " & "), " & \\\\"
    ),
    paste0(" & & ", paste(vapply(rows$std_error, se_cell, character(1)), collapse = " & "), " & \\\\")
  )
}
dose_full_lines <- c(
  dose_full_lines,
  "\\addlinespace[0.08cm]",
  paste0(
    "Change per assigned dose & & ",
    paste(mapply(
      coef_cell,
      dose_tone_summary$first_stage,
      dose_tone_summary$first_stage_p,
      MoreArgs = list(digits = 4)
    ), collapse = " & "),
    " & \\\\"
  ),
  paste0(
    " & & ",
    paste(vapply(dose_tone_summary$first_stage_se, se_cell, character(1)), collapse = " & "),
    " & \\\\"
  ),
  "\\addlinespace[0.18cm]",
  "\\multicolumn{5}{l}{\\textit{Panel B. Synthetic forecast revision}} \\\\",
  "Assigned prompt & Dose & \\mc{Composite} & \\mc{Anchored} & \\mc{Unanchored} \\\\",
  "\\midrule"
)
for (arm in dose_arm_order) {
  arm_name <- arm
  rows <- dose_arm_effects[
    get("arm") == arm_name & sample %in% c("Composite", "Anchored", "Unanchored")
  ][match(c("Composite", "Anchored", "Unanchored"), sample)]
  if (nrow(rows) != 3L || anyNA(rows$estimate)) stop("Dose forecast lookup failed")
  dose_full_lines <- c(
    dose_full_lines,
    paste0(
      dose_arm_labels[[arm]], " & ", dose_values[[arm]], " & ",
      paste(mapply(coef_cell, rows$estimate, rows$p_value), collapse = " & "), " \\\\"
    ),
    paste0(" & & ", paste(vapply(rows$std_error, se_cell, character(1)), collapse = " & "), " \\\\")
  )
}
dose_full_lines <- c(
  dose_full_lines,
  "\\addlinespace[0.08cm]",
  paste0(
    "Change per assigned dose (bp) & & ",
    paste(mapply(
      coef_cell,
      100 * dose_forecast_summary$estimate,
      dose_forecast_summary$p_value,
      MoreArgs = list(digits = 2)
    ), collapse = " & "),
    " \\\\"
  ),
  paste0(
    " & & ",
    paste(vapply(100 * dose_forecast_summary$std_error, se_cell, character(1), digits = 2), collapse = " & "),
    " \\\\"
  ),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "Each prompt produces 355 versions of the minutes, with five regenerations for each of 71 meetings.",
    "Panel A reports mean tone. Panel B reports mean forecast revisions relative to the original minute within the same meeting and regeneration.",
    "The rows labeled \\emph{Change per assigned dose} report coefficients from linear regressions of each outcome on the assigned dose, coded $-2,-1,0,1,2$, with fixed effects for meeting and regeneration pairs.",
    "A one-unit increase in dose moves one step toward hawkishness. All standard errors are clustered by meeting.",
    "Panel B means are in percentage points; its change-per-dose row is in basis points.",
    "The composite applies the full sample calibration coefficients to the anchored and unanchored revisions.",
    "Within each profile and meeting, every assignment and regeneration starts from the same prior.",
    "* $p<0.10$, ** $p<0.05$, *** $p<0.01$."
  ), size = "footnotesize")
)
write_tex("rr_dose_response_full.tex", dose_full_lines)

# Complete inference table, including the exact R wild-cluster bootstrap.
d23_wild <- read_result("referee_d23_wild_cluster_iv_20260715.csv")[1]
common_inference <- read_result("common_prior_inference_20260714.csv")
inference_methods <- c(
  "Meeting-clustered 2SLS",
  "Anderson-Rubin",
  "Conditional likelihood ratio"
)
common_inference <- common_inference[match(inference_methods, method)]
if (nrow(common_inference) != length(inference_methods) ||
    anyNA(common_inference$method) ||
    any(!is.finite(common_inference$ci_low)) ||
    any(!is.finite(common_inference$ci_high)) ||
    !is.finite(common_inference[method == "Meeting-clustered 2SLS"]$estimate)) {
  stop("Common-prior inference inputs are incomplete")
}
clustered_2sls <- common_inference[method == "Meeting-clustered 2SLS"][1]
anderson_rubin <- common_inference[method == "Anderson-Rubin"][1]
conditional_lr <- common_inference[method == "Conditional likelihood ratio"][1]
wild_p_cell <- if (d23_wild$p_value == 0) {
  paste0(
    "$<1/",
    gsub(
      ",",
      "{,}",
      format(d23_wild$bootstrap_replications, big.mark = ",", scientific = FALSE),
      fixed = TRUE
    ),
    "$"
  )
} else {
  p_cell(d23_wild$p_value)
}
inference_lines <- c(
  table_header("Weak-IV and cluster-robust inference for the baseline Prompt-IV", "tab:inference-completion", "footnotesize"),
  "\\begin{tabular}{lccc}",
  "\\toprule",
  "Method & Estimate & 95\\% confidence set & $p$ value \\\\",
  "\\midrule",
  paste0(
    "Meeting-clustered 2SLS & ", fmt(clustered_2sls$estimate, 4), " & ",
    ci_cell(clustered_2sls$ci_low, clustered_2sls$ci_high, 4), " & ",
    p_cell(clustered_2sls$p_value), " \\\\"
  ),
  paste0(
    "Anderson--Rubin & -- & ",
    ci_cell(anderson_rubin$ci_low, anderson_rubin$ci_high, 4), " & ",
    p_cell(anderson_rubin$p_value), " \\\\"
  ),
  paste0(
    "Conditional likelihood ratio & -- & ",
    ci_cell(conditional_lr$ci_low, conditional_lr$ci_high, 4), " & ",
    p_cell(conditional_lr$p_value), " \\\\"
  ),
  paste0(
    "Wild-cluster bootstrap-$t$ & ", fmt(d23_wild$estimate, 4), " & ",
    ci_cell(d23_wild$ci_low, d23_wild$ci_high, 4), " & ",
    wild_p_cell, " \\\\"
  ),
  "\\midrule",
  paste0("Wild-bootstrap replications & \\multicolumn{3}{c}{", format(d23_wild$bootstrap_replications, big.mark = ",", scientific = FALSE), "} \\\\"),
  paste0("Meeting clusters & \\multicolumn{3}{c}{", d23_wild$clusters, "} \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "All procedures use the same pair-fixed-effect IV specification.",
    "The wild-cluster bootstrap-$t$ is implemented in R with clusterSEs and 9,999 Rademacher draws at the meeting level.",
    "Zero simulated exceedances are reported at Monte Carlo resolution as $p<1/9{,}999$, not as a literal zero probability."
  ), "footnotesize")
)
write_tex("rr_inference_completion.tex", inference_lines)

# Tone numerator/denominator variance decomposition.
d24 <- read_result("referee_d24_tone_variance_decomposition_20260715.csv")
d24 <- d24[match(c("dove_text", "hawk_text"), role)]
variance_lines <- c(
  table_header("Variance decomposition of the lexicon tone index", "tab:tone-variance-decomposition", "footnotesize"),
  "\\begin{tabular}{lcc}",
  "\\toprule",
  "Component & Dovish rewrites & Hawkish rewrites \\\\",
  "\\midrule",
  paste0("Numerator contribution & ", paste(tex_percent(d24$numerator_share), collapse = " & "), " \\\\"),
  paste0("Length-denominator contribution & ", paste(tex_percent(d24$denominator_share), collapse = " & "), " \\\\"),
  paste0("Actual tone variance & ", paste(fmt(d24$actual_variance, 3), collapse = " & "), " \\\\"),
  paste0("Observations & ", paste(d24$observations, collapse = " & "), " \\\\"),
  "\\bottomrule",
  "\\end{tabular}",
  table_footer(paste(
    "The numerator is the hawkish-minus-dovish lexicon count and the denominator is document length.",
    "The exact covariance term is allocated by Shapley averaging across the two components, so the reported shares sum to one.",
    "Most variation comes from stance-word counts rather than document length."
  ), "footnotesize")
)
write_tex("rr_tone_variance_decomposition.tex", variance_lines)

# Figures: regime heterogeneity, tone-measure validation, and embedding preservation.
theme_journal <- function() {
  theme_classic(base_size = 10, base_family = "sans") +
    theme(
      panel.grid.major.y = element_line(colour = "#E2E2E2", linewidth = 0.3),
      panel.grid.minor = element_blank(),
      legend.position = "bottom",
      legend.title = element_blank(),
      plot.margin = margin(7, 9, 6, 7)
    )
}

d7_plot <- copy(d7)
d7_plot[, ci_low := prompt_iv - 1.96 * prompt_iv_se]
d7_plot[, ci_high := prompt_iv + 1.96 * prompt_iv_se]
d7_plot[, regime_label := factor(
  regime,
  levels = rev(c("Pre-Campos Neto", "Campos Neto onward", "Pre-2021", "2021 onward", "Below-median realized inflation", "Above-median realized inflation", "Markov state 1", "Markov state 2")),
  labels = rev(c("Pre-Campos Neto", "Campos Neto onward", "Pre-2021", "2021 onward", "Below-median inflation", "Above-median inflation", "Markov state 1", "Markov state 2"))
)]

p_regime <- ggplot(d7_plot, aes(x = prompt_iv, y = regime_label)) +
  geom_vline(xintercept = 0, colour = "#777777", linewidth = 0.45) +
  geom_errorbar(
    aes(xmin = ci_low, xmax = ci_high),
    orientation = "y",
    width = 0,
    linewidth = 0.55,
    colour = "#555555"
  ) +
  geom_point(size = 2.2, colour = "#0072B2") +
  scale_x_continuous(labels = label_number(accuracy = 0.01)) +
  labs(x = "Prompt-IV sensitivity (percentage points per tone point)", y = NULL) +
  theme_journal()
ggsave(file.path(figure_dir, "rr_regime_promptiv.pdf"), p_regime, device = cairo_pdf, width = 7.1, height = 4.2, units = "in", bg = "white")

d7_rolling <- read_result("referee_d7_rolling_24_meetings_20260715.csv")
d7_rolling[, window_end := as.Date(window_end)]
d7_weights <- melt(
  d7_rolling,
  id.vars = "window_end",
  measure.vars = c("anchored_weight", "unanchored_weight"),
  variable.name = "profile",
  value.name = "weight"
)
d7_weights[, profile := factor(
  profile,
  levels = c("anchored_weight", "unanchored_weight"),
  labels = c("Anchored", "Unanchored")
)]
p_rolling_weights <- ggplot(d7_weights, aes(x = window_end, y = weight, colour = profile)) +
  geom_hline(yintercept = 0, colour = "#777777", linewidth = 0.4) +
  geom_vline(xintercept = as.Date(c("2020-03-18", "2024-01-01")), colour = "#C5C5C5", linewidth = 0.4) +
  geom_line(linewidth = 0.75) +
  geom_point(size = 1.2) +
  scale_colour_manual(values = c("Anchored" = "#D55E00", "Unanchored" = "#0072B2")) +
  scale_x_date(date_breaks = "1 year", date_labels = "%Y") +
  labs(x = "End of 24-meeting rolling window", y = "Unrestricted calibration coefficient") +
  theme_journal()
ggsave(file.path(figure_dir, "rr_rolling_calibration_weights.pdf"), p_rolling_weights, device = cairo_pdf, width = 7.1, height = 4.0, units = "in", bg = "white")

scatter <- read_result("referee_d16_tone_scatter_data_20260715.csv")
scatter[, role := factor(source_role, levels = topic_roles, labels = role_labels)]
arm_colors <- c("High dove" = "#D55E00", "Low dove" = "#E69F00", "Neutral" = "#777777", "Low hawk" = "#56B4E9", "High hawk" = "#0072B2")
p_scatter <- ggplot(scatter[source_role != "original"], aes(x = lexicon_tone_pp, y = semantic_score, colour = role)) +
  geom_point(alpha = 0.20, size = 0.85) +
  geom_smooth(aes(group = 1), method = "lm", formula = y ~ x, se = TRUE, colour = "#222222", fill = "#BDBDBD", linewidth = 0.65) +
  scale_colour_manual(values = arm_colors) +
  labs(x = "Lexicon tone (percentage points)", y = "Direct rating") +
  theme_journal()
ggsave(file.path(figure_dir, "rr_tone_measure_validation.pdf"), p_scatter, device = cairo_pdf, width = 7.1, height = 4.3, units = "in", bg = "white")

pres_plot <- d16_pres[
  run_id == "v10_referee_step3_neutral_dose_5draws_20260627" &
    source_role %in% topic_roles &
    metric %in% c("document_cosine_similarity", "symmetric_sentence_similarity")
]
pres_plot[, role := factor(source_role, levels = topic_roles, labels = role_labels)]
pres_plot[, measure := factor(metric, levels = c("document_cosine_similarity", "symmetric_sentence_similarity"), labels = c("Document cosine", "Sentence alignment"))]
p_pres <- ggplot(pres_plot, aes(x = estimate, y = role, colour = measure)) +
  geom_errorbar(
    aes(xmin = ci_low, xmax = ci_high),
    orientation = "y",
    width = 0.10,
    position = position_dodge(width = 0.34),
    linewidth = 0.55
  ) +
  geom_point(position = position_dodge(width = 0.34), size = 2.1) +
  scale_colour_manual(values = c("Document cosine" = "#0072B2", "Sentence alignment" = "#D55E00")) +
  scale_x_continuous(limits = c(0.80, 1.005), breaks = seq(0.80, 1.00, 0.05), labels = label_number(accuracy = 0.01)) +
  labs(x = "Similarity to the original minute", y = NULL) +
  theme_journal()
ggsave(file.path(figure_dir, "rr_embedding_preservation.pdf"), p_pres, device = cairo_pdf, width = 7.1, height = 3.9, units = "in", bg = "white")

cat("Referee integration tables and figures rebuilt from July 15 R outputs.\n")
