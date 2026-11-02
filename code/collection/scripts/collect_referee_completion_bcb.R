# Import frozen BCB extracts offline. Acquisition belongs to download_minutes.py.
suppressPackageStartupMessages({library(DBI);library(RSQLite);library(data.table);library(jsonlite);library(digest)})
spec_path <- Sys.getenv("REPLICATION_SPEC_PATH")
if (!nzchar(spec_path)) stop("REPLICATION_SPEC_PATH is required")
source_root <- fromJSON(spec_path)$source_root
source(file.path(source_root,"code","read_run_spec.R"))
args <- commandArgs(trailingOnly=TRUE)
if (length(args) && (length(args) %% 2L || any(!args[seq.int(1L,length(args),by=2L)] %in% c("--database","--snapshot")))) stop("Use --database PATH --snapshot PATH, or specification defaults")
database_path <- spec$paths$database
snapshot <- spec$raw_snapshot$path
if (length(args)) {
  for (i in seq.int(1L,length(args),by=2L)) {
    if (args[i]=="--database") database_path <- args[i+1L]
    if (args[i]=="--snapshot") snapshot <- args[i+1L]
  }
}
# Overrides select only identities already authorized by this specification.
if (normalizePath(snapshot,winslash="/",mustWork=TRUE)!=normalizePath(spec$raw_snapshot$path,winslash="/",mustWork=TRUE)) stop("Snapshot override disagrees with specification")
if (normalizePath(database_path,winslash="/",mustWork=FALSE)!=normalizePath(spec$paths$database,winslash="/",mustWork=FALSE)) stop("Database override disagrees with specification")
database_path <- run_path(substring(spec$paths$database,nchar(root)+2L))
manifest_path <- file.path(snapshot,"source_manifest.json")
manifest <- fromJSON(manifest_path,simplifyVector=FALSE)
if (!identical(manifest$status,"acquired_current_values_vintages_unverified")) stop("Incomplete frozen BCB snapshot")
manifest_hash <- digest(file=manifest_path,algo="sha256",serialize=FALSE)
if (is.null(spec$raw_snapshot$manifest_sha256) || manifest_hash!=spec$raw_snapshot$manifest_sha256) stop("Raw snapshot manifest identity is not pinned")
for (filename in names(manifest$normalized_sha256)) {
  if (basename(filename)!=filename || !file.exists(file.path(snapshot,filename)) || digest(file=file.path(snapshot,filename),algo="sha256",serialize=FALSE)!=manifest$normalized_sha256[[filename]]) stop("Normalized snapshot checksum mismatch")
}
sgs <- fread(file.path(snapshot,"sgs.csv"),colClasses="character",encoding="UTF-8")
focus <- fread(file.path(snapshot,"focus.csv"),colClasses="character",encoding="UTF-8")
sgs$series_code <- as.integer(sgs$series_code)
sgs$value <- as.numeric(sgs$value)
for (column in c("mean","median","sd","min","max")) set(focus,j=column,value=as.numeric(focus[[column]]))
for (column in c("respondents","calculation_base")) set(focus,j=column,value=as.integer(focus[[column]]))
sgs_columns <- c("series_code","series_name","date","value","source_url","fetched_at")
focus_columns <- c("source_entity","indicator","date","reference_period","meeting","calculation_type","smoothed","mean","median","sd","min","max","respondents","calculation_base","source_url","fetched_at")
if (!identical(names(sgs),sgs_columns) || !identical(names(focus),focus_columns)) stop("Frozen BCB schema differs from database contract")
if (anyDuplicated(sgs[,.(series_code,date)]) || any(!is.finite(sgs$value))) stop("Invalid SGS keys/values")
if (anyDuplicated(focus[,.(source_entity,indicator,date,reference_period,meeting,calculation_type,smoothed,calculation_base)])) stop("Duplicate Focus selector/date key")
required_sgs <- c(7478L,24364L,10813L,432L,13522L,24363L,433L,29602L,29604L,29606L)
required_focus <- c("ExpectativasMercadoInflacao12Meses","ExpectativasMercadoTop5Inflacao12Meses","ExpectativasMercadoSelic")
if (!setequal(unique(sgs$series_code),required_sgs) || !setequal(unique(focus$source_entity),required_focus)) stop("Supporting-source inventory is incomplete")
if (any(!is.finite(focus$median)) || any(focus$indicator[focus$source_entity!="ExpectativasMercadoSelic"]!="IPCA")) stop("Invalid Focus statistics/indicator")
target <- focus[source_entity=="ExpectativasMercadoInflacao12Meses"]
if (any(target$smoothed!="N" | target$calculation_base!=0L) || anyDuplicated(target$date)) stop("Ambiguous same-date Focus target")
dates <- fread(spec$sample$dates_csv)[meeting_number %in% as.integer(spec$sample$meeting_numbers)]
if (!setequal(dates$meeting_number,as.integer(spec$sample$meeting_numbers)) || anyDuplicated(dates$meeting_number)) stop("Canonical meeting support is incomplete")
dates$meeting_date <- as.character(dates$meeting_date)
focus_audit <- merge(dates,target,by.x="meeting_date",by.y="date",all.x=TRUE,sort=FALSE)
if (nrow(focus_audit)!=nrow(dates) || anyNA(focus_audit$median)) stop("Missing same-date unsmoothed base-zero Focus target")
dir.create(run_path("data/analysis"),recursive=TRUE,showWarnings=FALSE)
fwrite(focus_audit,run_path("data/analysis/meeting_focus_target_audit.csv"))
source_manifest <- rbindlist(list(
  data.table(source_id=paste0("BCB_SGS_",required_sgs),source_type="BCB_SGS",status="imported_frozen",note="Current retrieval vintage. Reference date is not publication date.",source_url=paste0("https://api.bcb.gov.br/dados/serie/bcdata.sgs.",required_sgs,"/dados"),fetched_at=manifest$retrieved_at),
  data.table(source_id=required_focus,source_type="BCB_OLINDA",status="imported_frozen",note="Frozen official Focus statistics",source_url=paste0("https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/",required_focus),fetched_at=manifest$retrieved_at)
))
if (!file.exists(database_path)) stop("Initialize the selected minutes-only warehouse before offline BCB import")
con <- dbConnect(SQLite(),database_path)
tryCatch({
  dbExecute(con,"PRAGMA busy_timeout = 60000")
  if (!dbExistsTable(con,"metadata")) stop("Selected warehouse lacks immutable specification metadata")
  warehouse_identity <- dbGetQuery(con,"SELECT value FROM metadata WHERE key='replication_specification'")
  if (nrow(warehouse_identity)!=1L) stop("Selected warehouse has no unique specification identity")
  warehouse_spec <- fromJSON(warehouse_identity$value)
  expected_identity <- list(spec_sha256=spec_sha256,fingerprint=spec$fingerprint,release_id=spec$release_id,design_type=spec$design_type)
  for (field in names(expected_identity)) if (!identical(warehouse_spec[[field]],expected_identity[[field]])) stop("Warehouse/specification mismatch: ",field)
  dbWithTransaction(con,{
    dbExecute(con,"CREATE TABLE IF NOT EXISTS external_bcb_sgs (series_code INTEGER NOT NULL, series_name TEXT NOT NULL, date TEXT NOT NULL,value REAL,source_url TEXT NOT NULL,fetched_at TEXT NOT NULL,PRIMARY KEY(series_code,date))")
    dbExecute(con,"CREATE TABLE IF NOT EXISTS external_focus_statistics (source_entity TEXT NOT NULL,indicator TEXT,date TEXT NOT NULL,reference_period TEXT,meeting TEXT,calculation_type TEXT,smoothed TEXT,mean REAL,median REAL,sd REAL,min REAL,max REAL,respondents INTEGER,calculation_base INTEGER,source_url TEXT NOT NULL,fetched_at TEXT NOT NULL)")
    dbExecute(con,"CREATE INDEX IF NOT EXISTS idx_external_focus_source_date ON external_focus_statistics(source_entity,date)")
    dbExecute(con,"CREATE TABLE IF NOT EXISTS external_source_manifest (source_id TEXT PRIMARY KEY,source_type TEXT NOT NULL,status TEXT NOT NULL,note TEXT,source_url TEXT NOT NULL,fetched_at TEXT NOT NULL)")
    dbExecute(con,"CREATE TABLE IF NOT EXISTS external_bcb_snapshot (manifest_sha256 TEXT PRIMARY KEY,snapshot_path TEXT NOT NULL,imported_at TEXT NOT NULL)")
    existing <- dbReadTable(con,"external_bcb_snapshot")
    if (nrow(existing) && !identical(existing$manifest_sha256,manifest_hash)) stop("Warehouse already pins a different BCB snapshot")
    if (!nrow(existing)) {
      for (code in required_sgs) if (dbGetQuery(con,"SELECT count(*) n FROM external_bcb_sgs WHERE series_code=?",params=list(code))$n>0) stop("Unidentified existing SGS vintage cannot be replaced")
      for (entity in required_focus) if (dbGetQuery(con,"SELECT count(*) n FROM external_focus_statistics WHERE source_entity=?",params=list(entity))$n>0) stop("Unidentified existing Focus snapshot cannot be replaced")
      dbWriteTable(con,"external_bcb_sgs",as.data.frame(sgs),append=TRUE)
      dbWriteTable(con,"external_focus_statistics",as.data.frame(focus),append=TRUE)
      for (source_id in source_manifest$source_id) if (dbGetQuery(con,"SELECT count(*) n FROM external_source_manifest WHERE source_id=?",params=list(source_id))$n>0) stop("Existing source manifest cannot be replaced")
      dbWriteTable(con,"external_source_manifest",as.data.frame(source_manifest),append=TRUE)
      dbExecute(con,"INSERT INTO external_bcb_snapshot VALUES(?,?,?)",params=list(manifest_hash,snapshot,format(Sys.time(),tz="UTC",usetz=TRUE)))
    } else {
      # A matching marker alone is insufficient after an interrupted/manual change.
      stored_sgs <- as.data.table(dbReadTable(con,"external_bcb_sgs"))[series_code %in% required_sgs]
      stored_focus <- as.data.table(dbReadTable(con,"external_focus_statistics"))[source_entity %in% required_focus]
      setorderv(stored_sgs,c("series_code","date"))
      setorderv(sgs,c("series_code","date"))
      focus_keys <- c("source_entity","indicator","date","meeting","calculation_type","smoothed","calculation_base")
      setorderv(stored_focus,focus_keys)
      setorderv(focus,focus_keys)
      if (!isTRUE(all.equal(as.data.frame(stored_sgs),as.data.frame(sgs),check.attributes=FALSE)) || !isTRUE(all.equal(as.data.frame(stored_focus),as.data.frame(focus),check.attributes=FALSE))) stop("Pinned snapshot contents changed after import. SGS=",paste(all.equal(as.data.frame(stored_sgs),as.data.frame(sgs),check.attributes=FALSE),collapse=" / ")," Focus=",paste(all.equal(as.data.frame(stored_focus),as.data.frame(focus),check.attributes=FALSE),collapse=" / "))
    }
  })
},finally=dbDisconnect(con))
cat(sprintf("Offline BCB import verified. SGS=%d, Focus=%d, snapshot=%s\n",nrow(sgs),nrow(focus),manifest_hash))
