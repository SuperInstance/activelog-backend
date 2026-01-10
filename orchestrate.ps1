Start-Transcript -Path "C:\Logs\orchestration.log" -Append

Write-Host "`nChecking container health..."
$containers = @("activelog-db", "activelog-api", "activelog-worker")
$unhealthy = @()

foreach ($name in $containers) {
    $status = docker inspect -f '{{.State.Health.Status}}' $name 2>$null
    if ($status -ne "healthy" -and $status -ne "running") {
        Write-Host "[WARN] $name is not healthy: $status"
        $unhealthy += $name
    } else {
        Write-Host "[OK] $name is healthy"
    }
}

if ($unhealthy.Count -gt 0) {
    Write-Host "`nRestarting unhealthy containers..."
    foreach ($name in $unhealthy) {
        docker restart $name | Out-Null
        Write-Host "[RESTART] $name restarted"
    }
}

Write-Host "`nTriggering downstream workflows..."

# Trigger ledger sync
try {
    Invoke-WebRequest -Uri "http://localhost:8000/ledger/sync" -Method POST -UseBasicParsing -TimeoutSec 5
    Write-Host "[OK] Ledger sync triggered"
} catch {
    Write-Host "[FAIL] Ledger sync failed"
}

# Trigger agent heartbeat
try {
    Invoke-WebRequest -Uri "http://localhost:8000/agents/heartbeat" -Method POST -UseBasicParsing -TimeoutSec 5
    Write-Host "[OK] Agent heartbeat triggered"
} catch {
    Write-Host "[FAIL] Agent heartbeat failed"
}

Write-Host "`nOrchestration complete."
Stop-Transcript