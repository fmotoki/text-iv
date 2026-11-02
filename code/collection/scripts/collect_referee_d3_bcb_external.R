# Retired acquisition bootstrap. Use the single snapshot authority, then the offline importer.
stop("This network bootstrap is retired. Run code/01_data_preparation/download_minutes.py --data-only with the selected specification, initialize the minutes-only warehouse, then run collect_referee_completion_bcb.R offline. No calibration .dta is needed for acquisition.")
