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
  library(glmnet)
  library(boot)
  library(loo)
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

variables <- c(
  "inflacao12m", "forecast_anchored", "forecast_unanchored",
  "ibcbr_agro", "ibcbr_industry", "ibcbr_service"
)
data <- as.data.table(read_dta(dataset_path))[, ..variables]
data <- data[complete.cases(data)]

controls <- c("ibcbr_agro", "ibcbr_industry", "ibcbr_service")
scale_vector <- function(x) as.numeric(scale(x))

stan_data <- list(
  N = nrow(data),
  P = length(controls),
  y = data$inflacao12m - mean(data$inflacao12m),
  anchored = data$forecast_anchored - mean(data$forecast_anchored),
  unanchored = data$forecast_unanchored - mean(data$forecast_unanchored),
  X = do.call(cbind, lapply(data[, ..controls], scale_vector))
)

stan_code <- "
data {
  int<lower=1> N;
  int<lower=1> P;
  vector[N] y;
  vector[N] anchored;
  vector[N] unanchored;
  matrix[N, P] X;
}
parameters {
  simplex[2] phi;
  real alpha;
  vector[P] beta;
  real<lower=0> sigma;
  real<lower=0.05, upper=50> dirichlet_concentration;
}
model {
  vector[N] mu;
  dirichlet_concentration ~ gamma(2, 1);
  phi ~ dirichlet(rep_vector(dirichlet_concentration, 2));
  alpha ~ normal(0, 1);
  beta ~ normal(0, 1);
  sigma ~ student_t(3, 0, 1);
  mu = alpha + phi[1] * anchored + phi[2] * unanchored + X * beta;
  y ~ normal(mu, sigma);
}
generated quantities {
  vector[N] log_lik;
  vector[N] y_rep;
  vector[N] mu;
  mu = alpha + phi[1] * anchored + phi[2] * unanchored + X * beta;
  for (n in 1:N) {
    log_lik[n] = normal_lpdf(y[n] | mu[n], sigma);
    y_rep[n] = normal_rng(mu[n], sigma);
  }
}
"

model_path <- file.path(model_dir, paste0("referee_d6_hierarchical_dirichlet_", stamp, ".rds"))
fit <- sampling(
  stan_model(model_code = stan_code, model_name = "hierarchical_dirichlet_mixture"),
  data = stan_data,
  seed = seed,
  chains = 4L,
  iter = 4000L,
  warmup = 2000L,
  thin = 1L,
  cores = 2L,
  refresh = 250L,
  control = list(adapt_delta = 0.98, max_treedepth = 12L)
)
saveRDS(fit, model_path, compress = "xz")

draws <- rstan::extract(
  fit,
  pars = c("phi", "dirichlet_concentration", "sigma", "log_lik"),
  permuted = TRUE
)
summarize_draw <- function(x) {
  data.table(
    mean = mean(x),
    sd = sd(x),
    q025 = unname(quantile(x, 0.025)),
    median = unname(quantile(x, 0.5)),
    q975 = unname(quantile(x, 0.975))
  )
}

stan_summary <- as.data.table(
  summary(
    fit,
    pars = c("phi", "dirichlet_concentration", "sigma"),
    probs = c(0.025, 0.5, 0.975)
  )$summary,
  keep.rownames = "parameter"
)

hierarchical_table <- rbindlist(list(
  cbind(parameter = "Anchored weight", summarize_draw(draws$phi[, 1])),
  cbind(parameter = "Unanchored weight", summarize_draw(draws$phi[, 2])),
  cbind(
    parameter = "Dirichlet concentration",
    summarize_draw(draws$dirichlet_concentration)
  ),
  cbind(parameter = "Residual sigma", summarize_draw(draws$sigma))
), fill = TRUE)
hierarchical_table[, `:=`(
  observations = nrow(data),
  posterior_draws = length(draws$sigma),
  max_rhat = max(stan_summary$Rhat, na.rm = TRUE),
  min_effective_sample_size = min(stan_summary$n_eff, na.rm = TRUE)
)]

loo_result <- loo(draws$log_lik)
set.seed(seed + 5000L)
bridge_result <- bridge_sampler(fit, silent = TRUE)

# Empirical-Bayes ridge centered on equal weights. The selected penalty is
# converted to its Gaussian-prior scale using the glmnet objective.
ridge_data <- data[, .(
    y_centered = inflacao12m - forecast_unanchored -
      0.5 * (forecast_anchored - forecast_unanchored),
    agent_difference = forecast_anchored - forecast_unanchored,
  ibcbr_agro,
  ibcbr_industry,
  ibcbr_service
)]
ridge_predictors <- setdiff(names(ridge_data), "y_centered")
x_ridge <- as.matrix(ridge_data[, ..ridge_predictors])
set.seed(seed + 1L)
ridge_cv <- cv.glmnet(
  x = x_ridge,
  y = ridge_data$y_centered,
  alpha = 0,
  nfolds = nrow(ridge_data),
  foldid = seq_len(nrow(ridge_data)),
  penalty.factor = c(1, 0, 0, 0),
  standardize = TRUE,
  grouped = FALSE
)
ridge_fit <- glmnet(
  x = x_ridge,
  y = ridge_data$y_centered,
  alpha = 0,
  lambda = ridge_cv$lambda.min,
  penalty.factor = c(1, 0, 0, 0),
  standardize = TRUE
)
delta_hat <- unname(as.matrix(coef(ridge_fit))["agent_difference", 1])
phi_a_hat <- 0.5 + delta_hat

ridge_boot_stat <- function(frame, indices) {
  sample <- frame[indices]
  x <- as.matrix(sample[, ..ridge_predictors])
  fit_b <- glmnet(
    x = x,
    y = sample$y_centered,
    alpha = 0,
    lambda = ridge_cv$lambda.min,
    penalty.factor = c(1, 0, 0, 0),
    standardize = TRUE
  )
  0.5 + unname(as.matrix(coef(fit_b))["agent_difference", 1])
}
set.seed(seed + 2L)
ridge_boot <- boot(ridge_data, ridge_boot_stat, R = 1999L)
ridge_ci <- quantile(ridge_boot$t[, 1], c(0.025, 0.5, 0.975), na.rm = TRUE)
ridge_sd <- sd(ridge_boot$t[, 1], na.rm = TRUE)
ridge_sigma <- sqrt(mean((ridge_data$y_centered - as.numeric(predict(
  ridge_fit, newx = x_ridge, s = ridge_cv$lambda.min
)))^2))
implied_prior_sd <- ridge_sigma / sqrt(nrow(ridge_data) * ridge_cv$lambda.min)

empirical_bayes_table <- data.table(
  specification = "Empirical-Bayes ridge centered on equal weights",
  anchored_weight = phi_a_hat,
  unanchored_weight = 1 - phi_a_hat,
  anchored_std_error = ridge_sd,
  unanchored_std_error = ridge_sd,
  anchored_ci_low = unname(ridge_ci[[1]]),
  anchored_ci_high = unname(ridge_ci[[3]]),
  unanchored_ci_low = 1 - unname(ridge_ci[[3]]),
  unanchored_ci_high = 1 - unname(ridge_ci[[1]]),
  selected_lambda = ridge_cv$lambda.min,
  implied_gaussian_prior_sd = implied_prior_sd,
  bootstrap_replications = nrow(ridge_boot$t),
  observations = nrow(ridge_data)
)

hierarchical_path <- file.path(
  table_dir,
  paste0("referee_d6_hierarchical_dirichlet_", stamp, ".csv")
)
diagnostic_path <- file.path(
  table_dir,
  paste0("referee_d6_hierarchical_dirichlet_diagnostics_", stamp, ".csv")
)
eb_path <- file.path(
  table_dir,
  paste0("referee_d6_empirical_bayes_", stamp, ".csv")
)
summary_path <- file.path(
  table_dir,
  paste0("referee_d6_bayesian_calibration_", stamp, ".json")
)

fwrite(hierarchical_table, hierarchical_path)
fwrite(stan_summary, diagnostic_path)
fwrite(empirical_bayes_table, eb_path)

payload <- list(
  generated_at = format(Sys.time(), tz = "UTC", usetz = TRUE),
  implementation = list(
    language = "R",
    packages = c("rstan", "loo", "bridgesampling", "glmnet", "boot"),
    model = paste(
      "Two-weight simplex with a learned symmetric Dirichlet concentration;",
      "no word-count control."
    )
  ),
  hierarchical_dirichlet = list(
    summary = hierarchical_table,
    probability_unanchored_above_0_9 = mean(draws$phi[, 2] > 0.9),
    probability_anchored_above_0 = mean(draws$phi[, 1] > 0),
    loo = as.data.frame(loo_result$estimates),
    log_marginal_likelihood = bridge_result$logml
  ),
  empirical_bayes = empirical_bayes_table,
  outputs = list(
    hierarchical_csv = hierarchical_path,
    diagnostics_csv = diagnostic_path,
    empirical_bayes_csv = eb_path,
    stan_fit_rds = model_path
  )
)
write_json(
  release_metadata(payload, root, stamp),
  summary_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  digits = 16
)

cat(sprintf("Completed D6 Bayesian calibration: %s\n", summary_path))
