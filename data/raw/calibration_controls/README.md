# Frozen non-LLM calibration controls

`copom_meeting_activity_controls.csv` contains 71 rows and five fields:

| Field | Meaning |
|---|---|
| `reuniao` | Copom meeting number, 203--273. |
| `date_daily` | Meeting date, 2016-11-30 through 2025-09-17. |
| `ibcbr_agro` | Agriculture activity component, monthly index level. |
| `ibcbr_industry` | Industry activity component, monthly index level. |
| `ibcbr_service` | Services activity component, monthly index level. |

The three activity values were extracted without transformation from the
author-maintained workbook `data_copom.xlsx`. The extraction retains the
workbook's meeting/date pairing and numerical precision, sorts by meeting, and
contains no synthetic forecast or counterfactual outcome. The source workbook
has SHA-256
`aca8ef4996c025249b07c831b9b1f7c0303579fdf21640c802cb3a3df62a26c5`
and was last modified at `2025-12-03T19:22:30Z` according to its OOXML core
properties. It is preserved in the private research-history archive and is not
an active replication input.

The workbook identifies the provider as Banco Central do Brasil/Depec, the
frequency as monthly, and the unit as an index level. It does **not** retain the
SGS series identifiers, seasonal-adjustment flags, download timestamp, or a
machine-readable rule establishing which monthly release vintage was paired
with each meeting. Those attributes cannot be recovered reliably from the
available source. The public package therefore treats this CSV as a frozen
third-party-derived input and discloses the gap instead of assigning an
unverified series identifier or vintage.

Integrity facts for the distributed CSV:

- rows: 71;
- meetings: 203--273, complete and unique;
- meeting-date range: 2016-11-30--2025-09-17;
- missing values: none;
- SHA-256:
  `3628861d81d86fed145bd7c3751b40c51e632e115f4a64ed1f7b954217d936f4`.

At run time, `code/01_data_preparation/build_gpt55_calibration_data.R`
checks these invariants and joins the controls to official aggregate Focus
data, original-minute tone/length, and the 142 original-minute GPT-5.5
forecasts in `data/generated/prompt_iv_replication_source.sqlite`. It writes
the generated calibration panel to
`outputs/work/gpt55_calibration_data.dta`.
