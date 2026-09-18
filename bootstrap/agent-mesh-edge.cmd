@echo off
setlocal enableextensions
rem agent-mesh edge keepalive launcher (Windows).
rem Loads etc\edge.env, then runs the edge binary in a restart loop. This is
rem what the "agent-mesh-edge" Scheduled Task starts at boot (as SYSTEM).
cd /d "%~dp0.."

if not exist "logs" mkdir "logs"

for /f "usebackq tokens=1,* delims==" %%A in ("etc\edge.env") do (
    if not "%%~A"=="" set "%%~A=%%~B"
)
set "PATH=%CD%\bin;%PATH%"

:loop
"%CD%\bin\agent-mesh-edge.exe" >> "%CD%\logs\edge.log" 2>&1
echo %DATE% %TIME% agent-mesh-edge exited (code=%ERRORLEVEL%); restarting in 5s>> "%CD%\logs\edge.log"
timeout /t 5 /nobreak >nul
goto loop
