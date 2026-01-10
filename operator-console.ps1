# operator-console.ps1 – Live log + command input (PowerShell 5.1)

param(
    [string]$Inbox = '',
    [string]$Outbox = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- Resolve folders (default to .\agent\) ---
$Root = Get-Location
if (-not $Inbox -or -not $Outbox) {
    $AgentRoot = Join-Path $Root 'agent'
    if (-not $Inbox)  { $Inbox  = Join-Path $AgentRoot 'inbox'  }
    if (-not $Outbox) { $Outbox = Join-Path $AgentRoot 'outbox' }
}
$Queue = Join-Path $Inbox 'queue'
foreach ($d in @($Inbox,$Outbox,$Queue)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# --- UI ---
$form = New-Object System.Windows.Forms.Form
$form.Text = "Operator Console"
$form.Width = 900
$form.Height = 600

$log = New-Object System.Windows.Forms.RichTextBox
$log.ReadOnly = $true
$log.Font = New-Object System.Drawing.Font("Consolas",10)
$log.BackColor = [System.Drawing.Color]::Black
$log.ForeColor = [System.Drawing.Color]::Gainsboro
$log.Dock = 'Top'
$log.Height = 500
$form.Controls.Add($log)

$input = New-Object System.Windows.Forms.TextBox
$input.Dock = 'Bottom'
$input.Font = New-Object System.Drawing.Font("Consolas",10)
$input.Height = 24
$form.Controls.Add($input)

$send = New-Object System.Windows.Forms.Button
$send.Text = "Send"
$send.Dock = 'Bottom'
$send.Height = 30
$form.Controls.Add($send)

# --- State for tailing ---
$progressFile = Join-Path $Outbox 'progress.ndjson'
$seen = 0

function Append-LogLine {
    param([string]$text, [System.Drawing.Color]$color)

    $start = $log.TextLength
    $log.AppendText($text + [Environment]::NewLine)
    $log.Select($start, $text.Length)
    $log.SelectionColor = $color
    $log.Select($log.TextLength, 0)
    $log.ScrollToCaret()
}

function Color-ForStatusStage {
    param([string]$status, [string]$stage)
    $s = ($status | ForEach-Object { $_.ToString().ToLower() })
    switch ($s) {
        'ok'     { return [System.Drawing.Color]::LightGreen }
        'error'  { return [System.Drawing.Color]::LightCoral }
        'start'  { return [System.Drawing.Color]::DeepSkyBlue }
        'wait'   { return [System.Drawing.Color]::Khaki }
        default  { return [System.Drawing.Color]::Gainsboro }
    }
}

function Tail-Progress {
    if (-not (Test-Path $progressFile)) { return }
    try {
        $lines = Get-Content $progressFile -ErrorAction Stop
    } catch { return }
    if ($lines.Count -le $seen) { return }
    for ($i = $seen; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        try {
            $obj = $line | ConvertFrom-Json
            $msg = "[{0}] {1}:{2} {3} :: {4}" -f $obj.timestamp, $obj.lane, $obj.stage, $obj.status, $obj.message
            $color = Color-ForStatusStage -status $obj.status -stage $obj.stage
            Append-LogLine $msg $color
        } catch {
            Append-LogLine $line ([System.Drawing.Color]::Gainsboro)
        }
    }
    $seen = $lines.Count
}

function Enqueue-Command {
    param([hashtable]$cmd)
    $file = Join-Path $Queue ("{0:yyyyMMdd_HHmmss_fff}.json" -f (Get-Date))
    ($cmd | ConvertTo-Json -Compress) | Set-Content -Path $file -Encoding UTF8
}

function Handle-Input {
    $text = $input.Text.Trim()
    if (-not $text) { return }
    try {
        # Commands:
        # approve | reject
        # move X Y
        # click [left|right] [count]
        # type <text...>
        $parts = [System.Text.RegularExpressions.Regex]::Split($text, '\s+')
        $cmd = $parts[0].ToLower()

        switch ($cmd) {
            'approve' {
                New-Item -ItemType File -Path (Join-Path $Inbox 'approve.ok') -Force | Out-Null
                Append-LogLine ">> approve posted" ([System.Drawing.Color]::LightGreen)
            }
            'reject' {
                New-Item -ItemType File -Path (Join-Path $Inbox 'reject.no') -Force | Out-Null
                Append-LogLine ">> reject posted" ([System.Drawing.Color]::LightCoral)
            }
            'move' {
                if ($parts.Count -lt 3) { throw 'usage: move <x> <y>' }
                Enqueue-Command @{ action='MOVE'; x=[int]$parts[1]; y=[int]$parts[2] }
                Append-LogLine (">> enqueued MOVE {0},{1}" -f $parts[1],$parts[2]) ([System.Drawing.Color]::LightSkyBlue)
            }
            'click' {
                $btn = if ($parts.Count -ge 2) { $parts[1].ToLower() } else { 'left' }
                if ($btn -notin @('left','right')) { $btn = 'left' }
                $cnt = if ($parts.Count -ge 3) { [int]$parts[2] } else { 1 }
                Enqueue-Command @{ action='CLICK'; button=$btn; count=$cnt }
                Append-LogLine (">> enqueued CLICK {0} x{1}" -f $btn,$cnt) ([System.Drawing.Color]::LightSkyBlue)
            }
            'type' {
                $payload = ($text -replace '^\s*type\s*', '')
                if (-not $payload) { throw 'usage: type <text>' }
                Enqueue-Command @{ action='TYPE'; text=$payload }
                Append-LogLine (">> enqueued TYPE '{0}'" -f $payload) ([System.Drawing.Color]::LightSkyBlue)
            }
            default {
                throw ("unknown command: {0}" -f $cmd)
            }
        }
    }
    catch {
        Append-LogLine ("!! input error: {0}" -f $_.Exception.Message) ([System.Drawing.Color]::LightCoral)
    }
    finally {
        $input.Text = ''
        $input.Focus() | Out-Null
    }
}

# Events
$send.Add_Click({ Handle-Input })
$input.Add_KeyDown({
    param($sender,$e)
    if ($e.KeyCode -eq 'Enter') {
        $e.SuppressKeyPress = $true
        Handle-Input
    }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 300
$timer.Add_Tick({ Tail-Progress })
$timer.Start()

# Initial log line
Append-LogLine ("Operator console watching:`n  Inbox : {0}`n  Outbox: {1}" -f $Inbox, $Outbox) ([System.Drawing.Color]::Gainsboro)

[void]$form.ShowDialog()
