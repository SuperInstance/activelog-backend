# Bootstrap ActiveLog dev stack
param(
  [string]$ApiBase = "http://localhost:8080",
  [int]$WaitSec = 8
)

Write-Host "Starting docker compose..." -ForegroundColor Cyan
docker compose up -d --build
Write-Host "Waiting $WaitSec seconds for services..."
Start-Sleep -Seconds $WaitSec

# 1) Create org
$org = Invoke-RestMethod -Method Post -Uri "$ApiBase/orgs" -Body (@{ name = "Demo Org" } | ConvertTo-Json) -ContentType "application/json"
$orgId = $org.id
Write-Host "Org: $orgId"

# 2) Seed pricebook v1
$pb = @{
  version = 1
  items = @{
    "cpu.sec"   = "0.001"
    "token.in"  = "0.00001"
    "token.out" = "0.00002"
    "storage.gb.mo" = "0.05"
  }
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri "$ApiBase/pricebooks/seed" -Body $pb -ContentType "application/json" | Out-Null
Write-Host "Pricebook v1 seeded"

# 3) Create wallet
$headers = @{ "X-Org-Id" = $orgId }
$wallet = Invoke-RestMethod -Method Post -Headers $headers -Uri "$ApiBase/orgs/$orgId/wallets" -Body (@{
  owner_type = "org"
  owner_id   = $orgId
} | ConvertTo-Json) -ContentType "application/json"
$walletId = $wallet.id
Write-Host "Wallet: $walletId"

# 4) Credit wallet 100 CC
Invoke-RestMethod -Method Post -Headers $headers -Uri "$ApiBase/wallets/$walletId/credit" -Body (@{
  amount_cc = "100.00"
  memo = "bootstrap"
} | ConvertTo-Json) -ContentType "application/json" | Out-Null
Write-Host "Wallet credited with 100 CC"

# 5) Create job with 10 CC budget
$job = Invoke-RestMethod -Method Post -Headers $headers -Uri "$ApiBase/orgs/$orgId/jobs" -Body (@{
  owner_wallet = $walletId
  pricebook_version = 1
  budget_cc = "10.0"
} | ConvertTo-Json) -ContentType "application/json"
$jobId = $job.job_id
Write-Host "Job: $jobId (escrowed 10 CC)"

# 6) Send sample usage events
$events = @(
  @{ job_id = $jobId; sku="cpu.sec"; quantity=100 },
  @{ job_id = $jobId; sku="token.in"; quantity=50000 },
  @{ job_id = $jobId; sku="token.out"; quantity=25000 }
) | ConvertTo-Json
Invoke-RestMethod -Method Post -Headers $headers -Uri "$ApiBase/usage_events" -Body $events -ContentType "application/json" | Out-Null
Write-Host "Usage events sent"

# 7) Complete job (triggers escrow release after worker prices all usage)
Invoke-RestMethod -Method Post -Headers $headers -Uri "$ApiBase/jobs/$jobId/complete" | Out-Null
Write-Host "Job moved to settling; worker will finalize"

Start-Sleep -Seconds 5
$bal = Invoke-RestMethod -Method Get -Headers $headers -Uri "$ApiBase/wallets/$walletId/balance"
Write-Host "Wallet balance:" $bal.balance_cc