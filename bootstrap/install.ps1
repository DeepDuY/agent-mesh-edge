#Requires -Version 5.1
<#
.SYNOPSIS
    Install the agent-mesh edge agent on Windows (Scheduled Task, no systemd).

.DESCRIPTION
    Companion to bootstrap/install.sh for Windows. Copies the bundled binaries,
    writes etc\edge.env, and registers a Scheduled Task ("agent-mesh-edge") that
    runs the keepalive launcher at boot as SYSTEM. No NSSM/WinSW dependency: the
    launcher itself restarts the agent if it exits.

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

# Run a native command without letting its stderr become a terminating error
# under $ErrorActionPreference='Stop' (e.g. `schtasks /End` on a task that does
# not exist yet on a first install). Returns the exit code.
function Invoke-Quiet {
    param([string]$Exe, [string[]]$Args)
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try { & $Exe @Args 2>$null | Out-Null } catch { }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $eap
    return $code
}

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

# ========== Pre-flight checks ==========
Write-Host '==> Running pre-flight checks...'
if (-not $isAdmin) {
    Write-Warning 'not running as Administrator; Scheduled Task registration will be skipped'
}

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

$existing = (Test-Path (Join-Path $InstallDir 'bin\agent-mesh-edge.exe')) -or
            (Test-Path (Join-Path $InstallDir 'bin\agent-mesh-edge.bin'))
if ($existing) {
    Write-Warning "agent-mesh-edge already exists at $InstallDir"
    if (-not $Force) {
        Write-Error 'Set -Force to overwrite (or choose another -InstallDir).'
        exit 1
    }
    Write-Host '-Force: stopping existing Scheduled Task / agent'
    Invoke-Quiet schtasks.exe @('/End', '/TN', $TaskName) | Out-Null
    Get-Process agent-mesh-edge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}
Write-Host '==> Pre-flight checks passed'

# ========== Install ==========
Write-Host "==> Installing agent-mesh agent to $InstallDir"
foreach ($d in @('bin', 'etc', 'logs', 'run')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir $d) | Out-Null
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

# Fresh install: drop stale sync/upgrade state so the new binary re-syncs LLM config.
foreach ($f in @('config_version', 'upgrading', 'upgrade-started', 'agent_version.bak')) {
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $InstallDir "etc\$f")
}
Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $InstallDir 'bin\agent-mesh-edge.bin.old')

Copy-Item -Force -Path (Join-Path $ScriptDir 'bin\*') -Destination (Join-Path $InstallDir 'bin')
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

if ($SkipService -or (-not $isAdmin)) {
    Write-Host '==> Skipped Scheduled Task registration'
} else {
    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
    try {
        if (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue) {
            $action = New-ScheduledTaskAction -Execute $cmdExe -Argument "/c `"$launcher`""
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
            $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
                -Principal $principal -Force | Out-Null
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
        Write-Host "==> Scheduled Task registered: $TaskName (boot, SYSTEM)"
        Invoke-Quiet schtasks.exe @('/Run', '/TN', $TaskName) | Out-Null
        Write-Host "==> Started. Check: schtasks /Query /TN $TaskName /V /FO LIST"
    } else {
        Write-Warning "failed to register the Scheduled Task; start manually: $launcher"
    }
}

Write-Host "==> Agent installed to $InstallDir"
Write-Host "    version : $(Get-Content (Join-Path $InstallDir 'etc\agent_version') -ErrorAction SilentlyContinue)"
Write-Host "    log     : $(Join-Path $InstallDir 'logs\edge.log')"
if (-not $registered) {
    Write-Host "    start   : $launcher"
}
