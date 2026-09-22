#Requires -Version 5.1
<#
.SYNOPSIS
    Install the agent-mesh edge agent on Windows (Scheduled Task, no systemd).

.DESCRIPTION
    Companion to bootstrap/install.sh for Windows. Copies the bundled binaries,
    writes etc\edge.env, and registers a Scheduled Task ("agent-mesh-edge") that
    runs the keepalive launcher at boot as SYSTEM. No NSSM/WinSW dependency: the
    launcher itself restarts the agent if it exits and applies self-upgrades.

    The bundled agent binary is installed as bin\agent-mesh-edge.bin.exe and run
    by the launcher (bin\agent-mesh-edge.cmd). The indirection exists because a
    Windows process cannot replace its own running .exe: the agent only stages
    the new image and the launcher swaps it in on the next start.

.PARAMETER OrchestratorUrl
    Base URL of the orchestrator, e.g. http://10.0.0.1:8000.

.PARAMETER Token
    Long-lived user / global API token used to register this node.

.EXAMPLE
    .\install.ps1 -OrchestratorUrl http://10.0.0.1:8000 -Token <token> -AgentId node-1

.EXAMPLE
    $env:ORCHESTRATOR_URL='http://10.0.0.1:8000'; $env:TOKEN='<token>'
    .\install.ps1 -Force
#>
[CmdletBinding()]
param(
    [string]$OrchestratorUrl = $env:ORCHESTRATOR_URL,
    [string]$Token = $env:TOKEN,
    [string]$AgentId = $env:EDGE_ALIAS,
    [string]$InstallDir = $(if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { 'C:\ProgramData\agent-mesh-agent' }),
    [string]$WorkDir,
    [switch]$Force,
    [switch]$SkipService
)

$ErrorActionPreference = 'Stop'
$TaskName = 'agent-mesh-edge'

if (-not $OrchestratorUrl) {
    Write-Error 'ERROR: ORCHESTRATOR_URL is required (e.g. http://10.0.0.1:8000)'
    exit 1
}
if (-not $Token) {
    Write-Error 'ERROR: TOKEN is required (your user API token)'
    exit 1
}
if (-not $AgentId) { $AgentId = $env:COMPUTERNAME }
if (-not $WorkDir) { $WorkDir = Join-Path $InstallDir 'work' }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallLog = Join-Path $InstallDir 'logs\install.log'

# Run a native command without letting its stderr become a terminating error
# under $ErrorActionPreference='Stop' (e.g. `schtasks /End` on a task that does
# not exist yet on a first install). Returns the exit code.
function Invoke-Quiet {
    param([string]$Exe, [string[]]$Arguments)
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try { & $Exe @Arguments 2>$null | Out-Null } catch { }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $eap
    return $code
}

function Write-Log {
    param([string]$Message)
    Write-Host $Message
    try {
        Add-Content -Path $InstallLog -Value ("[" + (Get-Date -Format s) + "] " + $Message) -ErrorAction SilentlyContinue
    } catch { }
}

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

# ========== Pre-flight checks ==========
Write-Host '==> Running pre-flight checks...'

# The agent is a machine-wide SYSTEM service: registering its boot Scheduled
# Task needs Administrator. Fail loudly instead of silently skipping autostart
# (which used to leave a running-but-not-booting node behind).
if ((-not $isAdmin) -and (-not $SkipService)) {
    Write-Error @'
ERROR: Administrator privileges are required to register the boot Scheduled Task.
       Re-run this installer from an elevated PowerShell ("Run as Administrator").
       Pass -SkipService only if you intentionally want to lay down files without
       autostart (start bin\agent-mesh-edge.cmd manually).
'@
    exit 1
}
if (-not $isAdmin) {
    Write-Warning 'not running as Administrator; -SkipService given, Scheduled Task will be skipped'
}

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'logs') | Out-Null

$driveLetter = (Split-Path -Qualifier $InstallDir).TrimEnd(':')
try {
    $freeMB = [math]::Round((Get-PSDrive -Name $driveLetter).Free / 1MB)
    if ($freeMB -lt 500) {
        Write-Error "ERROR: insufficient disk space at $InstallDir (need ~500MB, have ${freeMB}MB)."
        exit 1
    }
} catch {
    Write-Warning "could not determine free disk space for $InstallDir"
}

$binFile = Join-Path $InstallDir 'bin\agent-mesh-edge.bin.exe'
$legacyBin = Join-Path $InstallDir 'bin\agent-mesh-edge.exe'
$existing = (Test-Path $binFile) -or (Test-Path $legacyBin)
if ($existing) {
    Write-Warning "agent-mesh-edge already exists at $InstallDir"
    if (-not $Force) {
        Write-Error 'Set -Force to overwrite (or choose another -InstallDir).'
        exit 1
    }
    Write-Host '-Force: stopping existing Scheduled Task / agent'
    Invoke-Quiet schtasks.exe @('/End', '/TN', $TaskName) | Out-Null
    Get-Process agent-mesh-edge, agent-mesh-edge.bin -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}
Write-Host '==> Pre-flight checks passed'

# ========== Install ==========
Write-Log "==> Installing agent-mesh agent to $InstallDir"
foreach ($d in @('bin', 'etc', 'logs', 'run')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir $d) | Out-Null
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# Fresh install: drop stale sync/upgrade state so the new binary re-syncs LLM
# config and does not skip config_version comparison after reinstall.
foreach ($f in @('config_version', 'upgrading', 'upgrade-started', 'agent_version.bak')) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $InstallDir "etc\$f")
}
foreach ($f in @(
        'agent-mesh-edge.bin.exe.old', 'agent-mesh-edge.bin.exe.new',
        'agent-mesh-edge.exe', 'opencode.exe.old', 'opencode.exe.new'
    )) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $InstallDir "bin\$f")
}

Copy-Item -Force -Path (Join-Path $ScriptDir 'bin\*') -Destination (Join-Path $InstallDir 'bin')
# The launcher runs the .bin.exe indirection so a self-upgrade can swap the
# image while it is not running (mirrors bin/agent-mesh-edge + .bin on POSIX).
Move-Item -Force (Join-Path $InstallDir 'bin\agent-mesh-edge.exe') $binFile
Copy-Item -Force -Path (Join-Path $ScriptDir 'agent-mesh-edge.cmd') -Destination (Join-Path $InstallDir 'bin\agent-mesh-edge.cmd')

# Runtime config (UTF-8 without BOM so the .cmd `for /f` parser and pydantic read it cleanly).
$envLines = @(
    "EDGE_AGENT_ID=$AgentId",
    "EDGE_ORCHESTRATOR_URL=$OrchestratorUrl",
    "EDGE_TOKEN=$Token",
    "EDGE_HEARTBEAT_S=3",
    "EDGE_RUNTIME=opencode",
    "EDGE_WORKDIR=$WorkDir",
    "EDGE_INSTALL_DIR=$InstallDir",
    "EDGE_LLM_API_KEY=$($env:EDGE_LLM_API_KEY)",
    "EDGE_LLM_BASE_URL=$($env:EDGE_LLM_BASE_URL)",
    "EDGE_LLM_MODEL=$($env:EDGE_LLM_MODEL)",
    "EDGE_LLM_MODELS=$($env:EDGE_LLM_MODELS)",
    "EDGE_SYSTEM_PROMPT=$($env:EDGE_SYSTEM_PROMPT)",
    "LOG_LEVEL=INFO"
)
$envPath = Join-Path $InstallDir 'etc\edge.env'
[System.IO.File]::WriteAllText($envPath, ($envLines -join "`r`n") + "`r`n", (New-Object System.Text.UTF8Encoding($false)))

# edge.env holds the node credential: restrict it to SYSTEM + Administrators.
Invoke-Quiet icacls.exe @($envPath, '/inheritance:r', '/grant:r', '*S-1-5-18:(R)', '*S-1-5-32-544:(F)') | Out-Null

# Version marker reported to the orchestrator.
if (Test-Path (Join-Path $ScriptDir 'VERSION')) {
    Copy-Item -Force (Join-Path $ScriptDir 'VERSION') (Join-Path $InstallDir 'etc\agent_version')
}

if (Test-Path (Join-Path $InstallDir 'bin\opencode.exe')) {
    Write-Host "==> opencode bundled: $(Join-Path $InstallDir 'bin\opencode.exe')"
} else {
    Write-Warning "this package does not contain bin\opencode.exe; llm tasks will fail until it is present"
}

# ========== Scheduled Task (boot autostart) ==========
$launcher = Join-Path $InstallDir 'bin\agent-mesh-edge.cmd'
$registered = $false

if ($SkipService) {
    Write-Host '==> Skipped Scheduled Task registration'
} else {
    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
    try {
        if (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue) {
            $action = New-ScheduledTaskAction -Execute $cmdExe -Argument "/c `"$launcher`""
            $bootTrigger = New-ScheduledTaskTrigger -AtStartup
            # Safety net: in addition to boot, retry every 5 minutes. A missed
            # boot trigger (or a failed install-time start) therefore self-heals
            # without a reboot; IgnoreNew prevents a second instance while the
            # keepalive loop is already running.
            $repetitionTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
                -RepetitionInterval (New-TimeSpan -Minutes 5) `
                -RepetitionDuration (New-TimeSpan -Days 3650)
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -DontStopOnIdleEnd -StartWhenAvailable -MultipleInstances IgnoreNew `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
            $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($bootTrigger, $repetitionTrigger) `
                -Settings $settings -Principal $principal -Force | Out-Null
            $registered = $true
        }
    } catch {
        Write-Warning "Register-ScheduledTask failed ($($_.Exception.Message)); falling back to schtasks"
    }
    if (-not $registered) {
        $registered = (Invoke-Quiet schtasks.exe @(
            '/Create', '/TN', $TaskName, '/TR', "cmd.exe /c `"$launcher`"",
            '/SC', 'ONSTART', '/RU', 'SYSTEM', '/RL', 'HIGHEST', '/F'
        )) -eq 0
    }
    if ($registered) {
        Write-Log "==> Scheduled Task registered: $TaskName (boot + every 5min, SYSTEM)"
        # Start it now and verify: a task that registered but never ran (Last
        # Result 267011 / 0x41303 "has not yet run") is exactly the silent
        # autostart failure we want to catch.
        $started = $false
        try {
            Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
            $started = $true
        } catch {
            Write-Warning "Start-ScheduledTask failed ($($_.Exception.Message)); falling back to schtasks /Run"
        }
        if (-not $started) {
            $started = (Invoke-Quiet schtasks.exe @('/Run', '/TN', $TaskName)) -eq 0
        }
        Start-Sleep -Seconds 2
        $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
        $state = (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue).State
        if ($info) {
            Write-Log "    task state=$state lastRun=$($info.LastRunTime) result=$($info.LastTaskResult)"
        }
        if ($started) {
            Write-Log "==> Started. Check: schtasks /Query /TN $TaskName /V /FO LIST"
        } else {
            Write-Warning "could not start the Scheduled Task automatically; it will start at boot / within 5 minutes. Start now: $launcher"
        }
    } else {
        Write-Error "failed to register the Scheduled Task; start manually: $launcher"
    }
}

Write-Host "==> Agent installed to $InstallDir"
Write-Host "    version : $(Get-Content (Join-Path $InstallDir 'etc\agent_version') -ErrorAction SilentlyContinue)"
Write-Host "    log     : $(Join-Path $InstallDir 'logs\edge.log')"
Write-Host "    install : $InstallLog"
if (-not $registered) {
    Write-Host "    start   : $launcher"
}
