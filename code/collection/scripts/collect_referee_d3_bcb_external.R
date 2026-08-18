suppressPackageStartupMessages({
  library(data.table)
  library(haven)
  library(jsonlite)
})

source(file.path("scripts", "referee_econometrics_common.R"))

args <- parse_cli_args(list(
  root = getwd(),
  stamp = "20260630",
  start_date = NA_character_,
  end_date = NA_character_
))

root <- normalizePath(args$root, winslash = "/", mustWork = TRUE)
dataset_path <- file.path(root, "outputs", "work", "gpt55_calibration_data.dta")
raw_dir <- file.path(root, "data", "raw", "external_bcb", "referee_d3")
interim_dir <- file.path(root, "data", "interim", "external_bcb", "referee_d3")
out_dir <- file.path(root, "outputs", "tables")
dir.create(raw_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(interim_dir, recursive = TRUE, showWarnings = FALSE)

df <- read_stata_df(dataset_path)
df$date_daily <- as.Date(df$date_daily)
start_date <- if (!is.na(args$start_date)) as.Date(args$start_date) else min(df$date_daily) - 60
end_date <- if (!is.na(args$end_date)) as.Date(args$end_date) else max(df$date_daily) + 60

fmt_bcb_date <- function(x) format(as.Date(x), "%d/%m/%Y")

read_bcb_json <- function(url) {
  con <- url(url, open = "rb")
  on.exit(close(con), add = TRUE)
  txt <- rawToChar(readBin(con, what = "raw", n = 5e7))
  txt <- sub("^\ufeff", "", txt)
  txt <- sub("^\\xEF\\xBB\\xBF", "", txt)
  if (grepl("^\\s*<", txt)) {
    stop(paste0("BCB endpoint returned XML/HTML instead of JSON. URL: ", url, "\nFirst bytes: ", substr(txt, 1, 300)))
  }
  jsonlite::fromJSON(txt, flatten = TRUE)
}

download_sgs <- function(code, name, start_date, end_date) {
  url <- paste0(
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.", code,
    "/dados?formato=json&dataInicial=", fmt_bcb_date(start_date),
    "&dataFinal=", fmt_bcb_date(end_date)
  )
  x <- read_bcb_json(url)
  if (length(x) == 0) {
    out <- data.frame(date = as.Date(character()), value = numeric(), series_code = integer(), series_name = character())
  } else {
    out <- data.frame(
      date = as.Date(x$data, format = "%d/%m/%Y"),
      value = as.numeric(x$valor),
      series_code = as.integer(code),
      series_name = name,
      stringsAsFactors = FALSE
    )
  }
  write_csv_safe(out, file.path(raw_dir, paste0("sgs_", code, "_", name, ".csv")))
  out
}

odata_get_page <- function(entity, filter, select = NULL, orderby = "Data asc", top = 5000, skip = 0) {
  base <- paste0("https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/", entity)
  params <- c(
    paste0("%24top=", top),
    paste0("%24skip=", skip),
    paste0("%24filter=", utils::URLencode(filter, reserved = TRUE)),
    paste0("%24orderby=", utils::URLencode(orderby, reserved = TRUE)),
    "%24format=json"
  )
  if (!is.null(select)) {
    params <- c(params, paste0("%24select=", utils::URLencode(select, reserved = TRUE)))
  }
  paste0(base, "?", paste(params, collapse = "&"))
}

odata_get_all <- function(entity, filter, select = NULL, orderby = "Data asc", top = 5000, max_pages = 20) {
  rows <- list()
  for (page in seq_len(max_pages)) {
    url_page <- odata_get_page(entity, filter, select, orderby, top, skip = (page - 1) * top)
    res <- read_bcb_json(url_page)
    if (!is.null(res$value) && length(res$value) > 0) rows[[length(rows) + 1]] <- as.data.frame(res$value)
    n_page <- if (is.null(res$value)) 0 else nrow(as.data.frame(res$value))
    if (n_page < top) break
  }
  if (!length(rows)) return(data.frame())
  do.call(rbind, rows)
}

last_available <- function(series_df, dates, value_col = "value") {
  if (nrow(series_df) == 0) return(rep(NA_real_, length(dates)))
  series_df <- series_df[order(series_df$date), , drop = FALSE]
  vapply(dates, function(d) {
    idx <- max(which(series_df$date <= d), na.rm = TRUE)
    if (!is.finite(idx) || length(idx) == 0) return(NA_real_)
    as.numeric(series_df[[value_col]][idx])
  }, numeric(1))
}

series <- list(
  selic_meta = download_sgs(432, "selic_meta", start_date, end_date),
  selic_over = download_sgs(11, "selic_over", start_date, end_date),
  selic_annualized_252 = download_sgs(1178, "selic_annualized_252", start_date, end_date),
  primary_result_12m_gdp = download_sgs(5783, "primary_result_12m_gdp", start_date, end_date),
  dbgg_gdp = download_sgs(13762, "dbgg_gdp", start_date, end_date)
)

focus_filter <- paste0(
  "Indicador eq 'IPCA' and Suavizada eq 'N' and baseCalculo eq 0",
  " and Data ge '", format(start_date, "%Y-%m-%d"), "'",
  " and Data le '", format(end_date, "%Y-%m-%d"), "'"
)
focus_ipca12 <- odata_get_all(
  "ExpectativasMercadoInflacao12Meses",
  focus_filter,
  select = "Indicador,Data,Suavizada,Media,Mediana,DesvioPadrao,numeroRespondentes,baseCalculo"
)
if (nrow(focus_ipca12) > 0) {
  focus_ipca12$Data <- as.Date(focus_ipca12$Data)
  names(focus_ipca12)[names(focus_ipca12) == "Data"] <- "date"
  focus_ipca12$Media <- as.numeric(focus_ipca12$Media)
  focus_ipca12$Mediana <- as.numeric(focus_ipca12$Mediana)
  focus_ipca12$DesvioPadrao <- as.numeric(focus_ipca12$DesvioPadrao)
}
write_csv_safe(focus_ipca12, file.path(raw_dir, "focus_ipca_12m_expectations.csv"))

focus_selic_filter <- paste0(
  "Indicador eq 'Selic' and baseCalculo eq 0",
  " and Data ge '", format(start_date, "%Y-%m-%d"), "'",
  " and Data le '", format(end_date, "%Y-%m-%d"), "'"
)
focus_selic <- odata_get_all(
  "ExpectativasMercadoSelic",
  focus_selic_filter,
  select = "Indicador,Data,Reuniao,Media,Mediana,DesvioPadrao,numeroRespondentes,baseCalculo"
)
if (nrow(focus_selic) > 0) {
  focus_selic$Data <- as.Date(focus_selic$Data)
  names(focus_selic)[names(focus_selic) == "Data"] <- "date"
  focus_selic$Media <- as.numeric(focus_selic$Media)
  focus_selic$Mediana <- as.numeric(focus_selic$Mediana)
  focus_selic$DesvioPadrao <- as.numeric(focus_selic$DesvioPadrao)
}
write_csv_safe(focus_selic, file.path(raw_dir, "focus_selic_expectations_by_copom_meeting.csv"))

meetings <- df[, c("reuniao", "date_daily")]
meetings <- meetings[order(meetings$reuniao), , drop = FALSE]
meetings$selic_meta_bcb <- last_available(series$selic_meta, meetings$date_daily)
meetings$selic_over_bcb <- last_available(series$selic_over, meetings$date_daily)
meetings$selic_annualized_252_bcb <- last_available(series$selic_annualized_252, meetings$date_daily)
meetings$primary_result_12m_gdp_bcb <- last_available(series$primary_result_12m_gdp, meetings$date_daily)
meetings$dbgg_gdp_bcb <- last_available(series$dbgg_gdp, meetings$date_daily)

focus_ipca12_series <- if (nrow(focus_ipca12) > 0) {
  data.frame(date = focus_ipca12$date, value = focus_ipca12$Mediana)
} else {
  data.frame(date = as.Date(character()), value = numeric())
}
meetings$focus_ipca12m_mediana_bcb <- last_available(focus_ipca12_series, meetings$date_daily)
meetings$ex_ante_real_selic_simple_bcb <- meetings$selic_meta_bcb - meetings$focus_ipca12m_mediana_bcb
meetings$ex_ante_real_selic_fisher_bcb <- ((1 + meetings$selic_meta_bcb / 100) / (1 + meetings$focus_ipca12m_mediana_bcb / 100) - 1) * 100

# Exact Selic-minus-neutral requires an external neutral-rate estimate. The BCB API collection
# supplies the ex-ante real Selic proxy and leaves the neutral-rate field explicit.
meetings$neutral_real_rate_bcb <- NA_real_
meetings$selic_minus_neutral_bcb <- NA_real_

panel_path <- file.path(interim_dir, paste0("d3_bcb_external_controls_by_meeting_", args$stamp, ".csv"))
metadata_path <- file.path(out_dir, paste0("referee_d3_bcb_external_collection_", args$stamp, ".json"))
write_csv_safe(meetings, panel_path)

metadata <- list(
  status = "complete_with_non_bcb_gaps",
  implementation = "R collection from official BCB SGS API and Olinda Expectativas API.",
  sample = list(
    start_date = start_date,
    end_date = end_date,
    meetings = nrow(meetings)
  ),
  bcb_sources = list(
    sgs_api = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados",
    olinda_expectativas_api = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata",
    series = list(
      selic_meta = list(code = 432, name = "Taxa de juros - Meta Selic definida pelo Copom"),
      selic_over = list(code = 11, name = "Taxa de juros - Selic"),
      selic_annualized_252 = list(code = 1178, name = "Taxa de juros - Selic anualizada base 252"),
      primary_result_12m_gdp = list(code = 5783, name = "NFSP sem desvalorização cambial (% PIB), resultado primário, Governo Federal e Banco Central, acumulado 12m"),
      dbgg_gdp = list(code = 13762, name = "Dívida bruta do governo geral (% PIB), metodologia a partir de 2008"),
      focus_ipca12 = list(entity = "ExpectativasMercadoInflacao12Meses", filter = "IPCA, Suavizada=N, baseCalculo=0"),
      focus_selic = list(entity = "ExpectativasMercadoSelic", filter = "Selic, baseCalculo=0")
    )
  ),
  gaps = list(
    sovereign_cds = "Not found in BCB open-data catalog/API under CDS/risco-pais searches; likely needs an external market-data source.",
    neutral_rate = "No direct BCB API neutral-rate time series found. Collection provides ex-ante real Selic using Focus IPCA 12m; true Selic-minus-neutral still needs a neutral-rate estimate.",
    fiscal_news_calendar = "BCB has statistical release pages/calendar, but not a historical fiscal-news event API for the full 2016-2025 sample in this collector."
  ),
  outputs = list(
    meeting_panel_csv = panel_path,
    raw_dir = raw_dir,
    metadata_json = metadata_path
  )
)
write_json_safe(metadata, metadata_path)
cat(jsonlite::toJSON(metadata$outputs, auto_unbox = TRUE, pretty = TRUE), "\n")
