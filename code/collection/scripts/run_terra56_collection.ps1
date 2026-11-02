<#
Prepare the selected Terra cohort. Remote collection requires both the switch
and explicit permission in the frozen specification. Production is not run by
the implementation task. Every command uses source code from SOURCE_ROOT.
#>
param(
  [Parameter(Mandatory = $true)] [string]$SpecificationPath,
  [switch]$AllowRemoteCalls,
  [string]$PythonExecutable = 'C:/Users/csg690/AppData/Local/anaconda3/python.exe',
  [string]$RscriptExecutable = 'C:/Users/csg690/AppData/Local/Programs/R/R-4.5.3/bin/Rscript.exe'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$SpecificationPath = (Resolve-Path -LiteralPath $SpecificationPath).ProviderPath
$Spec = Get-Content -LiteralPath $SpecificationPath -Raw | ConvertFrom-Json
$SourceRoot = $Spec.source_root
$RunRoot = $Spec.run_root
$PriorPythonPath = $env:PYTHONPATH
$PriorSpecPath = $env:REPLICATION_SPEC_PATH
$PriorBytecode = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONPATH = Join-Path $SourceRoot 'code/collection/src'
$env:REPLICATION_SPEC_PATH = $SpecificationPath
$env:PYTHONDONTWRITEBYTECODE = '1'
try {
  # Python resolves symlinks and rejects protected paths before any file write.
  & $PythonExecutable -c 'from prompt_iv.paths import load_spec; load_spec(require_frozen=True)'
  if ($LASTEXITCODE -ne 0) { throw 'Specification/fingerprint validation failed.' }
  if ($AllowRemoteCalls -and (-not $Spec.execution.allow_remote_calls)) {
    throw 'Remote calls are disabled in this specification.'
  }
  if ($AllowRemoteCalls -and $Spec.design_type -eq 'production' -and (-not $Spec.execution.full_production_authorized)) {
    throw 'Full production has not been authorized in this specification.'
  }
  $M = Join-Path $SourceRoot 'data/raw/copom_minutes'
  $B = Join-Path $RunRoot $Spec.paths.database
  $Collections = Join-Path $RunRoot $Spec.paths.collections
  $Dmain = Join-Path $Collections $Spec.run_ids.main_text
  $Ddose = Join-Path $Collections $Spec.run_ids.dose_text
  $Validation = Join-Path $RunRoot ('data/interim/dose_response/' + $Spec.run_ids.blinded_scoring)
  $Meetings = $Spec.sample.meeting_numbers -join ','
  $MainDraws = (1..$Spec.draws.main) -join ','
  $DoseDraws = (1..$Spec.draws.dose) -join ','
  $Profiles = $Spec.profiles -join ','
  $MainRoles = $Spec.roles.main -join ','
  $DoseRoles = $Spec.roles.dose -join ','
  $DoseGenerated = $Spec.roles.dose_generated -join ','
  $Cli = @('-m', 'prompt_iv.cli', '--root', $SourceRoot, '--spec', $SpecificationPath)

  & $PythonExecutable @Cli prepare-replication --run-id $Spec.run_ids.main_text --run-directory $Dmain --minutes-dir $M --meeting-numbers $Meetings --iterations $Spec.draws.main --parts $Spec.blocks --stances $MainRoles --prompt-version $Spec.prompt_versions.rewrite --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0
  if ($LASTEXITCODE -ne 0) { throw 'Main rewrite preparation failed.' }
  & $PythonExecutable @Cli prepare-replication --run-id $Spec.run_ids.dose_text --run-directory $Ddose --minutes-dir $M --meeting-numbers $Meetings --iterations $Spec.draws.dose --parts $Spec.blocks --stances $DoseGenerated --prompt-version $Spec.prompt_versions.rewrite --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0
  if ($LASTEXITCODE -ne 0) { throw 'Dose rewrite preparation failed.' }
  & $PythonExecutable @Cli sync-database --output $B --minutes-dir $M --minutes-only
  if ($LASTEXITCODE -ne 0) { throw 'Original minute bootstrap failed.' }
  & $RscriptExecutable (Join-Path $SourceRoot 'code/collection/scripts/collect_referee_completion_bcb.R') --database $B --snapshot $Spec.raw_snapshot.path
  if ($LASTEXITCODE -ne 0) { throw 'Offline Bacen import failed. Resolve the snapshot prerequisites.' }
  # Dose is registered once, before its DB-first executor owns its results.
  $DoseRegistered = & $PythonExecutable -c 'import pathlib,sqlite3,sys; c=sqlite3.connect(pathlib.Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True); print(c.execute("SELECT COUNT(*) FROM runs WHERE run_id=?",(sys.argv[2],)).fetchone()[0]); c.close()' $B $Spec.run_ids.dose_text
  if ($LASTEXITCODE -ne 0) { throw 'Dose registration inspection failed.' }
  if ([int]$DoseRegistered -eq 0) {
    & $PythonExecutable @Cli sync-database --output $B --minutes-dir $M --run-dir $Ddose
    if ($LASTEXITCODE -ne 0) { throw 'Initial dose manifest import failed.' }
  }
  if (-not $AllowRemoteCalls) {
    Write-Output "Preparation completed under $RunRoot. No remote calls were issued."
    return
  }

  # Complete every main rewrite before assembly. Earlier failed events remain
  # in the append-only log. Only the latest exact planned keys are accepted.
  & $PythonExecutable @Cli execute-counterfactuals --manifest (Join-Path $Dmain 'rewrite_manifest.jsonl') --output (Join-Path $Dmain 'rewrite_results.jsonl') --runner codex --allow-real-llm --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0 --max-retries 2 --max-workers 2 --timeout-seconds 300
  if ($LASTEXITCODE -ne 0) { throw 'Main rewrites failed.' }
  & $PythonExecutable @Cli evaluate-counterfactuals --manifest (Join-Path $Dmain 'rewrite_manifest.jsonl') --results (Join-Path $Dmain 'rewrite_results.jsonl') --output (Join-Path $Dmain 'counterfactual_quality_report.json') --details-output (Join-Path $Dmain 'counterfactual_quality_details.csv')
  if ($LASTEXITCODE -ne 0) { throw 'Main rewrite acceptance failed.' }
  & $PythonExecutable @Cli assemble-counterfactuals --manifest (Join-Path $Dmain 'rewrite_manifest.jsonl') --results (Join-Path $Dmain 'rewrite_results.jsonl') --output (Join-Path $Dmain 'counterfactual_corpus.csv') --required-stances $MainRoles
  if ($LASTEXITCODE -ne 0) { throw 'Main text assembly failed.' }
  & $PythonExecutable @Cli score-counterfactuals --input (Join-Path $Dmain 'counterfactual_corpus.csv') --output (Join-Path $Dmain 'counterfactual_tone_scores.csv')
  if ($LASTEXITCODE -ne 0) { throw 'Main lexicon scoring failed.' }
  $MainRegistered = & $PythonExecutable -c 'import pathlib,sqlite3,sys; c=sqlite3.connect(pathlib.Path(sys.argv[1]).as_uri()+"?mode=ro",uri=True); print(c.execute("SELECT COUNT(*) FROM runs WHERE run_id=?",(sys.argv[2],)).fetchone()[0]); c.close()' $B $Spec.run_ids.main_text
  if ($LASTEXITCODE -ne 0) { throw 'Main registration inspection failed.' }
  if ([int]$MainRegistered -eq 0) {
    & $PythonExecutable @Cli sync-database --output $B --minutes-dir $M --run-dir $Dmain
    if ($LASTEXITCODE -ne 0) { throw 'Complete main source import failed.' }
  }
  & $PythonExecutable @Cli execute-counterfactuals-db --database $B --run-id $Spec.run_ids.dose_text --runner codex --allow-real-llm --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0 --max-retries 2 --max-workers 2 --timeout-seconds 300
  if ($LASTEXITCODE -ne 0) { throw 'Dose rewrites failed.' }
  & $PythonExecutable (Join-Path $SourceRoot 'code/collection/scripts/postprocess_v10_referee_step3_db.py') --root $SourceRoot --database $B --run-id $Spec.run_ids.dose_text
  if ($LASTEXITCODE -ne 0) { throw 'Dose completeness/assembly/scoring failed.' }

  & $PythonExecutable @Cli prepare-original-priors-db --database $B --run-id $Spec.run_ids.original_priors --meeting-numbers $Meetings --forecaster-types $Profiles --initial-expectation $Spec.initial_expectation --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0
  if ($LASTEXITCODE -ne 0) { throw 'Original path preparation failed.' }
  & $PythonExecutable @Cli execute-original-priors-db --database $B --run-id $Spec.run_ids.original_priors --runner codex --allow-real-llm --max-workers 2 --timeout-seconds 300
  if ($LASTEXITCODE -ne 0) { throw 'Original path execution failed.' }
  & $PythonExecutable @Cli audit-original-priors-db --database $B --run-id $Spec.run_ids.original_priors
  if ($LASTEXITCODE -ne 0) { throw 'Original path audit failed.' }
  & $PythonExecutable @Cli prepare-common-prior-agents-db --database $B --run-id $Spec.run_ids.common_prior_main --source-run-id $Spec.run_ids.main_text --prior-run-id $Spec.run_ids.original_priors --meeting-numbers $Meetings --iterations $MainDraws --roles $MainRoles --forecaster-types $Profiles --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0
  if ($LASTEXITCODE -ne 0) { throw 'Main common-prior preparation failed.' }
  & $PythonExecutable @Cli execute-common-prior-agents-db --database $B --run-id $Spec.run_ids.common_prior_main --runner codex --allow-real-llm --max-workers 2 --timeout-seconds 300
  if ($LASTEXITCODE -ne 0) { throw 'Main common-prior execution failed.' }
  & $PythonExecutable @Cli audit-common-prior-db --database $B --run-id $Spec.run_ids.common_prior_main --prior-run-id $Spec.run_ids.original_priors
  if ($LASTEXITCODE -ne 0) { throw 'Main common-prior audit failed.' }
  & $PythonExecutable @Cli prepare-common-prior-agents-db --database $B --run-id $Spec.run_ids.common_prior_dose --source-run-id $Spec.run_ids.dose_text --prior-run-id $Spec.run_ids.original_priors --meeting-numbers $Meetings --iterations $DoseDraws --roles $DoseRoles --forecaster-types $Profiles --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0
  if ($LASTEXITCODE -ne 0) { throw 'Dose common-prior preparation failed.' }
  & $PythonExecutable @Cli execute-common-prior-agents-db --database $B --run-id $Spec.run_ids.common_prior_dose --runner codex --allow-real-llm --max-workers 2 --timeout-seconds 300
  if ($LASTEXITCODE -ne 0) { throw 'Dose common-prior execution failed.' }
  & $PythonExecutable @Cli audit-common-prior-db --database $B --run-id $Spec.run_ids.common_prior_dose --prior-run-id $Spec.run_ids.original_priors
  if ($LASTEXITCODE -ne 0) { throw 'Dose common-prior audit failed.' }
  & $PythonExecutable @Cli prepare-arm-specific-priors-db --database $B --run-id $Spec.run_ids.recursive_dose --source-run-id $Spec.run_ids.dose_text --meeting-numbers $Meetings --iterations $DoseDraws --roles $DoseRoles --forecaster-types $Profiles --initial-expectation $Spec.initial_expectation --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0
  if ($LASTEXITCODE -ne 0) { throw 'Recursive preparation failed.' }
  & $PythonExecutable @Cli execute-arm-specific-priors-db --database $B --run-id $Spec.run_ids.recursive_dose --runner codex --allow-real-llm --max-workers 2 --timeout-seconds 300
  if ($LASTEXITCODE -ne 0) { throw 'Recursive execution failed.' }
  & $PythonExecutable @Cli audit-arm-specific-priors-db --database $B --run-id $Spec.run_ids.recursive_dose
  if ($LASTEXITCODE -ne 0) { throw 'Recursive audit failed.' }

  & $PythonExecutable (Join-Path $SourceRoot 'code/collection/scripts/run_v10_referee_step3_blinded_validation.py') --root $SourceRoot --run-id $Spec.run_ids.blinded_scoring --source-run-id $Spec.run_ids.dose_text --runner codex --allow-real-llm --model $Spec.model --reasoning-effort $Spec.reasoning_effort --temperature 1.0 --max-workers 2 --stage all
  if ($LASTEXITCODE -ne 0) { throw 'Selected blinded validation failed.' }
  & $RscriptExecutable (Join-Path $SourceRoot 'code/collection/scripts/migrate_blinded_validation_to_db.R') --database $B --run-id $Spec.run_ids.blinded_scoring --source-run-id $Spec.run_ids.dose_text --validation-dir $Validation
  if ($LASTEXITCODE -ne 0) { throw 'Selected blinded import failed.' }
  & $PythonExecutable (Join-Path $SourceRoot 'code/collection/scripts/compute_referee_embeddings_db.py') --database $B --source-run-ids ($Spec.run_ids.main_text + ',' + $Spec.run_ids.dose_text)
  if ($LASTEXITCODE -ne 0) { throw 'Selected embeddings failed.' }
  Write-Output "Collection and scoped audits completed under $RunRoot. Candidate export and analysis remain separate stages."
}
finally {
  $env:PYTHONPATH = $PriorPythonPath
  $env:REPLICATION_SPEC_PATH = $PriorSpecPath
  $env:PYTHONDONTWRITEBYTECODE = $PriorBytecode
}
