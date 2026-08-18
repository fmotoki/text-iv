# Reproduced results

The master script recreates the paper-facing LaTeX tables under `tables/`, PDF
figures under `figures/`, and numerical/visual quality-assurance artifacts under
`qa/`. A successful run writes `verification_report.csv`; every row must have
`status = pass`.

These files are retained in the public package as convenience outputs. They are
not read as inputs by the econometric stages. Exact table comparisons use the
frozen targets under `reference/`.
