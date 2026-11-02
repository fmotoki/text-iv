# Read the explicit run authority before opening any artifact.
source_root <- normalizePath(getwd(), winslash = "/", mustWork = TRUE)
source(file.path(source_root, "code", "read_run_spec.R"), local = TRUE)

# The neutral dataset name must still identify this exact model and specification.
calibration_audit_path <- run_path("outputs/work/calibration_audit.json")
if (!file.exists(spec$paths$calibration_data) || !file.exists(calibration_audit_path)) {
  stop("Selected Terra calibration is absent. Production collection/calibration has not run")
}
calibration_audit <- jsonlite::fromJSON(calibration_audit_path, simplifyVector = TRUE)
if (!identical(calibration_audit$fingerprint, spec$fingerprint) ||
    !identical(calibration_audit$spec_sha256,spec_sha256) ||
    !identical(calibration_audit$original_run,spec$run_ids$original_priors) ||
    !identical(calibration_audit$main_text_run,spec$run_ids$main_text) ||
    !identical(calibration_audit$model, spec$model) ||
    !identical(calibration_audit$reasoning_effort, spec$reasoning_effort) ||
    !identical(as.integer(calibration_audit$meetings), as.integer(spec$sample$meeting_numbers)) ||
    !identical(calibration_audit$calibration_sha256,
      digest::digest(spec$paths$calibration_data, algo = "sha256", file = TRUE, serialize = FALSE))) {
  stop("Calibration provenance, membership or bytes do not match this run")
}

suppressPackageStartupMessages({
  library(data.table)
  library(haven)
  library(rstan)
  library(bridgesampling)
  library(jsonlite)
})

data.table::setDTthreads(1L)
rstan::rstan_options(auto_write = FALSE)
options(mc.cores = 2L)

source(file.path(source_root, "code", "02_analysis", "reproducibility_helpers.R"), local = TRUE)
dataset_path <- spec$paths$calibration_data
table_dir <- run_path("outputs/tables")
model_dir <- run_path("outputs/models")
dir.create(table_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(model_dir, recursive = TRUE, showWarnings = FALSE)

stamp <- result_stamp
seed <- 20260715L

data <- as.data.table(read_dta(dataset_path))
setorder(data, reuniao)
data[, lag2_inflacao12m := shift(inflacao12m, 2L)]

stan_code <- "
data {
  int<lower=1> N;
  int<lower=0> K;
  vector[N] y;
  matrix[N, K] X;
}
parameters {
  real alpha;
  vector[K] beta;
  real<lower=0> sigma;
}
model {
  alpha ~ normal(0, 1);
  beta ~ normal(0, 1);
  sigma ~ student_t(3, 0, 1);
  y ~ normal(alpha + X * beta, sigma);
}
generated quantities {
  vector[N] log_lik;
  for (n in 1:N) {
    log_lik[n] = normal_lpdf(y[n] | alpha + X[n] * beta, sigma);
  }
}
"
stan_model_fit <- stan_model(model_code = stan_code, model_name = "posterior_tournament_lm")

scale_if_variable <- function(x) {
  s <- sd(x)
  if (!is.finite(s) || s == 0) return(x - mean(x))
  as.numeric((x - mean(x)) / s)
}

prepare_predictor <- function(x, name) {
  if (name %in% c("anchored_centered", "unanchored_centered")) {
    return(as.numeric(x))
  }
  scale_if_variable(x)
}

fit_candidate <- function(panel, name, frame, response, predictors, model_seed) {
  used <- frame[, c(response, predictors), with = FALSE]
  used <- used[complete.cases(used)]
  y <- used[[response]] - mean(used[[response]])
  if (length(predictors)) {
    x <- do.call(cbind, Map(
      prepare_predictor,
      used[, ..predictors],
      predictors
    ))
    colnames(x) <- predictors
  } else {
    x <- matrix(numeric(0), nrow = nrow(used), ncol = 0L)
  }
  fit <- sampling(
    stan_model_fit,
    data = list(N = nrow(used), K = ncol(x), y = y, X = x),
    seed = model_seed,
    chains = 4L,
    iter = 2000L,
    warmup = 1000L,
    cores = 2L,
    refresh = 0L,
    control = list(adapt_delta = 0.95, max_treedepth = 11L)
  )
  set.seed(model_seed + 500000L)
  bridge <- bridge_sampler(fit, silent = TRUE)
  diagnostics <- summary(fit, pars = c("alpha", "beta", "sigma"))$summary
  list(
    row = data.table(
      panel = panel,
      model = name,
      observations = nrow(used),
      predictors = if (length(predictors)) paste(predictors, collapse = " + ") else "intercept only",
      log_marginal_likelihood = bridge$logml,
      max_rhat = max(diagnostics[, "Rhat"], na.rm = TRUE),
      min_effective_sample_size = min(diagnostics[, "n_eff"], na.rm = TRUE)
    ),
    fit = fit
  )
}

# Every Focus candidate uses the same 77-meeting support so that marginal
# likelihoods are comparable when the AR(2) model enters the tournament.
focus <- copy(data[complete.cases(
  inflacao12m,
  l_inflacao12m,
  lag2_inflacao12m,
  score_orig,
  ibcbr_agro,
  ibcbr_industry,
  ibcbr_service
)])
focus_specs <- list(
  # All candidates below retain the same complete two-lag support.
  list("No tone, AR(1)", c("l_inflacao12m")),
  list("Tone, no persistence", c("score_orig")),
  list("Tone and AR(1)", c("score_orig", "l_inflacao12m")),
  list("Tone and AR(2)", c("score_orig", "l_inflacao12m", "lag2_inflacao12m")),
  list(
    "Tone, AR(1), and activity controls",
    c("score_orig", "l_inflacao12m", "ibcbr_agro", "ibcbr_industry", "ibcbr_service")
  )
)

if (nrow(focus)!=length(spec$sample$meeting_numbers)-2L) {
  stop("Posterior Focus tournament must retain the common 77-meeting support")
}
focus_results <- lapply(seq_along(focus_specs), function(i) {
  fit_candidate(
    "Focus projection",
    focus_specs[[i]][[1]],
    focus,
    "inflacao12m",
    focus_specs[[i]][[2]],
    seed + i
  )
})

mixture <- copy(data[complete.cases(
  inflacao12m,
  forecast_anchored,
  forecast_unanchored,
  ibcbr_agro,
  ibcbr_industry,
  ibcbr_service
)])
mixture[, `:=`(
  anchored_centered = forecast_anchored - mean(forecast_anchored),
  unanchored_centered = forecast_unanchored - mean(forecast_unanchored),
  equal_weight_offset = inflacao12m -
    0.5 * (forecast_anchored + forecast_unanchored)
)]

control_names <- c("ibcbr_agro", "ibcbr_industry", "ibcbr_service")
mixture_specs <- list(
  list("Fundamentals only", "inflacao12m", control_names),
  list(
    "Anchored agent plus fundamentals",
    "inflacao12m",
    c("anchored_centered", control_names)
  ),
  list(
    "Unanchored agent plus fundamentals",
    "inflacao12m",
    c("unanchored_centered", control_names)
  ),
  list(
    "Unconstrained two-agent projection",
    "inflacao12m",
    c("anchored_centered", "unanchored_centered", control_names)
  ),
  list("Equal-weight constrained projection", "equal_weight_offset", control_names)
)

mixture_results <- lapply(seq_along(mixture_specs), function(i) {
  fit_candidate(
    "Synthetic calibration",
    mixture_specs[[i]][[1]],
    mixture,
    mixture_specs[[i]][[2]],
    mixture_specs[[i]][[3]],
    seed + 100L + i
  )
})

tournament <- rbindlist(c(
  lapply(focus_results, `[[`, "row"),
  lapply(mixture_results, `[[`, "row")
))

d6_summary_path <- file.path(
  table_dir,
  paste0("referee_d6_bayesian_calibration_", stamp, ".json")
)
if (!file.exists(d6_summary_path)) {
  stop("Run scripts/run_referee_d6_bayesian_calibration.R before D11")
}
d6_summary <- read_json(d6_summary_path, simplifyVector = TRUE)
tournament <- rbind(
  tournament,
  data.table(
    panel = "Synthetic calibration",
    model = "Hierarchical Dirichlet constrained projection",
    observations = 79L,
    predictors = "simplex(anchored, unanchored) + activity controls",
    log_marginal_likelihood = d6_summary$hierarchical_dirichlet$log_marginal_likelihood,
    max_rhat = max(d6_summary$hierarchical_dirichlet$summary$max_rhat),
    min_effective_sample_size = min(
      d6_summary$hierarchical_dirichlet$summary$min_effective_sample_size
    )
  ),
  fill = TRUE
)

classify_kass_raftery <- function(two_log_bf) {
  fifelse(
    two_log_bf < 2, "Not worth more than a bare mention",
    fifelse(
      two_log_bf < 6, "Positive evidence against model",
      fifelse(two_log_bf < 10, "Strong evidence against model", "Very strong evidence against model")
    )
  )
}

tournament[, `:=`(
  best_log_marginal_likelihood = max(log_marginal_likelihood),
  log_bayes_factor_vs_best = log_marginal_likelihood - max(log_marginal_likelihood)
), by = panel]
tournament[, `:=`(
  two_log_bf_best_over_model = -2 * log_bayes_factor_vs_best,
  posterior_model_probability = exp(log_marginal_likelihood -
    max(log_marginal_likelihood)) /
    sum(exp(log_marginal_likelihood - max(log_marginal_likelihood)))
), by = panel]
tournament[, kass_raftery := classify_kass_raftery(two_log_bf_best_over_model)]
setorder(tournament, panel, -log_marginal_likelihood)

model_path <- file.path(model_dir, paste0("referee_d11_posterior_tournament_", stamp, ".rds"))
table_path <- file.path(table_dir, paste0("referee_d11_posterior_tournament_", stamp, ".csv"))
summary_path <- file.path(table_dir, paste0("referee_d11_posterior_tournament_", stamp, ".json"))

saveRDS(
  list(
    focus = lapply(focus_results, `[[`, "fit"),
    mixture = lapply(mixture_results, `[[`, "fit")
  ),
  model_path,
  compress = "xz"
)
fwrite(tournament, table_path)
write_json(
  release_metadata(list(
    generated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
    implementation = list(
      language = "R",
      packages = c("rstan", "bridgesampling"),
      prior = "Proper N(0,1) priors on standardized predictors and half-t residual scale"
    ),
    tournament = tournament,
    outputs = list(csv = table_path, models_rds = model_path)
  ), root, stamp),
  summary_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  digits = 16
)

cat(sprintf("Completed D11 posterior tournament: %s\n", summary_path))
