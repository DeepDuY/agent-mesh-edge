@echo off
setlocal enableextensions
rem agent-mesh edge keepalive launcher (Windows).
rem
rem Mirrors the POSIX wrapper (bin/agent-mesh-edge): loads etc\edge.env, applies
rem a pending upgrade, rolls back a broken upgrade, then runs
rem bin\agent-mesh-edge.bin.exe in a restart loop. It is what the
rem "agent-mesh-edge" Scheduled Task starts at boot (as SYSTEM).
rem
rem A Windows process cannot replace its own running .exe, so the agent only
rem *stages* the new binary as agent-mesh-edge.bin.exe.new and exits; this
rem launcher performs the actual rename before starting it (at that point the
rem old image is no longer running/locked).
cd /d "%~dp0.."

if not exist "logs" mkdir "logs"

for /f "usebackq tokens=1,* delims==" %%A in ("etc\edge.env") do (
    if not "%%~A"=="" set "%%~A=%%~B"
)
set "PATH=%CD%\bin;%PATH%"

set "BIN=%CD%\bin\agent-mesh-edge.bin.exe"
set "OLD=%CD%\bin\agent-mesh-edge.bin.exe.old"
set "NEW=%CD%\bin\agent-mesh-edge.bin.exe.new"
set "OC=%CD%\bin\opencode.exe"
set "OC_OLD=%CD%\bin\opencode.exe.old"
set "OC_NEW=%CD%\bin\opencode.exe.new"
set "MARKER=%CD%\etc\upgrading"
set "STARTED=%CD%\etc\upgrade-started"

:loop
rem --- Apply a staged upgrade (we run before the agent, so nothing is locked) ---
if exist "%NEW%" (
    if exist "%BIN%" move /y "%BIN%" "%OLD%" >nul 2>&1
    move /y "%NEW%" "%BIN%" >nul 2>&1
)
if exist "%OC_NEW%" (
    if exist "%OC%" move /y "%OC%" "%OC_OLD%" >nul 2>&1
    move /y "%OC_NEW%" "%OC%" >nul 2>&1
)

rem --- Post-upgrade rollback (same rule as the POSIX wrapper): if the new
rem --- binary started but never confirmed healthy (its first successful
rem --- heartbeat clears etc\upgrading), restore the previous binary.
if exist "%OLD%" if exist "%MARKER%" (
    if exist "%STARTED%" (
        del /q "%MARKER%" "%STARTED%" >nul 2>&1
        move /y "%OLD%" "%BIN%" >nul 2>&1
        if exist "%CD%\etc\agent_version.bak" move /y "%CD%\etc\agent_version.bak" "%CD%\etc\agent_version" >nul 2>&1
        echo %DATE% %TIME% agent-mesh-edge: rolled back to previous binary>> "%CD%\logs\edge.log"
    ) else (
        type nul > "%STARTED%"
    )
)

"%BIN%" >> "%CD%\logs\edge.log" 2>&1
echo %DATE% %TIME% agent-mesh-edge exited (code=%ERRORLEVEL%); restarting in 5s>> "%CD%\logs\edge.log"
rem `timeout` fails in a non-interactive SYSTEM session ("Input redirection is
rem not supported"); ping is a portable delay that works there.
ping -n 6 127.0.0.1 >nul
goto loop
