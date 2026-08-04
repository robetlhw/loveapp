[CmdletBinding()]
param(
    [string]$UserId = "local-user",
    [Parameter(Mandatory = $true)]
    [string]$RelationshipId,
    [string]$ConversationId,
    [ValidateSet("snapshot", "watch", "context", "list", "runs", "show")]
    [string]$Mode = "snapshot",
    [string]$MemoryId,
    [ValidateRange(1, 500)]
    [int]$Limit = 50,
    [ValidateRange(0.5, 30)]
    [double]$Interval = 1,
    [switch]$IncludeInactive,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Invoke-MemoryCommand {
    param([string[]]$CommandArguments)

    & uv run loveapp memory @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "LoveApp memory command failed with exit code $LASTEXITCODE."
    }
}

function Get-ScopeArguments {
    return @(
        "--user-id", $UserId,
        "--relationship-id", $RelationshipId
    )
}

function Get-RunArguments {
    $arguments = @(Get-ScopeArguments)
    if ($ConversationId) {
        $arguments += @("--conversation-id", $ConversationId)
    }
    return $arguments
}

Push-Location $repoRoot
try {
    try {
        $Host.UI.RawUI.WindowTitle = "LoveApp Memory - $RelationshipId"
    }
    catch {
        # Some non-interactive hosts do not expose a writable window title.
    }

    switch ($Mode) {
        "context" {
            Invoke-MemoryCommand -CommandArguments (@("context") + (Get-ScopeArguments))
        }
        "list" {
            $arguments = @("list") + (Get-ScopeArguments) + @("--limit", $Limit.ToString())
            if ($Json) {
                $arguments += "--json"
            }
            Invoke-MemoryCommand -CommandArguments $arguments
        }
        "runs" {
            $arguments = @("runs") + (Get-RunArguments) + @("--limit", $Limit.ToString())
            if ($Json) {
                $arguments += "--json"
            }
            Invoke-MemoryCommand -CommandArguments $arguments
        }
        "show" {
            if (-not $MemoryId) {
                throw "-MemoryId is required when -Mode show is used."
            }
            Invoke-MemoryCommand -CommandArguments @(
                "show", $MemoryId,
                "--user-id", $UserId
            )
        }
        "watch" {
            $intervalText = $Interval.ToString(
                [System.Globalization.CultureInfo]::InvariantCulture
            )
            $arguments = @("watch") + (Get-RunArguments) + @("--interval", $intervalText)
            if ($IncludeInactive) {
                $arguments += "--include-inactive"
            }
            Invoke-MemoryCommand -CommandArguments $arguments
        }
        "snapshot" {
            Write-Host "`n=== Effective context used by agents ===" -ForegroundColor Cyan
            Invoke-MemoryCommand -CommandArguments (@("context") + (Get-ScopeArguments))

            Write-Host "`n=== Persisted memories (all statuses) ===" -ForegroundColor Cyan
            $listArguments = @("list") + (Get-ScopeArguments) + @(
                "--limit", $Limit.ToString()
            )
            Invoke-MemoryCommand -CommandArguments $listArguments

            Write-Host "`n=== Extraction runs ===" -ForegroundColor Cyan
            $runArguments = @("runs") + (Get-RunArguments) + @(
                "--limit", $Limit.ToString()
            )
            Invoke-MemoryCommand -CommandArguments $runArguments
        }
    }
}
finally {
    Pop-Location
}
