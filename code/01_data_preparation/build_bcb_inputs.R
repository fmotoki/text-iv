# Build release-evidence controls without generated calibration outcomes.
suppressPackageStartupMessages({library(data.table); library(jsonlite)})
spec_path <- Sys.getenv("REPLICATION_SPEC_PATH")
if (!nzchar(spec_path)) stop("REPLICATION_SPEC_PATH is required")
source_root <- jsonlite::fromJSON(spec_path)$source_root
source(file.path(source_root, "code", "read_run_spec.R"))
if (spec$activity_controls$policy != "strict_release_vintage") stop("An approved value-vintage policy is required")
if (!identical(as.integer(spec$activity_controls$sector_codes), c(29602L,29604L,29606L))) stop("Sector specification changed")
dates <- fread(spec$sample$dates_csv)
setorder(dates, meeting_number)
dates <- dates[meeting_number %in% as.integer(spec$sample$meeting_numbers)]
if (!identical(as.integer(dates$meeting_number), as.integer(spec$sample$meeting_numbers)) || anyDuplicated(dates$meeting_number) || anyDuplicated(dates$meeting_date)) stop("Canonical meeting keys are incomplete")
if (spec$design_type == "production" && !identical(as.integer(dates$meeting_number), 203:281)) stop("Production requires all 79 meetings")
dates$meeting_date <- as.character(dates$meeting_date)

# Missing cutoffs remain explicit audit rows. No clock time is guessed.
cutoff_path <- spec$activity_controls$cutoffs_file
cutoffs <- data.table(meeting_number=integer(),meeting_cutoff=character(),timezone=character(),cutoff_precision=character(),evidence_url=character(),convention=character())
if (file.exists(cutoff_path)) {
  cutoffs <- fread(cutoff_path, colClasses="character")
  if (!all(names(data.table(meeting_number=1,meeting_cutoff="",timezone="",cutoff_precision="",evidence_url="",convention="")) %in% names(cutoffs))) stop("Cutoff schema is incomplete")
  cutoffs$meeting_number <- as.integer(cutoffs$meeting_number)
  if (anyDuplicated(cutoffs$meeting_number) || any(!cutoffs$meeting_number %in% dates$meeting_number)) stop("Duplicate/unknown cutoff meeting")
}
dates <- merge(dates, cutoffs, by="meeting_number", all.x=TRUE, sort=FALSE)
setorder(dates, meeting_number)
snapshot <- spec$raw_snapshot$path
calendar_path <- file.path(snapshot, spec$activity_controls$calendar_file)
vintages_path <- file.path(snapshot, spec$activity_controls$vintages_file)
calendar <- data.table(series_code=integer(),reference_month=character(),release_id=character(),actual_release_date=character(),actual_release_timestamp=character(),timezone=character(),timestamp_precision=character(),scheduled_release_date=character(),event_status=character(),evidence_url=character(),retrieved_at=character(),source_hash=character())
vintages <- data.table(series_code=integer(),reference_month=character(),release_id=character(),vintage_timestamp=character(),value_at_release=numeric(),vintage_status=character(),source_file=character(),source_hash=character())
calendar_required <- names(calendar)
vintage_required <- names(vintages)
if (file.exists(calendar_path)) calendar <- fread(calendar_path, colClasses="character")
if (file.exists(vintages_path)) vintages <- fread(vintages_path, colClasses="character")
if (!all(calendar_required %in% names(calendar)) || !all(vintage_required %in% names(vintages))) stop("Release/vintage schema is incomplete")
calendar$series_code <- as.integer(calendar$series_code)
vintages$series_code <- as.integer(vintages$series_code)
if (anyNA(calendar$series_code) || anyNA(vintages$series_code)) stop("Invalid series code")
if (anyDuplicated(calendar[, .(series_code,reference_month,release_id)]) || anyDuplicated(vintages[, .(series_code,reference_month,release_id)])) stop("Duplicate release/vintage keys")
if (nrow(calendar) && any(!calendar$event_status %in% c("actual","scheduled","announcement","catalog_update"))) stop("Unknown release-event status")
actual_rows <- calendar[event_status=="actual"]
if (nrow(actual_rows) && any(!grepl("^https://",actual_rows$evidence_url) | !grepl("^[0-9a-f]{64}$",actual_rows$source_hash))) stop("Actual event lacks evidence")

# Production events must trace to the pinned payload bytes, not only a URL label.
if (spec$design_type=="production" && file.exists(calendar_path)) {
  manifest_path <- file.path(snapshot,"source_manifest.json")
  if (!file.exists(manifest_path) || is.null(spec$raw_snapshot$manifest_sha256) || digest::digest(file=manifest_path,algo="sha256",serialize=FALSE)!=spec$raw_snapshot$manifest_sha256) stop("Release-history snapshot is not pinned")
  source_manifest <- fromJSON(manifest_path,simplifyVector=FALSE)
  if (is.null(source_manifest$normalized_sha256[[basename(calendar_path)]]) || digest::digest(file=calendar_path,algo="sha256",serialize=FALSE)!=source_manifest$normalized_sha256[[basename(calendar_path)]]) stop("Release history checksum mismatch")
  for (hash in unique(calendar$source_hash)) {
    evidence <- Filter(function(item) identical(item$sha256,hash),source_manifest$requests)
    if (length(evidence)!=1L) stop("Release event lacks unique original payload evidence")
    payload <- normalizePath(file.path(snapshot,evidence[[1L]]$file),winslash="/",mustWork=TRUE)
    if (!startsWith(payload,paste0(normalizePath(snapshot,winslash="/",mustWork=TRUE),"/")) || digest::digest(file=payload,algo="sha256",serialize=FALSE)!=hash) stop("Release payload identity mismatch")
  }
  if (any(!startsWith(calendar$evidence_url,"https://www.bcb.gov.br/"))) stop("Production disclosure evidence must be official BCB evidence")
}

# No library supplies this BCB event-status and vintage join. Keep every failed
# intended key. Current SGS values are never a fallback for a release vintage.
audit_rows <- vector("list", nrow(dates)*4L)
index <- 0L
for (meeting_index in seq_len(nrow(dates))) {
  meeting <- dates[meeting_index]
  for (code in c(29602L,29604L,29606L,24364L)) {
    index <- index+1L
    row <- data.table(meeting_number=meeting$meeting_number,meeting_date=meeting$meeting_date,series_code=code,alignment_rule=spec$activity_controls$alignment,meeting_cutoff=meeting$meeting_cutoff,cutoff_timezone=meeting$timezone,cutoff_precision=meeting$cutoff_precision,cutoff_evidence=meeting$evidence_url,cutoff_convention=meeting$convention,release_id=NA_character_,reference_month=NA_character_,actual_release_date=NA_character_,actual_release_timestamp=NA_character_,publication_precision=NA_character_,selected_value=NA_real_,ibc_change_pre=NA_real_,vintage_status="BLOCKED_VINTAGE",evidence_url=NA_character_,source_hash=NA_character_,retrieval_vintage=NA_character_,availability_status="BLOCKED_RELEASE_EVIDENCE")
    status <- "BLOCKED_RELEASE_EVIDENCE"
    if (code %in% c(29602L,29604L,29606L) && meeting$meeting_date < spec$activity_controls$sector_first_publication) {
      status <- "BLOCKED_PUBLIC_AVAILABILITY"
    } else if (is.na(meeting$meeting_cutoff) || !nzchar(meeting$meeting_cutoff) || is.na(meeting$timezone) || !meeting$timezone %in% OlsonNames() || (is.na(meeting$evidence_url) || !nzchar(meeting$evidence_url)) && (is.na(meeting$convention) || !nzchar(meeting$convention))) {
      status <- "BLOCKED_MEETING_CUTOFF"
    } else {
      cutoff_date <- substr(meeting$meeting_cutoff,1L,10L)
      if (!identical(cutoff_date, meeting$meeting_date)) stop("Cutoff date disagrees with meeting date")
      actual <- calendar[series_code==code & event_status=="actual" & !is.na(actual_release_date) & nzchar(actual_release_date)]
      if (code %in% c(29602L,29604L,29606L)) actual <- actual[actual_release_date>=spec$activity_controls$sector_first_publication]
      actual$release_clock <- as.POSIXct(strptime(sub("Z$","+0000",gsub("([+-][0-9]{2}):([0-9]{2})$","\\1\\2",actual$actual_release_timestamp)),"%Y-%m-%dT%H:%M:%OS%z",tz="UTC"))
      cutoff_clock <- as.POSIXct(NA)
      if (meeting$cutoff_precision %in% c("second","minute") && grepl("(Z|[+-][0-9]{2}:[0-9]{2})$",meeting$meeting_cutoff)) cutoff_clock <- as.POSIXct(strptime(sub("Z$","+0000",gsub("([+-][0-9]{2}):([0-9]{2})$","\\1\\2",meeting$meeting_cutoff)),"%Y-%m-%dT%H:%M:%OS%z",tz="UTC"))
      same_day <- actual[actual_release_date==cutoff_date]
      if (nrow(same_day) && (is.na(cutoff_clock) || anyNA(same_day$release_clock) || any(!same_day$timestamp_precision %in% c("second","minute")))) {
        status <- "BLOCKED_SAME_DAY_CUTOFF"
      } else {
        eligible <- actual[actual_release_date<cutoff_date | (actual_release_date==cutoff_date & !is.na(release_clock) & release_clock<=cutoff_clock)]
        if (nrow(eligible)) {
          setorder(eligible,actual_release_date,release_clock,reference_month)
          selected <- tail(eligible,1L)
          latest <- eligible[actual_release_date==selected$actual_release_date]
          if (nrow(unique(latest[,.(release_id,actual_release_timestamp)]))>1L && anyNA(latest$release_clock)) stop("Conflicting date-only latest releases")
          for (field in c("release_id","reference_month","actual_release_date","actual_release_timestamp","evidence_url","source_hash")) set(row,j=field,value=selected[[field]])
          row$publication_precision <- selected$timestamp_precision
          row$retrieval_vintage <- selected$retrieved_at
          vintage <- vintages[series_code==code & release_id==selected$release_id]
          value <- vintage[reference_month==selected$reference_month]
          status <- "BLOCKED_VINTAGE"
          if (nrow(value)==1L && value$vintage_status=="VERIFIED_RELEASE_VINTAGE" && is.finite(as.numeric(value$value_at_release))) {
            # Require the exact archived bytes for every value used.
            vintage_ready <- all(vintage$vintage_status=="VERIFIED_RELEASE_VINTAGE") && all(vintage$vintage_timestamp==selected$actual_release_timestamp) && nzchar(selected$actual_release_timestamp)
            for (vi in seq_len(nrow(vintage))) {
              source_file <- normalizePath(file.path(snapshot,vintage$source_file[vi]),winslash="/",mustWork=FALSE)
              snapshot_prefix <- paste0(normalizePath(snapshot,winslash="/",mustWork=TRUE),"/")
              if (!startsWith(source_file,snapshot_prefix) || !file.exists(source_file) || !grepl("^[0-9a-f]{64}$",vintage$source_hash[vi]) || digest::digest(file=source_file,algo="sha256",serialize=FALSE)!=vintage$source_hash[vi]) vintage_ready <- FALSE
            }
            if (vintage_ready) {
              row$selected_value <- as.numeric(value$value_at_release)
              row$vintage_status <- value$vintage_status
              status <- "READY"
              if (code==24364L) {
                vintage <- vintage[reference_month<=selected$reference_month & vintage_status=="VERIFIED_RELEASE_VINTAGE"]
                setorder(vintage,reference_month)
                if (nrow(vintage)<2L) status <- "BLOCKED_VINTAGE" else {
                  pair <- as.numeric(tail(vintage$value_at_release,2L))
                  months <- as.Date(paste0(tail(vintage$reference_month,2L),"-01"))
                  expected_month <- seq(months[1L],by="month",length.out=2L)[2L]
                  if (months[2L]!=expected_month) stop("Aggregate change requires adjacent months in one release vintage")
                  if (any(!is.finite(pair) | pair<=0)) stop("Invalid aggregate vintage levels")
                  row$ibc_change_pre <- 100*log(pair[2L]/pair[1L])
                }
              }
            }
          }
        }
      }
    }
    row$availability_status <- status
    audit_rows[[index]] <- row
  }
}
audit <- rbindlist(audit_rows)
sector <- audit[series_code %in% c(29602L,29604L,29606L)]
aggregate <- audit[series_code==24364L]
if (nrow(sector)!=3L*nrow(dates) || anyDuplicated(sector[,.(meeting_number,series_code)]) || nrow(aggregate)!=nrow(dates)) stop("Audit lost intended keys")
dir.create(run_path("data/analysis"),recursive=TRUE,showWarnings=FALSE)
fwrite(sector,run_path("data/analysis/meeting_ibc_availability.csv"),na="")
fwrite(aggregate,run_path("data/analysis/meeting_ibc_aggregate_availability.csv"),na="")
if (all(sector$availability_status=="READY")) {
  panel <- dcast(sector,meeting_number+meeting_date~series_code,value.var="selected_value")
  setnames(panel,c("meeting_number","meeting_date","29602","29604","29606"),c("reuniao","date_daily","ibcbr_agro","ibcbr_industry","ibcbr_service"))
  fwrite(panel,run_path("data/analysis/copom_meeting_activity_controls.csv"))
} else if (file.exists(spec$paths$sector_controls)) stop("Blocked policy with an existing sector panel. Use a fresh run root.")
if (all(aggregate$availability_status=="READY")) {
  fwrite(aggregate,run_path("data/analysis/meeting_ibc_aggregate_controls.csv"))
} else if (file.exists(run_path("data/analysis/meeting_ibc_aggregate_controls.csv"))) {
  stop("Blocked policy with an existing aggregate panel. Use a fresh run root.")
}
readiness <- list(implementation="audit_written",policy=spec$activity_controls$policy,full_input_readiness=if(all(sector$availability_status=="READY")) "READY" else "BLOCKED",intended_sector_rows=nrow(sector),statuses=as.data.frame(sector[,.N,by=availability_status]),snapshot=snapshot,release_id=spec$release_id)
write_json(readiness,run_path("data/analysis/bcb_input_readiness.json"),auto_unbox=TRUE,pretty=TRUE)
cat(toJSON(readiness,auto_unbox=TRUE),"\n")
