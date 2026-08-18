suppressPackageStartupMessages(library(data.table))

root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
intermediate <- file.path(root, "outputs", "tables")
result_dir <- file.path(root, "results")
manifest <- fread(file.path(root, "data", "metadata", "exhibit_manifest.csv"))

normalized_text <- function(path) {
  paste(readLines(path, warn = FALSE, encoding = "UTF-8"), collapse = "\n")
}

artifact_rows <- rbindlist(lapply(seq_len(nrow(manifest)), function(index) {
  output_path <- file.path(root, manifest$output_path[[index]])
  reference_path <- file.path(root, manifest$reference_path[[index]])
  output_size <- if (file.exists(output_path)) file.info(output_path)$size else 0
  present <- is.finite(output_size) && output_size > 0
  is_table <- identical(tolower(manifest$type[[index]]), "table")
  reference_present <- file.exists(reference_path) &&
    is.finite(file.info(reference_path)$size) &&
    file.info(reference_path)$size > 0
  passed <- if (is_table) {
    present &&
      reference_present &&
      identical(
        normalized_text(output_path),
        normalized_text(reference_path)
      )
  } else {
    present
  }
  data.table(
    check_id = paste0("artifact:", manifest$output_path[[index]]),
    observed = as.numeric(passed),
    expected = 1,
    tolerance = 0,
    status = if (passed) "pass" else "fail",
    detail = if (is_table) {
      paste("exact normalized-text match to reference;", manifest$producer[[index]])
    } else {
      manifest$producer[[index]]
    }
  )
}))

checks <- list()
add_check <- function(id, observed, expected, tolerance, detail = "") {
  checks[[length(checks) + 1L]] <<- data.table(
    check_id = id,
    observed = as.numeric(observed),
    expected = as.numeric(expected),
    tolerance = as.numeric(tolerance),
    status = if (is.finite(observed) && abs(observed - expected) <= tolerance) "pass" else "fail",
    detail = detail
  )
}

main <- fread(file.path(intermediate, "common_prior_main_models_20260714.csv"))
main <- main[sample == "Composite"][1]
add_check("main_prompt_iv", main$prompt_iv, 0.0187233976525336, 1e-10)
add_check("main_prompt_iv_se", main$prompt_iv_se, 0.00281419238078897, 1e-10)
add_check("main_reduced_form", main$reduced_form, 0.0226657185584151, 1e-10)
add_check("main_first_stage", main$first_stage, 1.21055584990723, 1e-10)
add_check("main_first_stage_f", main$first_stage_f, 3347.64067699011, 1e-6)

inference <- fread(file.path(
  intermediate,
  "common_prior_inference_20260714.csv"
))
inference_row <- function(method_name) {
  row <- inference[method == method_name]
  if (nrow(row) != 1L) {
    stop("Expected one common-prior inference row for: ", method_name)
  }
  row
}
clustered_2sls <- inference_row("Meeting-clustered 2SLS")
anderson_rubin <- inference_row("Anderson-Rubin")
conditional_lr <- inference_row("Conditional likelihood ratio")
add_check(
  "inference_clustered_2sls_estimate",
  clustered_2sls$estimate,
  0.0187233976525336,
  1e-10
)
add_check(
  "inference_clustered_2sls_ci_low",
  clustered_2sls$ci_low,
  0.0132075805861872,
  1e-10
)
add_check(
  "inference_clustered_2sls_ci_high",
  clustered_2sls$ci_high,
  0.02423921471888,
  1e-10
)
add_check(
  "inference_ar_ci_low",
  anderson_rubin$ci_low,
  0.0159426961125161,
  1e-10
)
add_check(
  "inference_ar_ci_high",
  anderson_rubin$ci_high,
  0.0215067158705976,
  1e-10
)
add_check(
  "inference_clr_ci_low",
  conditional_lr$ci_low,
  0.0159426961512317,
  1e-10
)
add_check(
  "inference_clr_ci_high",
  conditional_lr$ci_high,
  0.0215067158318093,
  1e-10
)
inference_table_text <- normalized_text(file.path(
  result_dir,
  "tables",
  "rr_inference_completion.tex"
))
add_check(
  "inference_table_uses_current_main_iv",
  grepl(
    "Meeting-clustered 2SLS & 0.0187 & [0.0132, 0.0242]",
    inference_table_text,
    fixed = TRUE
  ),
  1,
  0
)
add_check(
  "inference_table_excludes_superseded_values",
  !grepl(
    "0.0258|\\[0.0172, 0.0345\\]|\\[0.0214, 0.0303\\]",
    inference_table_text
  ),
  1,
  0
)

draws <- fread(file.path(intermediate, "common_prior_main_by_draw_20260714.csv"))
add_check("draws_positive", sum(draws$prompt_iv > 0), 25, 0)
add_check("draw_iv_mean", mean(draws$prompt_iv), 0.0203663099696411, 1e-10)

dose <- fread(file.path(intermediate, "common_prior_dose_arm_effects_20260714.csv"))
dose <- dose[sample == "Composite"]
dose_expected <- c(
  dove_high = -0.0103160290562316,
  dove_low = -0.00838254219653829,
  neutral = 0.00184079683448314,
  hawk_low = 0.0178954992683954,
  hawk_high = 0.0248736603398609
)
for (arm_name in names(dose_expected)) {
  add_check(
    paste0("dose_", arm_name),
    dose[arm == arm_name]$estimate,
    dose_expected[[arm_name]],
    1e-10
  )
}

heldout <- fread(file.path(intermediate, "common_prior_heldout_20260714.csv"))
heldout <- heldout[specification == "Held-out 2024-2025, pre-2024 calibration"][1]
add_check("heldout_prompt_iv", heldout$prompt_iv, 0.00951157687719227, 1e-10)

d8_loo <- fread(file.path(intermediate, "referee_d8_loo_summary_20260715.csv"))[1]
d8_metrics <- fread(file.path(
  intermediate,
  "referee_d8_forecast_metrics_20260715.csv"
))
d8_loss <- fread(file.path(
  intermediate,
  "referee_d8_mean_loss_tests_20260715.csv"
))
d8_composite <- d8_metrics[model == "composite"][1]
d8_ar1 <- d8_metrics[model == "focus_ar1"][1]
d8_equal <- d8_metrics[model == "equal_weight"][1]
d8_bates <- d8_metrics[model == "bates_granger"][1]
d8_composite_loss <- d8_loss[model == "composite"][1]
d8_bates_loss <- d8_loss[model == "bates_granger"][1]
add_check("d8_loo_meetings", d8_loo$n, 71, 0)
add_check("d8_loo_r_squared", d8_loo$r2_cv, 0.722688788581542, 1e-10)
add_check("d8_loo_rmse", d8_loo$rmse, 0.408629324440901, 1e-10)
add_check("d8_loo_mae", d8_loo$mae, 0.288508663523992, 1e-10)
add_check("d8_expanding_forecasts", d8_composite$n, 47, 0)
add_check("d8_composite_rmse", d8_composite$rmse, 0.574094257218305, 1e-10)
add_check("d8_focus_ar1_rmse", d8_ar1$rmse, 0.474253866457948, 1e-10)
add_check("d8_equal_weight_rmse", d8_equal$rmse, 0.520067896549361, 1e-10)
add_check("d8_bates_granger_rmse", d8_bates$rmse, 0.483167978690283, 1e-10)
add_check(
  "d8_composite_r2_oos_vs_historical_mean",
  d8_composite$r2_oos_vs_historical_mean,
  0.607690051411221,
  1e-10
)
add_check(
  "d8_composite_loss_difference",
  d8_composite_loss$mean_loss_difference,
  -0.104667486320724,
  1e-10
)
add_check(
  "d8_composite_loss_hac_se",
  d8_composite_loss$se_hac,
  0.104641068763104,
  1e-10
)
add_check(
  "d8_composite_loss_p",
  d8_composite_loss$p_hac,
  0.322421205902739,
  1e-10
)
add_check(
  "d8_bates_granger_loss_p",
  d8_bates_loss$p_hac,
  0.931361294848362,
  1e-10
)

placebo <- fread(file.path(intermediate, "referee_d5_scrambled_outcome_placebo_summary_20260715.csv"))[1]
add_check("placebo_mean_f", placebo$mean_first_stage_f, 1.08475291586012, 1e-8)
add_check("placebo_iv_rejection_share", placebo$share_prompt_iv_p_below_05, 0, 1e-12)

hierarchical <- fread(file.path(intermediate, "referee_d6_hierarchical_dirichlet_20260715.csv"))
add_check(
  "hierarchical_unanchored_weight",
  hierarchical[parameter == "Unanchored weight"]$mean,
  0.347694228985941,
  0.01,
  "MCMC tolerance"
)

regime <- fread(file.path(intermediate, "referee_d7_regime_estimates_20260715.csv"))
add_check("pre_campos_prompt_iv", regime[get("regime") == "Pre-Campos Neto"]$prompt_iv, 0.00409988541274492, 1e-9)
add_check("campos_prompt_iv", regime[get("regime") == "Campos Neto onward"]$prompt_iv, 0.0183957165253756, 1e-9)

sunspot <- fread(file.path(intermediate, "referee_d10_sunspot_order_bootstrap_20260715.csv"))
sunspot <- sunspot[synthetic_aggregate == "hierarchical_simplex"][1]
add_check("sunspot_bmz_share", sunspot$bmz_order_bootstrap_share, 0.27663831915958, 1e-12)
add_check("sunspot_reverse_share", sunspot$reverse_order_bootstrap_share, 0.250625312656328, 1e-12)

wild <- fread(file.path(intermediate, "referee_d23_wild_cluster_iv_20260715.csv"))[1]
add_check("wild_cluster_estimate", wild$estimate, 0.0187233976525341, 1e-10)
add_check("wild_cluster_ci_low", wild$ci_low, 0.0130462562760255, 1e-8)
add_check("wild_cluster_ci_high", wild$ci_high, 0.0244005390290426, 1e-8)

embedding <- fread(file.path(intermediate, "referee_d16_embedding_preservation_20260715.csv"))
add_check(
  "dove_document_cosine",
  embedding[source_role == "dove" & metric == "document_cosine_similarity"]$estimate,
  0.967692190929199,
  1e-10
)
add_check(
  "hawk_document_cosine",
  embedding[source_role == "hawk" & metric == "document_cosine_similarity"]$estimate,
  0.968072820817924,
  1e-10
)

numeric_checks <- rbindlist(checks)
verification <- rbindlist(list(artifact_rows, numeric_checks), fill = TRUE)
write.csv(verification, file.path(result_dir, "verification_report.csv"), row.names = FALSE)

intext <- data.table(
  result = c(
    "Main Prompt-IV", "Main standard error", "Reduced form", "First stage",
    "First-stage F", "Held-out Prompt-IV", "High-dove revision bp",
    "Low-dove revision bp", "Neutral revision bp", "Low-hawk revision bp",
    "High-hawk revision bp", "LOO R-squared", "LOO RMSE",
    "Expanding composite RMSE", "Expanding Focus AR(1) RMSE",
    "Expanding composite R-squared OOS", "Composite loss-difference p-value",
    "Bates-Granger loss-difference p-value"
  ),
  value = c(
    main$prompt_iv, main$prompt_iv_se, main$reduced_form, main$first_stage,
    main$first_stage_f, heldout$prompt_iv,
    100 * dose[arm == "dove_high"]$estimate,
    100 * dose[arm == "dove_low"]$estimate,
    100 * dose[arm == "neutral"]$estimate,
    100 * dose[arm == "hawk_low"]$estimate,
    100 * dose[arm == "hawk_high"]$estimate,
    d8_loo$r2_cv, d8_loo$rmse, d8_composite$rmse, d8_ar1$rmse,
    d8_composite$r2_oos_vs_historical_mean,
    d8_composite_loss$p_hac, d8_bates_loss$p_hac
  )
)
write.csv(intext, file.path(result_dir, "intext_results.csv"), row.names = FALSE)
tex <- c(
  sprintf("\\newcommand{\\MainPromptIV}{%.4f}", main$prompt_iv),
  sprintf("\\newcommand{\\MainPromptIVSE}{%.4f}", main$prompt_iv_se),
  sprintf("\\newcommand{\\MainReducedForm}{%.4f}", main$reduced_form),
  sprintf("\\newcommand{\\MainFirstStage}{%.4f}", main$first_stage),
  sprintf("\\newcommand{\\MainFirstStageF}{%.1f}", main$first_stage_f),
  sprintf("\\newcommand{\\HeldoutPromptIV}{%.4f}", heldout$prompt_iv),
  sprintf("\\newcommand{\\DoseHighDoveBP}{%.2f}", 100 * dose[arm == "dove_high"]$estimate),
  sprintf("\\newcommand{\\DoseLowDoveBP}{%.2f}", 100 * dose[arm == "dove_low"]$estimate),
  sprintf("\\newcommand{\\DoseNeutralBP}{%.2f}", 100 * dose[arm == "neutral"]$estimate),
  sprintf("\\newcommand{\\DoseLowHawkBP}{%.2f}", 100 * dose[arm == "hawk_low"]$estimate),
  sprintf("\\newcommand{\\DoseHighHawkBP}{%.2f}", 100 * dose[arm == "hawk_high"]$estimate),
  sprintf("\\newcommand{\\LooRtwo}{%.4f}", d8_loo$r2_cv),
  sprintf("\\newcommand{\\LooRMSE}{%.4f}", d8_loo$rmse),
  sprintf("\\newcommand{\\ExpandingCompositeRMSE}{%.4f}", d8_composite$rmse),
  sprintf("\\newcommand{\\ExpandingFocusAROneRMSE}{%.4f}", d8_ar1$rmse),
  sprintf(
    "\\newcommand{\\ExpandingCompositeROOS}{%.4f}",
    d8_composite$r2_oos_vs_historical_mean
  ),
  sprintf("\\newcommand{\\CompositeLossDiffP}{%.4f}", d8_composite_loss$p_hac),
  sprintf("\\newcommand{\\BatesGrangerLossDiffP}{%.4f}", d8_bates_loss$p_hac)
)
writeLines(tex, file.path(result_dir, "intext_numbers.tex"), useBytes = TRUE)

if (any(verification$status != "pass")) {
  failed <- verification[status != "pass", check_id]
  stop("Replication verification failed: ", paste(failed, collapse = ", "))
}
cat(sprintf("Verified %d artifacts and numerical checks\n", nrow(verification)))
