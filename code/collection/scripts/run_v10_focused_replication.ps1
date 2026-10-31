<#
Reconstructed prospective-collection launcher.

The original run_v10_focused_replication.ps1 was unavailable when replication
package 2.0.0 was assembled. This reconstruction uses the retained parameter
wrapper, prompt_iv CLI, lower-level modules, collection documentation, and
frozen run inventory. It is not represented as the historical production
script and cannot make a future remote collection byte-identical to the frozen
one.

The default is plan-only. For a new run ID, it prepares the deterministic
rewrite manifest and a collection_plan.json file, but it does not call a remote
model. The frozen historical run ID is immutable in every mode. Real
collection also requires -AllowRemoteCalls.
#>

param(
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$RunId,
  [ValidateRange(1, 1000)] [int]$Iterations = 5,
  [ValidateRange(1, 100)] [int]$Parts = 5,
  [ValidateRange(1, 1000000)] [int]$RewriteChunkSize = 40,
  [ValidateRange(1, 128)] [int]$RewriteWorkers = 2,
  [ValidateRange(1, 1000000)] [int]$AgentChunkSize = 40,
  [ValidateRange(1, 128)] [int]$AgentWorkers = 2,
  [ValidateNotNullOrEmpty()] [string]$Model = "gpt-5.5",
  [ValidateNotNullOrEmpty()] [string]$ReasoningEffort = "high",
  [ValidateNotNullOrEmpty()] [string]$PromptVersion = "calibrated_symmetric_rebalance_v10",
  [double]$RewriteTemperature = 1.0,
  [double]$AgentTemperature = 1.0,
  [ValidateRange(1, 86400)] [int]$TimeoutSeconds = 300,
  [ValidateRange(0, 1000000)] [int]$MaxRewriteChunks = 0,
  [ValidateRange(0, 1000000)] [int]$MaxAgentChunks = 0,
  [switch]$StopAfterGeneration,
  [switch]$AllowRemoteCalls,
  [string]$PythonExecutable = $env:PROMPT_IV_PYTHON
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$FrozenRunId = "v10_referee_d5_25draws_20260620"
$PackageRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))
$SourceRoot = Join-Path $PackageRoot "code\collection\src"
$RunBase = [System.IO.Path]::GetFullPath((Join-Path $PackageRoot "data\interim\replications"))
$RunDir = [System.IO.Path]::GetFullPath((Join-Path $RunBase $RunId))
$RunBasePrefix = $RunBase.TrimEnd('\') + '\'

if (-not $RunDir.StartsWith($RunBasePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "RunId resolves outside data/interim/replications: $RunId"
}
if ($RunId -eq $FrozenRunId) {
  throw "The frozen historical run ID '$FrozenRunId' is immutable. Supply a new RunId for plan-only or remote execution."
}

$FrozenDatabase = [System.IO.Path]::GetFullPath((Join-Path $PackageRoot "data\generated\prompt_iv_replication_source.sqlite"))
if ($RunDir.StartsWith((Split-Path $FrozenDatabase -Parent), [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "The prospective run directory cannot be placed under the frozen data/generated directory."
}

if ([string]::IsNullOrWhiteSpace($PythonExecutable)) {
  $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if (-not $PythonCommand) {
    throw "Set PROMPT_IV_PYTHON or pass -PythonExecutable with a Python 3.10+ interpreter."
  }
  $PythonExecutable = $PythonCommand.Source
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
  throw "Python executable not found: $PythonExecutable"
}
if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot "prompt_iv\cli.py") -PathType Leaf)) {
  throw "Packaged prompt_iv CLI not found under $SourceRoot"
}

function Invoke-PromptIv {
  param([Parameter(Mandatory = $true)] [string[]]$Arguments)
  Write-Output ("prompt-iv " + ($Arguments -join " "))
  & $PythonExecutable -m prompt_iv.cli --root $PackageRoot @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "prompt_iv command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
  }
}

function Assert-ExistingConfigMatches {
  param([Parameter(Mandatory = $true)] [string]$ConfigPath)
  $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
  $checks = [ordered]@{
    run_id = $RunId
    iterations = $Iterations
    parts = $Parts
    model = $Model
    reasoning_effort = $ReasoningEffort
    prompt_version = $PromptVersion
    temperature = $RewriteTemperature
  }
  foreach ($entry in $checks.GetEnumerator()) {
    $actual = $config.($entry.Key)
    if ([string]$actual -ne [string]$entry.Value) {
      throw "Existing run config mismatch for $($entry.Key): expected '$($entry.Value)', found '$actual'."
    }
  }
}

function Assert-ExistingPlanMatches {
  param([Parameter(Mandatory = $true)] [pscustomobject]$ExistingPlan)
  $checks = [ordered]@{
    model = $Model
    reasoning_effort = $ReasoningEffort
    prompt_version = $PromptVersion
    rewrite_temperature_requested = $RewriteTemperature
    agent_temperature_requested = $AgentTemperature
    rewrite_chunk_size = $RewriteChunkSize
    rewrite_workers = $RewriteWorkers
    agent_chunk_size = $AgentChunkSize
    agent_workers = $AgentWorkers
    timeout_seconds = $TimeoutSeconds
    max_rewrite_chunks = $MaxRewriteChunks
    max_agent_chunks = $MaxAgentChunks
    stop_after_generation = [bool]$StopAfterGeneration
  }
  foreach ($entry in $checks.GetEnumerator()) {
    $actual = $ExistingPlan.parameters.($entry.Key)
    if ([string]$actual -ne [string]$entry.Value) {
      throw "Existing collection plan mismatch for $($entry.Key): expected '$($entry.Value)', found '$actual'. Use a new RunId."
    }
  }
}

function Get-TaskCompletion {
  param(
    [Parameter(Mandatory = $true)] [string]$ManifestPath,
    [Parameter(Mandatory = $true)] [string]$ResultsPath
  )
  $manifestIds = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
  foreach ($line in Get-Content -LiteralPath $ManifestPath) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $row = $line | ConvertFrom-Json
    if ($null -ne $row.task_id) { [void]$manifestIds.Add([string]$row.task_id) }
  }
  $latest = @{}
  if (Test-Path -LiteralPath $ResultsPath -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $ResultsPath) {
      if ([string]::IsNullOrWhiteSpace($line)) { continue }
      $row = $line | ConvertFrom-Json
      if ($null -ne $row.task_id) { $latest[[string]$row.task_id] = $row }
    }
  }
  $completed = 0
  $bad = @()
  foreach ($taskId in $manifestIds) {
    if (-not $latest.ContainsKey($taskId)) { continue }
    $row = $latest[$taskId]
    if ([string]$row.status -eq "completed") { $completed += 1 }
    else { $bad += $row }
  }
  [pscustomobject]@{
    total = $manifestIds.Count
    completed = $completed
    missing = $manifestIds.Count - $completed - $bad.Count
    bad = $bad.Count
    bad_statuses = @($bad | Group-Object status | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ", "
  }
}

function Write-PlanFile {
  param(
    [Parameter(Mandatory = $true)] [string]$Path,
    [Parameter(Mandatory = $true)] [string]$Content
  )
  $temporaryPath = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
  try {
    [System.IO.File]::WriteAllText($temporaryPath, $Content, [System.Text.UTF8Encoding]::new($false))
    for ($attempt = 1; $attempt -le 5; $attempt += 1) {
      try {
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
        return
      }
      catch {
        if ($attempt -eq 5) { throw }
        Start-Sleep -Milliseconds 250
      }
    }
  }
  finally {
    if (Test-Path -LiteralPath $temporaryPath) { Remove-Item -LiteralPath $temporaryPath -Force }
  }
}

$RewriteLimit = $null
if ($MaxRewriteChunks -gt 0) {
  $RewriteLimit64 = [int64]$RewriteChunkSize * [int64]$MaxRewriteChunks
  if ($RewriteLimit64 -gt [int]::MaxValue) { throw "Rewrite task limit exceeds the CLI integer range." }
  $RewriteLimit = [int]$RewriteLimit64
}
$AgentLimit = $null
if ($MaxAgentChunks -gt 0) {
  $AgentLimit64 = [int64]$AgentChunkSize * [int64]$MaxAgentChunks
  if ($AgentLimit64 -gt [int]::MaxValue) { throw "Agent task limit exceeds the CLI integer range." }
  $AgentLimit = [int]$AgentLimit64
}

$RewriteManifest = Join-Path $RunDir "rewrite_manifest.jsonl"
$RewriteResults = Join-Path $RunDir "rewrite_results.jsonl"
$CounterfactualCorpus = Join-Path $RunDir "counterfactual_corpus.csv"
$QualityReport = Join-Path $RunDir "counterfactual_quality_report.json"
$QualityDetails = Join-Path $RunDir "counterfactual_quality_details.csv"
$ToneScores = Join-Path $RunDir "counterfactual_tone_scores.csv"
$AgentManifest = Join-Path $RunDir "counterfactual_agent_manifest.jsonl"
$AgentResults = Join-Path $RunDir "counterfactual_agent_results.jsonl"
$AgentTextDir = Join-Path $RunDir "agent_texts"
$AgentLong = Join-Path $RunDir "agent_results_long.csv"
$AgentWide = Join-Path $RunDir "agent_results_wide.csv"
$RunDatabase = Join-Path $RunDir "prompt_iv.sqlite"
$ConfigPath = Join-Path $RunDir "replication_config.json"
$PlanPath = Join-Path $RunDir "collection_plan.json"

$PriorPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $SourceRoot
try {
  if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    Assert-ExistingConfigMatches -ConfigPath $ConfigPath
    if (-not (Test-Path -LiteralPath $RewriteManifest -PathType Leaf)) {
      throw "Existing run config has no rewrite manifest: $RewriteManifest"
    }
    Write-Output "Reusing the matching manifest and config for resumable run '$RunId'."
  }
  else {
    if ((Test-Path -LiteralPath $RunDir) -and (Get-ChildItem -LiteralPath $RunDir -Force | Select-Object -First 1)) {
      throw "Run directory exists without a resumable replication_config.json: $RunDir"
    }
    Invoke-PromptIv -Arguments @(
      "prepare-replication", "--run-id", $RunId,
      "--iterations", [string]$Iterations, "--parts", [string]$Parts,
      "--model", $Model, "--reasoning-effort", $ReasoningEffort,
      "--temperature", [string]$RewriteTemperature,
      "--prompt-version", $PromptVersion
    )
  }

  $RewriteCommand = @(
    "execute-counterfactuals", "--manifest", $RewriteManifest,
    "--output", $RewriteResults, "--runner", "codex", "--allow-real-llm",
    "--model", $Model, "--reasoning-effort", $ReasoningEffort,
    "--temperature", [string]$RewriteTemperature,
    "--timeout-seconds", [string]$TimeoutSeconds, "--max-retries", "2",
    "--max-workers", [string]$RewriteWorkers
  )
  if ($null -ne $RewriteLimit) { $RewriteCommand += @("--limit", [string]$RewriteLimit) }

  $AgentCommand = @(
    "execute-agents", "--manifest", $AgentManifest,
    "--output", $AgentResults, "--runner", "codex", "--allow-real-llm",
    "--model", $Model, "--reasoning-effort", $ReasoningEffort,
    "--temperature", [string]$AgentTemperature,
    "--timeout-seconds", [string]$TimeoutSeconds,
    "--max-workers", [string]$AgentWorkers
  )
  if ($null -ne $AgentLimit) { $AgentCommand += @("--limit", [string]$AgentLimit) }

  $GenerationStages = @(
    [ordered]@{ name = "prepare rewrites"; command = @("prepare-replication"); output = $RewriteManifest },
    [ordered]@{ name = "execute rewrites with numeric retries"; command = $RewriteCommand; output = $RewriteResults },
    [ordered]@{ name = "require complete valid rewrite results"; command = @("launcher completion gate"); output = $RewriteResults },
    [ordered]@{ name = "assemble rewrites"; command = @("assemble-counterfactuals", "--results", $RewriteResults, "--output", $CounterfactualCorpus); output = $CounterfactualCorpus },
    [ordered]@{ name = "audit rewrite quality"; command = @("evaluate-counterfactuals", "--results", $RewriteResults, "--output", $QualityReport, "--details-output", $QualityDetails); output = $QualityReport },
    [ordered]@{ name = "score rewrite tone"; command = @("score-counterfactuals", "--input", $CounterfactualCorpus, "--output", $ToneScores); output = $ToneScores }
  )
  $ForecasterStages = @(
    [ordered]@{ name = "prepare synthetic forecasters"; command = @("prepare-counterfactual-agents", "--corpus", $CounterfactualCorpus, "--output", $AgentManifest, "--text-dir", $AgentTextDir); output = $AgentManifest },
    [ordered]@{ name = "execute synthetic forecasters"; command = $AgentCommand; output = $AgentResults },
    [ordered]@{ name = "require complete valid synthetic-forecaster results"; command = @("launcher completion gate"); output = $AgentResults },
    [ordered]@{ name = "assemble synthetic forecasters"; command = @("assemble-counterfactual-agents", "--results", $AgentResults, "--long-output", $AgentLong, "--wide-output", $AgentWide); output = $AgentWide },
    [ordered]@{ name = "materialize run-specific database"; command = @("sync-database", "--output", $RunDatabase, "--run-dir", $RunDir); output = $RunDatabase }
  )
  $PlannedStages = if ($StopAfterGeneration) { $GenerationStages } else { @($GenerationStages) + @($ForecasterStages) }

  $Plan = [ordered]@{
    reconstruction_notice = "The original base launcher was unavailable. This prospective command plan is reconstructed from package 2.0.0 evidence."
    historical_identity_claimed = $false
    remote_calls_enabled = [bool]$AllowRemoteCalls
    frozen_run_id = $FrozenRunId
    requested_run_id = $RunId
    frozen_database = $FrozenDatabase
    frozen_database_is_output = $false
    run_directory = $RunDir
    parameters = [ordered]@{
      iterations = $Iterations; parts = $Parts
      rewrite_chunk_size = $RewriteChunkSize; rewrite_workers = $RewriteWorkers
      agent_chunk_size = $AgentChunkSize; agent_workers = $AgentWorkers
      model = $Model; reasoning_effort = $ReasoningEffort
      prompt_version = $PromptVersion
      rewrite_temperature_requested = $RewriteTemperature
      agent_temperature_requested = $AgentTemperature
      timeout_seconds = $TimeoutSeconds
      max_rewrite_chunks = $MaxRewriteChunks; max_agent_chunks = $MaxAgentChunks
      rewrite_task_limit = $RewriteLimit; agent_task_limit = $AgentLimit
      stop_after_generation = [bool]$StopAfterGeneration
    }
    reconstruction_choices = @(
      "Chunk size times maximum chunks is mapped to the packaged CLI task limit. With a zero maximum, the CLI consumes all pending tasks.",
      "The file-based resumable JSONL pipeline is used because it matches the retained replication-run layout.",
      "The package's R analysis remains separate from prospective remote recollection. This launcher ends after assembling agent outputs and a run-specific SQLite snapshot."
    )
    completion_policy = "Only status=completed counts. Failed, numeric_failed, parse_failed, or usage_limited records block downstream stages. A requested task limit may stop a resumable batch before assembly, but it is never reported as a completed collection."
    stages = $PlannedStages
  }
  $PlanJson = $Plan | ConvertTo-Json -Depth 8
  if (Test-Path -LiteralPath $PlanPath -PathType Leaf) {
    $ExistingPlan = Get-Content -LiteralPath $PlanPath -Raw | ConvertFrom-Json
    Assert-ExistingPlanMatches -ExistingPlan $ExistingPlan
    if (($ExistingPlan | ConvertTo-Json -Depth 8) -ne $PlanJson) {
      Write-PlanFile -Path $PlanPath -Content $PlanJson
    }
  }
  else {
    Write-PlanFile -Path $PlanPath -Content $PlanJson
  }

  if (-not $AllowRemoteCalls) {
    Write-Output "Plan-only mode completed. No remote model call was issued."
    Write-Output "Review the reconstructed command plan at $PlanPath"
    return
  }

  Invoke-PromptIv -Arguments $RewriteCommand
  $RewriteState = Get-TaskCompletion -ManifestPath $RewriteManifest -ResultsPath $RewriteResults
  if ($RewriteState.bad -gt 0) {
    throw "Rewrite completion gate failed: $($RewriteState.bad) non-completed result(s) [$($RewriteState.bad_statuses)]."
  }
  if ($RewriteState.missing -gt 0) {
    if ($null -ne $RewriteLimit) {
      Write-Output "Rewrite batch stopped at the requested task limit: $($RewriteState.completed)/$($RewriteState.total) complete. Rerun the same configuration to resume; downstream stages were not started."
      return
    }
    throw "Rewrite completion gate failed: $($RewriteState.missing) of $($RewriteState.total) task(s) have no completed result."
  }
  Invoke-PromptIv -Arguments @("assemble-counterfactuals", "--results", $RewriteResults, "--output", $CounterfactualCorpus)
  Invoke-PromptIv -Arguments @("evaluate-counterfactuals", "--results", $RewriteResults, "--output", $QualityReport, "--details-output", $QualityDetails)
  Invoke-PromptIv -Arguments @("score-counterfactuals", "--input", $CounterfactualCorpus, "--output", $ToneScores)

  if ($StopAfterGeneration) {
    Write-Output "StopAfterGeneration requested. Rewrite assembly and scoring are complete."
    return
  }

  Invoke-PromptIv -Arguments @(
    "prepare-counterfactual-agents", "--corpus", $CounterfactualCorpus,
    "--output", $AgentManifest, "--text-dir", $AgentTextDir,
    "--scenarios", "hawk,dove", "--forecaster-types", "anchored,unanchored",
    "--num-agents", "1", "--initial-expectation", "3.8", "--random-seed", "123"
  )
  Invoke-PromptIv -Arguments $AgentCommand
  $AgentState = Get-TaskCompletion -ManifestPath $AgentManifest -ResultsPath $AgentResults
  if ($AgentState.bad -gt 0) {
    throw "Synthetic-forecaster completion gate failed: $($AgentState.bad) non-completed result(s) [$($AgentState.bad_statuses)]."
  }
  if ($AgentState.missing -gt 0) {
    if ($null -ne $AgentLimit) {
      Write-Output "Synthetic-forecaster batch stopped at the requested task limit: $($AgentState.completed)/$($AgentState.total) complete. Rerun the same configuration to resume; assembly and database sync were not started."
      return
    }
    throw "Synthetic-forecaster completion gate failed: $($AgentState.missing) of $($AgentState.total) task(s) have no completed result."
  }
  Invoke-PromptIv -Arguments @("assemble-counterfactual-agents", "--results", $AgentResults, "--long-output", $AgentLong, "--wide-output", $AgentWide)
  Invoke-PromptIv -Arguments @("sync-database", "--output", $RunDatabase, "--run-dir", $RunDir)
  Write-Output "Prospective collection completed under new run ID '$RunId'."
}
finally {
  $env:PYTHONPATH = $PriorPythonPath
}
