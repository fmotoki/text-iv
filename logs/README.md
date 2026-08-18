# Logs

`code/00_master.R` writes one log and one completion marker per stage under `steps/`, plus `master_run_times.csv` and `master_sessionInfo.txt`. `code/00_setup.R` writes `setup_sessionInfo.txt` after restoring the locked environment.

The step directory is expendable and is recreated by a fresh run. The run-time and session files should be retained in the deposited package after final validation.
