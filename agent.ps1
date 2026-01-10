# agent.ps1
# Near-autonomous local executor with approval gates, progress beacons, and health waits.
# PowerShell 7+, ASCII-only.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Paths ---
$Root    = (Get-Location).Path
$Agent   = Join-Path $Root "agent"
$Inbox   = Join-Path $Agent "inbox"
$Outbox  = Join-Path $Agent "outbox"
$LogsDir = Join-Path $Root "logs"

foreach ($p in @($Agent,$Inbox,$Outbox,$LogsDir)) { if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null } }

# --- Progress beacons ---
function Write-Stage {
    param(
        [Parameter(Mandatory)][string]$Stage,   # plan|step|build|up|health|heal|done|fail|wait
        [Parameter(Mandatory)][string]$Status,  # start|ok|fail|info|pause
        [string]$Message = "",
        $Data = $null
    )
    $payload = [pscustomobject]@{
        ts    = (Get-Date).ToString("o")
        stage = $Stage
        status= $Status
        msg   = $Message
        host  = $env:COMPUTERNAME
        stack = $(Get-Location).Path | Split-Path -Leaf
    }
    if ($Data) { $payload | Add-Member -NotePropertyName data -NotePropertyValue $Data }
    $line = $payload | ConvertTo-Json -Depth 6 -Compress
    Add-Content -Path (Join-Path $Outbox "progress.ndjson") -Value $line
    Write-Host "[$($payload.ts)] $($payload.stage):$($payload.status) $($payload.msg)"
}

# --- Health waits / status ---
function Wait-Health {
    param([string[]]$Services, [int]$TimeoutSec = 180, [int]$PollSec = 2)
    foreach ($svc in $Services) {
        Write-Stage "health" "start" $svc
        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        do {
            $id = (docker compose ps -q $svc) 2>$null
            if ([string]::IsNullOrWhiteSpace($id)) { Start-Sleep -Seconds $PollSec; continue }
            $state = (docker inspect --format '{{.State.Health.Status}}' $id) 2>$null
            if ($state -eq "healthy") { Write-Stage "health" "ok" $svc; break }
            if ($state -eq "unhealthy") { Write-Stage "health" "info" "$svc unhealthy; retrying..." }
            if ((Get-Date) -gt $deadline) { Write-Stage "health" "fail" "$svc timed out"; throw "Health timeout: $svc" }
            Start-Sleep -Seconds $PollSec
        } while ($true)
    }
}

function Print-StatusTable {
    Write-Host ""
    Write-Host "SERVICE            STATE     HEALTH     PORTS"
    Write-Host "-----------------------------------------------"
    $lines = docker compose ps --format "{{.Name}}|{{.State}}|{{.Health}}|{{.Ports}}"
    $lines | ForEach-Object {
        $n,$s,$h,$p = $_ -split '\|',4
        "{0,-18} {1,-8} {2,-9} {3}" -f $n,$s,$h,$p
    }
}

function Heal-Unhealthy {
    $rows = docker ps --format "{{.ID}}|{{.Names}}"
    foreach ($row in $rows) {
        $id,$name = $row -split '\|',2
        $health = (docker inspect --format '{{.State.Health.Status}}' $id) 2>$null
        if ($health -eq 'unhealthy') {
            Write-Stage "heal" "start" $name
            docker restart $id | Out-Null
            Start-Sleep -Seconds 5
            $post = (docker inspect --format '{{.State.Health.Status}}' $id) 2>$null
            if ($post -eq 'healthy') { Write-Stage "heal" "ok" $name } else { Write-Stage "heal" "fail" "$name still $post" }
        }
    }
}

# --- Approvals ---
function Require-Approval {
    param([string]$Reason, [pscustomobject]$Step)
    $pending = [pscustomobject]@{
        ts         = (Get-Date).ToString("o")
        reason     = $Reason  # legal|payment|critical
        stepId     = $Step.id
        stepDesc   = $Step.desc
        command    = $Step.shell
        details    = $Step.details
        howToApprove = "Create a file: $($Inbox)\approve.ok (or reject.no) to proceed or abort this step."
    }
    $file = Join-Path $Outbox "pending-approval.json"
    $pending | ConvertTo-Json -Depth 6 | Out-File -FilePath $file -Encoding ASCII
    Write-Stage "step" "pause" "$Reason approval required for $($Step.id)"
    Write-Host ""
    Write-Host "=== APPROVAL REQUIRED: $Reason ==="
    Write-Host "Step: $($Step.id) - $($Step.desc)"
    if ($Step.details) { Write-Host "Details: $($Step.details)" }
    Write-Host "Command: $($Step.shell)"
    Write-Host "Approve: create $($Inbox)\approve.ok"
    Write-Host "Reject:  create $($Inbox)\reject.no"
    Write-Host "Waiting for your decision..."
    while ($true) {
        if (Test-Path (Join-Path $Inbox "approve.ok")) { Remove-Item (Join-Path $Inbox "approve.ok"); Write-Stage "step" "info" "Approval granted"; break }
        if (Test-Path (Join-Path $Inbox "reject.no"))  { Remove-Item (Join-Path $Inbox "reject.no"); throw "Approval rejected by user." }
        Start-Sleep -Seconds 2
    }
}

# --- Command execution ---
function Invoke-StepCommand {
    param([pscustomobject]$Step)
    $logFile = Join-Path $LogsDir ("step-" + $Step.id + ".log")
    Write-Stage "step" "start" "$($Step.id): $($Step.desc)"
    # Use nested PowerShell for consistent parsing and to capture streams.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = (Get-Command pwsh).Source
    $psi.Arguments = "-NoProfile -Command `"${($Step.shell)}`""
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $stdOut = $proc.StandardOutput.ReadToEndAsync()
    $stdErr = $proc.StandardError.ReadToEndAsync()
    $proc.WaitForExit()
    $out = $stdOut.Result
    $err = $stdErr.Result
    $rc  = $proc.ExitCode

    $out | Out-File -FilePath $logFile -Encoding ASCII
    if ($err -and $err.Trim().Length -gt 0) { Add-Content -Path $logFile -Value "`n--- STDERR ---`n$err" }

    if ($rc -ne 0) {
        Write-Stage "step" "fail" "$($Step.id) exit $rc (see $($logFile))"
        if ($Step.onFail -and $Step.onFail.action -eq "retry" -and $Step.onFail.retries -gt 0) {
            $retries = [int]$Step.onFail.retries
            $backoff = [int]($Step.onFail.backoffSec | ForEach-Object { if ($_ -is [int]) { $_ } else { 5 } })
            for ($i=1; $i -le $retries; $i++) {
                Write-Stage "step" "info" "retry $i/$retries after $backoff s"
                Start-Sleep -Seconds $backoff
                return Invoke-StepCommand -Step $Step
            }
        }
        if ($Step.onFail -and $Step.onFail.action -eq "continue") {
            Write-Stage "step" "info" "continuing despite failure"
            return $true
        }
        throw "Step failed: $($Step.id)"
    } else {
        Write-Stage "step" "ok" "$($Step.id)"
        return $true
    }
}

# --- Plan execution ---
function Load-Plan {
    $planPath = Join-Path $Inbox "plan.json"
    if (-not (Test-Path $planPath)) { throw "No plan found at $planPath. Place plan.json into agent/inbox." }
    $json = Get-Content -Raw -Path $planPath
    return $json | ConvertFrom-Json -Depth 7
}

function Save-State {
    param($State)
    $State | ConvertTo-Json -Depth 7 | Out-File -FilePath (Join-Path $Outbox "state.json") -Encoding ASCII
}

try {
    Write-Stage "plan" "start" "loading plan"
    $plan = Load-Plan
    Write-Stage "plan" "ok" "plan: $($plan.name) steps: $($plan.steps.Count)"

    $state = [pscustomobject]@{ started = (Get-Date).ToString("o"); completed = @() }
    foreach ($step in $plan.steps) {
        if ($state.completed -contains $step.id) { continue }

        # Gate by approval
        if ($step.approval -and $step.approval -in @("legal","payment","critical")) {
            Require-Approval -Reason $step.approval -Step $step
        }

        # Special step types
        if ($step.type -eq "waitHealth") {
            Wait-Health -Services $step.services -TimeoutSec ($step.timeoutSec | ForEach-Object { if ($_){$_} else {180} })
        } elseif ($step.type -eq "printStatus") {
            Print-StatusTable
        } elseif ($step.type -eq "healUnhealthy") {
            Heal-Unhealthy
        } else {
            # Default: run command
            Invoke-StepCommand -Step $step | Out-Null
        }

        # Mark complete
        $state.completed += $step.id
        Save-State -State $state
    }

    Print-StatusTable
    Write-Stage "done" "ok" "all steps completed"
    exit 0
}
catch {
    Write-Stage "fail" "fail" $_.Exception.Message
    exit 1
}
