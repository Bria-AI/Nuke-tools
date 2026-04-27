@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "BRIA_EXTENSION_ROOT=%%~fI"
set "NUKE_BRIA=%BRIA_EXTENSION_ROOT%"
for %%I in ("%BRIA_EXTENSION_ROOT%\nuke") do set "BRIA_NUKE_STARTUP_DIR=%%~fI"
set "BRIA_NUKE_ROOT=%BRIA_NUKE_STARTUP_DIR%"

if not defined BRIA_API_KEY_NUKE (
	if defined BRIA_API_KEY_HOUDINI set "BRIA_API_KEY_NUKE=%BRIA_API_KEY_HOUDINI%"
)

REM Disable Nuke FrameServer by default in dev runs (can be overridden by setting this env var before launch)
if not defined NUKE_DISABLE_FRAMESERVER set "NUKE_DISABLE_FRAMESERVER=1"

set "NUKE_PATH=%BRIA_NUKE_STARTUP_DIR%;%NUKE_PATH%"

cd /d "%BRIA_EXTENSION_ROOT%"

REM Update this path for your local Nuke installation.
set "NUKE_EXE=D:\Program Files\Nuke16.0v6\Nuke16.0.exe"
"%NUKE_EXE%"
