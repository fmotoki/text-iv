suppressPackageStartupMessages({
  library(DBI)
  library(RSQLite)
  library(data.table)
  library(httr2)
  library(jsonlite)
})

root <- normalizePath(".", winslash = "/", mustWork = TRUE)
database_path <- file.path(root, "data", "interim", "prompt_iv.sqlite")
fetched_at <- format(Sys.time(), tz = "UTC", usetz = TRUE)

sgs_series <- data.table(
  series_code = c(7478L, 24363L, 24364L, 10813L, 13522L, 433L, 432L),
  series_name = c(
    "IPCA-15 monthly change",
    "IBC-Br",
    "IBC-Br seasonally adjusted",
    "BRL per USD buying rate",
    "IPCA accumulated over 12 months",
    "IPCA monthly change",
    "Selic target set by Copom"
  )
)

fetch_json <- function(url, attempts = 4L) {
  for (attempt in seq_len(attempts)) {
    response <- request(url) |>
      req_user_agent("Prompt-IV referee replication; academic research") |>
      req_retry(max_tries = attempts) |>
      req_timeout(60) |>
      req_perform()
    resp_check_status(response)
    body <- resp_body_string(response)
    if (grepl("^\\s*[\\[{]", body)) {
      return(fromJSON(body, simplifyVector = TRUE))
    }
    if (attempt < attempts) Sys.sleep(attempt)
  }
  stop(sprintf("BCB endpoint did not return JSON after %d attempts: %s", attempts, url))
}

fetch_sgs <- function(code, name) {
  url <- sprintf(
    paste0(
      "https://api.bcb.gov.br/dados/serie/bcdata.sgs.%s/dados",
      "?formato=json&dataInicial=01/01/2016&dataFinal=31/12/2025"
    ),
    code
  )
  payload <- fetch_json(url)
  out <- as.data.table(payload)
  setnames(out, c("data", "valor"), c("date", "value"))
  out[, `:=`(
    series_code = as.integer(code),
    series_name = name,
    date = as.character(as.IDate(date, format = "%d/%m/%Y")),
    value = as.numeric(value),
    source_url = url,
    fetched_at = fetched_at
  )]
  out[, .(series_code, series_name, date, value, source_url, fetched_at)]
}

odata_url <- "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata"

fetch_odata <- function(entity, filter) {
  endpoint <- paste(odata_url, entity, sep = "/")
  pages <- list()
  page_size <- 10000L
  skip <- 0L
  source_urls <- character()
  repeat {
    query <- list(
      request(endpoint),
      `$filter` = filter,
      `$orderby` = "Data asc",
      `$top` = page_size,
      `$skip` = skip,
      `$format` = "json"
    )
    response <- do.call(req_url_query, query) |>
      req_user_agent("Prompt-IV referee replication; academic research") |>
      req_retry(max_tries = 4L) |>
      req_timeout(90) |>
      req_perform()
    resp_check_status(response)
    payload <- resp_body_json(response, simplifyVector = TRUE)
    page <- as.data.table(payload$value)
    pages[[length(pages) + 1L]] <- page
    source_urls <- c(source_urls, resp_url(response))
    if (nrow(page) < page_size) break
    skip <- skip + page_size
    if (skip > 1000000L) stop(sprintf("Pagination safety limit reached for %s", entity))
  }
  rows <- rbindlist(pages, fill = TRUE)
  attr(rows, "source_url") <- paste(source_urls, collapse = " | ")
  rows
}

pick <- function(data, candidates, default = NA) {
  name <- candidates[candidates %in% names(data)][1]
  if (is.na(name)) rep(default, nrow(data)) else data[[name]]
}

standardize_focus <- function(data, entity) {
  source_url <- attr(data, "source_url")
  data.table(
    source_entity = entity,
    indicator = as.character(pick(data, c("Indicador", "indicador"))),
    date = as.character(as.IDate(pick(data, "Data"))),
    reference_period = as.character(pick(data, "DataReferencia")),
    meeting = as.character(pick(data, c("Reuniao", "reuniao"))),
    calculation_type = as.character(pick(data, c("tipoCalculo", "TipoCalculo"))),
    smoothed = as.character(pick(data, "Suavizada")),
    mean = as.numeric(pick(data, c("Media", "media"))),
    median = as.numeric(pick(data, c("Mediana", "mediana"))),
    sd = as.numeric(pick(data, c("DesvioPadrao", "desvioPadrao"))),
    min = as.numeric(pick(data, c("Minimo", "minimo"))),
    max = as.numeric(pick(data, c("Maximo", "maximo"))),
    respondents = as.integer(pick(data, "numeroRespondentes")),
    calculation_base = as.integer(pick(data, "baseCalculo")),
    source_url = source_url,
    fetched_at = fetched_at
  )
}

date_filter <- "Data ge '2016-01-01' and Data le '2025-12-31'"
focus_queries <- list(
  ExpectativasMercadoInflacao12Meses = paste(
    "Indicador eq 'IPCA' and Suavizada eq 'N' and baseCalculo eq 0 and",
    date_filter
  ),
  ExpectativasMercadoTop5Inflacao12Meses = paste(
    "Indicador eq 'IPCA' and Suavizada eq 'N' and", date_filter
  ),
  ExpectativasMercadoSelic = paste("baseCalculo eq 0 and", date_filter),
  ExpectativasMercadoTop5Selic = date_filter,
  ExpectativasMercadoAnuais = paste(
    "Indicador eq 'IPCA' and baseCalculo eq 0 and", date_filter
  ),
  ExpectativasMercadoTop5Anuais = paste("Indicador eq 'IPCA' and", date_filter)
)

cat("Collecting BCB SGS series...\n")
sgs <- rbindlist(Map(fetch_sgs, sgs_series$series_code, sgs_series$series_name))

cat("Collecting BCB Focus aggregates and Top-5 statistics...\n")
focus <- rbindlist(lapply(names(focus_queries), function(entity) {
  standardize_focus(fetch_odata(entity, focus_queries[[entity]]), entity)
}), fill = TRUE)

manifest <- data.table(
  source_id = c(
    paste0("BCB_SGS_", sgs_series$series_code),
    names(focus_queries),
    "ExpectativasMercadoInstituicoes"
  ),
  source_type = c(
    rep("BCB_SGS", nrow(sgs_series)),
    rep("BCB_OLINDA", length(focus_queries)),
    "BCB_OLINDA_DEACTIVATED"
  ),
  status = c(
    rep("collected", nrow(sgs_series) + length(focus_queries)),
    "unavailable"
  ),
  note = c(
    sgs_series$series_name,
    rep("Official Focus aggregate or Top-5 statistics", length(focus_queries)),
    paste(
      "Official institution-level endpoint deactivated by BCB because of",
      "microdata confidentiality risk; aggregate and Top-5 endpoints retained."
    )
  ),
  source_url = c(
    sprintf(
      "https://api.bcb.gov.br/dados/serie/bcdata.sgs.%s/dados",
      sgs_series$series_code
    ),
    paste(odata_url, names(focus_queries), sep = "/"),
    paste(odata_url, "ExpectativasMercadoInstituicoes", sep = "/")
  ),
  fetched_at = fetched_at
)

con <- dbConnect(SQLite(), database_path)
on.exit(dbDisconnect(con), add = TRUE)
dbExecute(con, "PRAGMA busy_timeout = 60000")

dbWithTransaction(con, {
  dbExecute(con, paste(
    "CREATE TABLE IF NOT EXISTS external_bcb_sgs (",
    "series_code INTEGER NOT NULL, series_name TEXT NOT NULL, date TEXT NOT NULL,",
    "value REAL, source_url TEXT NOT NULL, fetched_at TEXT NOT NULL,",
    "PRIMARY KEY (series_code, date))"
  ))
  dbExecute(con, paste(
    "CREATE TABLE IF NOT EXISTS external_focus_statistics (",
    "source_entity TEXT NOT NULL, indicator TEXT, date TEXT NOT NULL,",
    "reference_period TEXT, meeting TEXT, calculation_type TEXT, smoothed TEXT,",
    "mean REAL, median REAL, sd REAL, min REAL, max REAL, respondents INTEGER,",
    "calculation_base INTEGER, source_url TEXT NOT NULL, fetched_at TEXT NOT NULL)"
  ))
  dbExecute(con, paste(
    "CREATE INDEX IF NOT EXISTS idx_external_focus_source_date",
    "ON external_focus_statistics(source_entity, date)"
  ))
  dbExecute(con, paste(
    "CREATE TABLE IF NOT EXISTS external_source_manifest (",
    "source_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, status TEXT NOT NULL,",
    "note TEXT, source_url TEXT NOT NULL, fetched_at TEXT NOT NULL)"
  ))

  dbExecute(con, "DELETE FROM external_bcb_sgs WHERE series_code IN (7478,24363,24364,10813,13522,433,432)")
  dbWriteTable(con, "external_bcb_sgs", as.data.frame(sgs), append = TRUE)

  placeholders <- paste(rep("?", length(focus_queries)), collapse = ",")
  dbExecute(
    con,
    sprintf("DELETE FROM external_focus_statistics WHERE source_entity IN (%s)", placeholders),
    params = as.list(unname(names(focus_queries)))
  )
  dbWriteTable(con, "external_focus_statistics", as.data.frame(focus), append = TRUE)

  for (source_id in manifest$source_id) {
    dbExecute(
      con,
      "DELETE FROM external_source_manifest WHERE source_id = ?",
      params = list(source_id)
    )
  }
  dbWriteTable(con, "external_source_manifest", as.data.frame(manifest), append = TRUE)
})

cat(sprintf(
  "Stored %s SGS rows and %s Focus rows directly in %s.\n",
  format(nrow(sgs), big.mark = ","),
  format(nrow(focus), big.mark = ","),
  database_path
))
