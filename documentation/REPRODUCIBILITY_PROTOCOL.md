# AEA-ready reproducibility protocol

## Prospective Terra implementation

Current source code follows the [Terra protocol](terra56_collection_validation_protocol.md).
It requires an explicit effective specification and keeps Git source versions
separate from data/result versions in the sibling artifact directory. No new
production collection, accepted numerical baseline, or clean-room pass is implied.
The release rules below describe the inherited package and the requirements for
a later accepted deposit.

## Release construction

1. Curate only the inputs read by active analysis programs.
2. Freeze generated LLM output in a compact SQLite database; never require a paid recollection for exact replication.
3. Separate immutable source data from the writable analysis database.
4. Remove credentials, caches, personal paths, temporary files, and superseded artifacts.
5. Record source-level provenance, access conditions, run dimensions, variable definitions, and SHA-256 hashes.
6. Lock R packages with `renv` and report software, memory, disk, and runtime requirements.
7. Map every current paper exhibit to one producing program.
8. Execute the package from a clean isolated copy with only the documented commands.
9. Verify headline values and exhibit completeness automatically.
10. Retain step logs, session information, and the final package manifest.

## Release gates

- **Isolation:** code lookup is restricted to the selected source package. Artifact writes and cleanup are restricted to its separately selected run root. Shared immutable inputs are explicitly fingerprinted.
- **Integrity:** every frozen input must match `data/metadata/input_checksums.csv`.
- **Completeness:** all rows in `data/metadata/exhibit_manifest.csv` must produce a nonempty artifact.
- **Numerical reproduction:** all rows in `results/verification_report.csv` must pass.
- **Security:** no API key, token, password, or user credential may appear in the release.
- **Transparency:** LLM collection must be documented separately from exact analysis reproduction.
- **Licensing:** code, author-generated data, and third-party-source rights must be distinguished.
- **Clean-room:** the master must succeed in an isolated copy without the parent project.

## Versioning rule

Any change to a frozen input, prompt family, run identifier, econometric specification, or paper exhibit increments the package version and requires a new clean-room run. Recollection results are never merged silently into an existing frozen run.

During implementation, Git commits identify code changes and a new artifact
release ID identifies each substantive rerun. A final package version and deposit
are assigned only with the later reviewed freeze and clean-room run.
