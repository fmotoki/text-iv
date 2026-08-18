param(
  [int]$RewriteChunkSize = 40,
  [int]$RewriteWorkers = 2,
  [int]$AgentChunkSize = 40,
  [int]$AgentWorkers = 2,
  [string]$Model = "gpt-5.5",
  [string]$ReasoningEffort = "high",
  [int]$TimeoutSeconds = 300,
  [int]$MaxRewriteChunks = 0,
  [int]$MaxAgentChunks = 0,
  [switch]$StopAfterGeneration
)

$ErrorActionPreference = "Stop"

$BaseScript = Join-Path $PSScriptRoot "run_v10_focused_replication.ps1"
$Parameters = @{
  RunId = "v10_referee_d5_25draws_20260620"
  Iterations = 25
  Parts = 5
  RewriteChunkSize = $RewriteChunkSize
  RewriteWorkers = $RewriteWorkers
  AgentChunkSize = $AgentChunkSize
  AgentWorkers = $AgentWorkers
  Model = $Model
  ReasoningEffort = $ReasoningEffort
  PromptVersion = "calibrated_symmetric_rebalance_v10"
  RewriteTemperature = 1.0
  AgentTemperature = 1.0
  TimeoutSeconds = $TimeoutSeconds
  MaxRewriteChunks = $MaxRewriteChunks
  MaxAgentChunks = $MaxAgentChunks
}

if ($StopAfterGeneration) {
  $Parameters["StopAfterGeneration"] = $true
}

& $BaseScript @Parameters
