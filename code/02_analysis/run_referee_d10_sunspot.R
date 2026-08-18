suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(sandwich)
  library(lmtest)
  library(jsonlite)
})

data.table::setDTthreads(1L)

root <- normalizePath(".", winslash = "/", mustWork = TRUE)
source(file.path(root, "code", "02_analysis", "reproducibility_helpers.R"), local = TRUE)
database_path <- file.path(root, "outputs", "work", "prompt_iv_replication.sqlite")
table_dir <- file.path(root, "outputs", "tables")
dir.create(table_dir, recursive = TRUE, showWarnings = FALSE)

stamp <- "20260715"
seed <- 20260715L
bootstrap_draws <- 1999L

calibration_path <- file.path(
  table_dir, paste0("referee_d6_hierarchical_dirichlet_", stamp, ".csv")
)
if (!file.exists(calibration_path)) {
  stop("Missing hierarchical-calibration output: ", calibration_path)
}
calibration <- fread(calibration_path)
anchored_weight <- calibration[parameter == "Anchored weight", mean]
unanchored_weight <- calibration[parameter == "Unanchored weight", mean]
if (length(anchored_weight) != 1L || length(unanchored_weight) != 1L ||
    any(!is.finite(c(anchored_weight, unanchored_weight))) ||
    abs(anchored_weight + unanchored_weight - 1) > 1e-10) {
  stop("Hierarchical posterior-mean calibration weights are invalid")
}

con <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(con), add = TRUE)
dbExecute(con, "PRAGMA busy_timeout = 60000")

# The laboratory analogues are deliberately narrow. The neutral rewrite is an
# imperfect benchmark, the original minute is the richest observed-information
# benchmark, and the four assigned tone arms are the candidate extrinsic factor.
dose <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, corpus_iteration, source_role, forecaster_type,",
    "forecast_revision",
    "FROM agent_results_latest",
    "WHERE run_id = 'common_prior_dose_6arm_5draws_v1_20260713'",
    "AND status = 'completed'"
  )
))
if (nrow(dose) != 4260L) {
  stop(sprintf("Expected 4,260 dose-agent rows, found %d", nrow(dose)))
}

dose_wide <- dcast(
  dose,
  meeting_number + corpus_iteration + source_role ~ forecaster_type,
  value.var = "forecast_revision"
)
dose_wide[, equal_weight := 0.5 * anchored + 0.5 * unanchored]
dose_wide[, hierarchical_simplex :=
  anchored_weight * anchored + unanchored_weight * unanchored]

dose_long <- melt(
  dose_wide,
  id.vars = c("meeting_number", "corpus_iteration", "source_role"),
  measure.vars = c(
    "anchored", "unanchored", "equal_weight", "hierarchical_simplex"
  ),
  variable.name = "synthetic_aggregate",
  value.name = "forecast_revision"
)
dose_meeting <- dose_long[, .(
  forecast_revision = mean(forecast_revision)
), by = .(meeting_number, source_role, synthetic_aggregate)]

tone_roles <- c("dove_high", "dove_low", "hawk_low", "hawk_high")

benchmark_variances <- function(data) {
  data.table(
    benchmark = c(
      "Quasi-REE proxy: original minute",
      "Candidate tone process: assigned arms",
      "Neutral-rewrite proxy (imperfect fundamentals benchmark)"
    ),
    variance = c(
      var(data[source_role == "original"]$forecast_revision),
      var(data[source_role %chin% tone_roles]$forecast_revision),
      var(data[source_role == "neutral"]$forecast_revision)
    ),
    observations = c(
      nrow(data[source_role == "original"]),
      nrow(data[source_role %chin% tone_roles]),
      nrow(data[source_role == "neutral"])
    )
  )
}

weighted_variance <- function(values, weights) {
  total_weight <- sum(weights)
  weighted_mean <- sum(weights * values) / total_weight
  sum(weights * (values - weighted_mean)^2) / (total_weight - 1)
}

set.seed(seed)
model_names <- unique(dose_meeting$synthetic_aggregate)
bootstrap_rows <- vector("list", length(model_names))
variance_rows <- vector("list", length(model_names))

for (model_index in seq_along(model_names)) {
  model_name <- model_names[[model_index]]
  model_data <- dose_meeting[synthetic_aggregate == model_name]
  meetings <- sort(unique(model_data$meeting_number))
  point <- benchmark_variances(model_data)
  setnames(point, "variance", "variance_point")

  bootstrap_matrix <- matrix(NA_real_, nrow = bootstrap_draws, ncol = 3L)
  colnames(bootstrap_matrix) <- c("quasi_ree", "tone", "fundamentals")
  for (draw in seq_len(bootstrap_draws)) {
    sampled <- sample(meetings, length(meetings), replace = TRUE)
    meeting_weights <- tabulate(
      match(sampled, meetings),
      nbins = length(meetings)
    )
    row_weights <- meeting_weights[match(model_data$meeting_number, meetings)]
    bootstrap_matrix[draw, ] <- c(
      weighted_variance(
        model_data[source_role == "original"]$forecast_revision,
        row_weights[model_data$source_role == "original"]
      ),
      weighted_variance(
        model_data[source_role %chin% tone_roles]$forecast_revision,
        row_weights[model_data$source_role %chin% tone_roles]
      ),
      weighted_variance(
        model_data[source_role == "neutral"]$forecast_revision,
        row_weights[model_data$source_role == "neutral"]
      )
    )
  }

  intervals <- t(apply(
    bootstrap_matrix,
    2L,
    quantile,
    probs = c(0.025, 0.975),
    na.rm = TRUE,
    names = FALSE
  ))
  point[, `:=`(
    synthetic_aggregate = model_name,
    variance_ci_low = intervals[, 1L],
    variance_ci_high = intervals[, 2L]
  )]
  variance_rows[[model_index]] <- point

  point_values <- setNames(point$variance_point, c("quasi_ree", "tone", "fundamentals"))
  bootstrap_rows[[model_index]] <- data.table(
    synthetic_aggregate = model_name,
    quasi_ree_variance = point_values[["quasi_ree"]],
    tone_variance = point_values[["tone"]],
    fundamentals_variance = point_values[["fundamentals"]],
    bmz_order_point =
      point_values[["quasi_ree"]] < point_values[["tone"]] &&
      point_values[["tone"]] < point_values[["fundamentals"]],
    reverse_order_point =
      point_values[["fundamentals"]] < point_values[["tone"]] &&
      point_values[["tone"]] < point_values[["quasi_ree"]],
    bmz_order_bootstrap_share = mean(
      bootstrap_matrix[, "quasi_ree"] < bootstrap_matrix[, "tone"] &
        bootstrap_matrix[, "tone"] < bootstrap_matrix[, "fundamentals"]
    ),
    reverse_order_bootstrap_share = mean(
      bootstrap_matrix[, "fundamentals"] < bootstrap_matrix[, "tone"] &
        bootstrap_matrix[, "tone"] < bootstrap_matrix[, "quasi_ree"]
    ),
    tone_to_quasi_ree_variance_ratio =
      point_values[["tone"]] / point_values[["quasi_ree"]],
    fundamentals_to_tone_variance_ratio =
      point_values[["fundamentals"]] / point_values[["tone"]],
    bootstrap_draws = bootstrap_draws
  )
}

variance_table <- rbindlist(variance_rows)
setcolorder(
  variance_table,
  c(
    "synthetic_aggregate", "benchmark", "variance_point",
    "variance_ci_low", "variance_ci_high", "observations"
  )
)
ordering_table <- rbindlist(bootstrap_rows)

# The BMZ existence conditions apply to a structural model. We report two
# transparent screens: the exact paired-assignment design, and a reduced-form
# proxy mapping using realized tone-gap persistence and macro-news persistence.
tone <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, iteration, role, 100.0 * score AS tone_pp",
    "FROM tone_scores",
    "WHERE run_id = 'v10_referee_d5_25draws_20260620'",
    "AND role IN ('hawk_text', 'dove_text')"
  )
))
tone_wide <- dcast(tone, meeting_number + iteration ~ role, value.var = "tone_pp")
tone_wide[, tone_gap := hawk_text - dove_text]
tone_gap <- tone_wide[, .(tone_gap = mean(tone_gap)), by = meeting_number]
setorder(tone_gap, meeting_number)
tone_ar <- lm(tone_gap[-1L, tone_gap] ~ tone_gap[-.N, tone_gap])
tone_ar_vcov <- NeweyWest(tone_ar, lag = 2L, prewhite = FALSE, adjust = TRUE)
phi_gap <- unname(coef(tone_ar)[[2L]])
phi_gap_se <- sqrt(diag(tone_ar_vcov))[[2L]]

recursive <- as.data.table(dbGetQuery(
  con,
  paste(
    "SELECT meeting_number, source_role, forecaster_type,",
    "previous_expectation, new_expectation",
    "FROM agent_results_latest",
    "WHERE run_id = 'arm_specific_prior_dose_6arm_5draws_v1_20260714'",
    "AND status = 'completed'"
  )
))
alpha_rows <- rbindlist(lapply(unique(recursive$forecaster_type), function(agent) {
  agent_data <- recursive[forecaster_type == agent]
  model <- lm(
    new_expectation ~ previous_expectation + factor(source_role),
    data = agent_data
  )
  vc <- vcovCL(model, cluster = ~meeting_number, type = "HC1")
  ct <- coeftest(model, vcov. = vc)
  data.table(
    forecaster_type = agent,
    alpha_proxy = ct["previous_expectation", "Estimate"],
    alpha_proxy_se = ct["previous_expectation", "Std. Error"],
    observations = nobs(model)
  )
}))
alpha_proxy <- mean(alpha_rows$alpha_proxy)

macro <- as.data.table(dbReadTable(con, "referee_meeting_macro_controls"))
setorder(macro, meeting_number)
macro_variables <- c(
  "ipca15_change_pre", "ibc_change_pre", "brl_return_5d_pre",
  "focus_drift_7d_pre", "selic_surprise"
)
macro_matrix <- as.matrix(macro[, ..macro_variables])
for (column in seq_len(ncol(macro_matrix))) {
  missing <- is.na(macro_matrix[, column])
  macro_matrix[missing, column] <- median(macro_matrix[, column], na.rm = TRUE)
}
macro_scaled <- scale(macro_matrix)
macro_pc <- prcomp(macro_scaled, center = FALSE, scale. = FALSE)$x[, 1L]

ar1_coefficient <- function(x) {
  unname(coef(lm(x[-1L] ~ x[-length(x)]))[[2L]])
}
rho_pc <- ar1_coefficient(macro_pc)
rho_components <- vapply(
  seq_len(ncol(macro_scaled)),
  function(column) ar1_coefficient(macro_scaled[, column]),
  numeric(1)
)
names(rho_components) <- macro_variables
rho_max <- max(rho_components)

alpha_threshold <- function(phi) 1 / (1 + phi * (1 - phi))
rho_threshold <- function(alpha, phi) {
  numerator <-
    alpha^2 * phi - alpha * phi^2 - alpha^3 * phi^2 +
    alpha * phi^4 + alpha^3 * phi^4 - alpha^2 * phi^5
  denominator <-
    alpha^2 + alpha^3 * phi^3 + 3 * alpha^2 * phi^4 -
    6 * alpha^2 * phi^2 - alpha^2 * phi^6 - 1 + 3 * alpha * phi
  numerator / denominator
}

assignment_alpha_threshold <- alpha_threshold(0)
gap_alpha_threshold <- alpha_threshold(phi_gap)
gap_rho_threshold <- rho_threshold(alpha_proxy, phi_gap)

existence_screen <- data.table(
  screen = c(
    "Exact paired prompt assignment",
    "Reduced-form proxy using macro-news PC1",
    "Reduced-form proxy using most persistent measured macro-news series"
  ),
  alpha_proxy = alpha_proxy,
  phi_proxy = c(0, phi_gap, phi_gap),
  rho_proxy = c(NA_real_, rho_pc, rho_max),
  alpha_threshold = c(assignment_alpha_threshold, gap_alpha_threshold, gap_alpha_threshold),
  rho_threshold = c(NA_real_, gap_rho_threshold, gap_rho_threshold)
)
existence_screen[, inside_bmz_existence_region :=
  alpha_proxy > alpha_threshold & rho_proxy > rho_threshold]
existence_screen[screen == "Exact paired prompt assignment",
  inside_bmz_existence_region := FALSE]
existence_screen[, interpretation := c(
  paste(
    "Both labels occur in every meeting and there is no serial assignment state;",
    "phi=0 implies an alpha threshold of one, outside the determinate BMZ model."
  ),
  paste(
    "Diagnostic proxy only: alpha is prior pass-through, phi is realized tone-gap",
    "persistence, and rho is persistence of the first macro-news principal component."
  ),
  paste(
    "Conservative diagnostic replaces PC1 persistence with the largest AR(1)",
    "among the five measured pre-meeting macro-news series."
  )
)]

macro_persistence <- data.table(
  macro_proxy = c("PC1", names(rho_components)),
  ar1 = c(rho_pc, rho_components)
)

dbWithTransaction(con, {
  dbWriteTable(
    con,
    "referee_sunspot_variance_ordering",
    as.data.frame(variance_table),
    overwrite = TRUE
  )
  dbWriteTable(
    con,
    "referee_sunspot_existence_screen",
    as.data.frame(existence_screen),
    overwrite = TRUE
  )
})

write_table <- function(data, name) {
  path <- file.path(table_dir, paste0(name, "_", stamp, ".csv"))
  fwrite(data, path)
  path
}

outputs <- list(
  variance = write_table(
    variance_table,
    "referee_d10_sunspot_variance_ordering"
  ),
  ordering = write_table(
    ordering_table,
    "referee_d10_sunspot_order_bootstrap"
  ),
  existence = write_table(
    existence_screen,
    "referee_d10_sunspot_existence_screen"
  ),
  alpha = write_table(alpha_rows, "referee_d10_sunspot_alpha_proxy"),
  persistence = write_table(
    macro_persistence,
    "referee_d10_sunspot_macro_persistence"
  )
)

summary_path <- file.path(
  table_dir,
  paste0("referee_d10_sunspot_", stamp, ".json")
)
write_json(
  release_metadata(list(
    generated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    implementation = list(
      language = "R",
      packages = c("RSQLite", "data.table", "sandwich", "lmtest"),
      bootstrap_draws = bootstrap_draws,
      bootstrap_unit = "meeting"
    ),
    variance_ordering = variance_table,
    ordering_test = ordering_table,
    alpha_proxy = alpha_rows,
    realized_tone_gap_ar1 = list(
      estimate = phi_gap,
      newey_west_se = phi_gap_se,
      observations = nrow(tone_gap)
    ),
    macro_persistence = macro_persistence,
    existence_screen = existence_screen,
    caveat = paste(
      "The variance exercise is a laboratory analogue, not an estimate of a",
      "structural restricted-perceptions equilibrium. The exact assignment-design",
      "screen is the decisive design-based result."
    ),
    outputs = outputs
  ), root, stamp),
  summary_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  digits = 16
)

cat(sprintf("Completed D10 sunspot diagnostics: %s\n", summary_path))
