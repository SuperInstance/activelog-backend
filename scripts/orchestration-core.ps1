<#
    orchestration-core.ps1
    Shared cold-boot safe orchestration logic for all stacks
#>

function Start-DockerIfNeeded {
    Write-Host "?? Ensuring Docker Desktop is running..." -ForegroundColor Cyan
    if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
        Start-Process "Docker Desktop"
        Write-Host "? Waiting for Docker Desktop to start..."
        Start-Sleep -Seconds 10
        while (-not (docker info 2>$null)) {
            Start-Sleep -Seconds 2
        }
    }
    Write-Host "? Docker is running." -ForegroundColor Green
}

function Wait-ForServiceHealth {
    param (
        [Parameter(Mandatory)]
        [string[]]$ServiceNames
    )
    foreach ($service in $ServiceNames) {
        Write-Host "?? Waiting for $service to report healthy..."
        while ($true) {
            $status = docker inspect --format='{{.State.Health.Status}}' $service 2>$null
            if ($status -eq "healthy") {
                Write-Host "? $service is healthy." -ForegroundColor Green
                break
            }
            Start-Sleep -Seconds 2
        }
    }
}  # ? Properly closed

function Show-StackStatus {
    Write-Host "`n?? Final container status:" -ForegroundColor Yellow
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
}

function Deploy-Stack {
    param (
        [string]$ComposeFile = "docker-compose.yml",
        [string[]]$HealthServices
    )

    Start-DockerIfNeeded

    Write-Host "?? Building and starting containers..."
    docker compose -f $ComposeFile up -d --build

    if ($HealthServices.Count -gt 0) {
        Wait-ForServiceHealth -ServiceNames $HealthServices
    }

    Show-StackStatus
}
