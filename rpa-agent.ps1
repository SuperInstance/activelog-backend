# rpa-agent.ps1 – RPA worker with queue + lanes (PowerShell 5.1)

param(
    [string]$Name = 'agent',
    [string]$Inbox = '',
    [string]$Outbox = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- Resolve folders ---
$Root = Get-Location
if (-not $Inbox -or -not $Outbox) {
    $AgentRoot = Join-Path $Root 'agent'
    if (-not $Inbox)  { $Inbox  = Join-Path $AgentRoot 'inbox'  }
    if (-not $Outbox) { $Outbox = Join-Path $AgentRoot 'outbox' }
}
$Queue = Join-Path $Inbox 'queue'

foreach ($d in @($Inbox, $Outbox, $Queue)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

# --- Load assemblies ---
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# --- Win32 interop ---
Add-Type -Namespace Win32 -Name Native -MemberDefinition @'
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern bool SetCursorPos(int X,int Y);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern void mouse_event(
        uint dwFlags,
        uint dx,
        uint dy,
        uint dwData,
        System.UIntPtr dwExtraInfo
    );

    public const uint MOUSEEVENTF_LEFTDOWN  = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP    = 0x0004;
    public const uint MOUSEEVENTF_RIGHTDOWN = 0x0008;
    public const uint MOUSEEVENTF_RIGHTUP   = 0x0010;
    public const uint MOUSEEVENTF_WHEEL     = 0x0800;
'@

# --- Logger ---
function Write-Stage {
    param([string]$Stage, [string]$Status, [string]$Message = '')

    $entry = [pscustomobject]@{
        timestamp = (Get-Date).ToString('o')
        lane      = $Name
        stage     = $Stage
        status    = $Status
        message   = $Message
    }
    $json = $entry | ConvertTo-Json -Compress
    Add-Content -Path (Join-Path $Outbox 'progress.ndjson') -Value $json
    Write-Host ("[{0}] {1}:{2} {3} :: {4}" -f $entry.timestamp, $Name, $Stage, $Status, $Message)
}

# --- Primitives ---
function Move-Cursor {
    param([int]$x, [int]$y)
    [Win32.Native]::SetCursorPos($x, $y) | Out-Null
}

function Click-Mouse {
    param(
        [ValidateSet('left','right')]
        [string]$Button = 'left',
        [int]$Count = 1
    )
    switch ($Button.ToLower()) {
        'left'  { $down = [Win32.Native]::MOUSEEVENTF_LEFTDOWN;  $up = [Win32.Native]::MOUSEEVENTF_LEFTUP }
        'right' { $down = [Win32.Native]::MOUSEEVENTF_RIGHTDOWN; $up = [Win32.Native]::MOUSEEVENTF_RIGHTUP }
    }
    for ($i = 0; $i -lt $Count; $i++) {
        [Win32.Native]::mouse_event($down,0,0,0,[UIntPtr]::Zero)
        Start-Sleep -Milliseconds 50
        [Win32.Native]::mouse_event($up,0,0,0,[UIntPtr]::Zero)
        Start-Sleep -Milliseconds 50
    }
}

function Send-Keys {
    param([string]$Text)
    if ($Text) {
        $shell = New-Object -ComObject WScript.Shell
        $shell.SendKeys($Text) | Out-Null
    }
}

function Pause-For-Approval {
    param([string]$Reason = 'manual pause')
    Write-Stage 'PAUSE' 'wait' $Reason
    $approveFile = Join-Path $Inbox 'approve.ok'
    $rejectFile  = Join-Path $Inbox 'reject.no'
    while ($true) {
        if (Test-Path $approveFile) {
            Remove-Item $approveFile -ErrorAction SilentlyContinue
            Write-Stage 'PAUSE' 'approved' $Reason
            break
        }
        if (Test-Path $rejectFile) {
            Remove-Item $rejectFile -ErrorAction SilentlyContinue
            throw 'User rejected the pause.'
        }
        Start-Sleep -Milliseconds 200
    }
}

# --- Dispatcher ---
function Process-Command {
    param([pscustomobject]$cmd)

    if (-not $cmd.action) { throw 'Missing action in command.' }
    $action = $cmd.action.ToString().ToUpperInvariant()

    switch ($action) {
        'MOVE' {
            Move-Cursor -x ([int]$cmd.x) -y ([int]$cmd.y)
            Write-Stage 'MOVE' 'ok' "($($cmd.x),$($cmd.y))"
        }
        'CLICK' {
            $btn = if ($cmd.button) { $cmd.button } else { 'left' }
            $cnt = if ($cmd.count)  { [int]$cmd.count }  else { 1 }
            Click-Mouse -Button $btn -Count $cnt
            Write-Stage 'CLICK' 'ok' "$btn x$cnt"
        }
        'TYPE' {
            Send-Keys -Text ([string]$cmd.text)
            Write-Stage 'TYPE' 'ok' ([string]$cmd.text)
        }
        'PAUSE' {
            Pause-For-Approval -Reason ([string]$cmd.reason)
        }
        Default {
            throw ("Unsupported action: {0}" -f $action)
        }
    }
}

# --- Helpers to get next command file ---
function Get-NextCommandFile {
    $single = Join-Path $Inbox 'command.json'
    if (Test-Path $single) { return $single }

    $items = Get-ChildItem -Path $Queue -Filter *.json -File | Sort-Object Name
    if ($items -and $items.Count -gt 0) { return $items[0].FullName }

    return $null
}

# --- Main loop ---
Write-Stage 'AGENT' 'start' 'Initialized. Watching inbox and queue.'
while ($true) {
    $cmdFile = Get-NextCommandFile
    if ($cmdFile) {
        try {
            $raw = Get-Content $cmdFile -Raw -ErrorAction Stop
            $cmd = $raw | ConvertFrom-Json
            Process-Command -cmd $cmd
        }
        catch {
            Write-Stage 'AGENT' 'error' $_.Exception.Message
        }
        finally {
            Remove-Item $cmdFile -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 150
}
