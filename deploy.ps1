# Cold-boot-safe, health-aligned deploy script (ASCII-only)
# Usage:
#   .\deploy.ps1
#   .\deploy.ps1 activelog-api activelog-worker
#   .\deploy.ps1 -TimeoutSec 420
#   .\deploy.ps1 -NoBuild
[CmdletBinding()]
param(
    [string[]]$Services,
    [int]$TimeoutSec = 300,
    [switch]$NoBuild
)

function Start-DockerIfNeeded {
    Write-Host "Checking Docker Desktop status..."
    $dockerProcess = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
    if (-not $dockerProcess) {
        Write-Host "Starting Docker Desktop..."
        $dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
        if (-not (Test-Path $dockerExe)) {
            Write-Error "Docker Desktop not found at: $dockerExe"
            throw
        }
        Start-Process $dockerExe | Out-Null
        Write-Host "Waiting for Docker engine..."
        while (-not (docker info --format '{{.ServerVersion}}' 2>$null)) {
            Start-Sleep -Seconds 2
        }
    } else {
        Write-Host "Docker Desktop already running."
    }
}

function Build-And-Start {
    param([string[]]$Svcs, [switch]$NoBuild)

    if ($Svcs -and $Svcs.Count -gt 0) {
        if (-not $NoBuild) {
            Write-Host "Building services: $($Svcs -join ', ')"
            docker compose build $Svcs
        } else {
            Write-Host "Skipping build for services: $($Svcs -join ', ')"
        }
        Write-Host "Starting services: $($Svcs -join ', ')"
        docker compose up -d $Svcs
    } else {
        if (-not $NoBuild) {
            Write-Host "Building all services..."
            docker compose build
        } else {
            Write-Host "Skipping build for all services."
        }
        Write-Host "Starting all services..."
        docker compose up -d
    }
}

function Get-HealthSnapshot {
    # Returns array of objects: @{ Name="container"; Status="running/exited"; Health="healthy/unhealthy/starting/none" }
    $ids = (docker ps -q)
    if (-not $ids) { return @() }
    $fmt = "{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    $lines = docker inspect --format $fmt $ids
    $lines | ForEach-Object {
        $parts = $_.Trim() -replace '^/','' -split '\|'
        [pscustomobject]@{
            Name   = $parts[0]
            Status = $parts[1]
            Health = $parts[2]
        }
    }
}

function Wait-For-Health {
    param([int]$TimeoutSec)

    Write-Host "Waiting for all containers to be healthy (timeout: $TimeoutSec s)..."
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        $snap = Get-HealthSnapshot
        if (-not $snap -or $snap.Count -eq 0) {
            Start-Sleep -Seconds 2
            continue
        }

        $unready = $snap | Where-Object {
            # Treat "none" as acceptable if container is running (no healthcheck defined)
            -not (($_.Health -eq "healthy") -or ($_.Health -eq "none" -and $_.Status -eq "running"))
        }

        if (-not $unready -or $unready.Count -eq 0) {
            Write-Host "All containers report healthy."
            return $true
        } else {
            $summary = ($snap | ForEach-Object { "$($_.Name)=$($_.Health)" }) -join ", "
            Write-Host ("In progress: {0}" -f $summary)
            Start-Sleep -Seconds 3
        }
    } while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec)

    Write-Warning "Timed out waiting for healthy containers."
    return $false
}

function Show-StatusTable {
    Write-Host ""
    Write-Host "Final container health:"
    docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
}

# --- Main flow ---
try {
    Start-DockerIfNeeded
    Build-And-Start -Svcs $Services -NoBuild:$NoBuild
    $ok = Wait-For-Health -TimeoutSec $TimeoutSec
    Show-StatusTable
    if ($ok) {
        Write-Host ""
        Write-Host "Deploy complete - all systems healthy."
        exit 0
    } else {
        Write-Error "Deploy finished with unhealthy containers."
        exit 1
    }
} catch {
    Write-Error "Deploy failed: $($_.Exception.Message)"
    exit 1
}
