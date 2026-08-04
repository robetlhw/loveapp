[CmdletBinding()]
param(
    [string]$UserId = "local-user",
    [Parameter(Mandatory = $true)]
    [string]$RelationshipId,
    [string]$ConversationId,
    [ValidateSet(
        "unknown",
        "stranger",
        "acquaintance",
        "ambiguous",
        "dating",
        "stable_relationship",
        "long_distance",
        "breakup"
    )]
    [string]$Stage = "unknown",
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$memoryDebugScript = Join-Path $PSScriptRoot "memory_debug.ps1"

Push-Location $repoRoot
try {
    if (-not $SkipDocker) {
        & docker compose up -d
        if ($LASTEXITCODE -ne 0) {
            throw "Docker is unavailable. Start Docker Desktop and run this script again."
        }
    }

    $watchArguments = @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $memoryDebugScript + '"'),
        "-UserId", $UserId,
        "-RelationshipId", $RelationshipId,
        "-Mode", "watch",
        "-IncludeInactive"
    )
    if ($ConversationId) {
        $watchArguments += @("-ConversationId", $ConversationId)
    }
    $startParameters = @{
        FilePath = "powershell.exe"
        ArgumentList = $watchArguments
        WorkingDirectory = $repoRoot
        PassThru = $true
    }
    $watcher = Start-Process @startParameters
    Write-Host "Memory watcher started (PID $($watcher.Id))." -ForegroundColor Green

    $chatArguments = @(
        "run", "loveapp", "chat",
        "--user-id", $UserId,
        "--relationship-id", $RelationshipId,
        "--stage", $Stage,
        "--debug-memory",
        "--debug-route",
        "--stream",
        "--timings"
    )
    if ($ConversationId) {
        $chatArguments += @("--conversation-id", $ConversationId)
    }

    try {
        $Host.UI.RawUI.WindowTitle = "LoveApp Chat - $RelationshipId"
    }
    catch {
        # Some non-interactive hosts do not expose a writable window title.
    }
    & uv @chatArguments
    if ($LASTEXITCODE -ne 0) {
        throw "LoveApp chat failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
