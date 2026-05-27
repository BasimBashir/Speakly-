@echo off
setlocal enabledelayedexpansion

REM ============================================================================
REM  Dograh — one-click start for Windows
REM  Sets up the devcontainer + GPU model stack, launches backend + UI in
REM  separate console windows, waits for the API to be healthy, and opens
REM  the app in your default browser.
REM ============================================================================

REM Always run from the script's directory so relative paths resolve.
cd /d "%~dp0"

echo ================================================================
echo  Dograh stack startup
echo  Directory: %CD%
echo ================================================================
echo.

REM ---- Prerequisite checks --------------------------------------------------

where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm not found on PATH. Install Node.js from https://nodejs.org first.
    pause
    exit /b 1
)

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] docker not found on PATH. Install Docker Desktop first.
    pause
    exit /b 1
)

REM Make sure Docker Desktop is actually running, not just installed.
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker daemon is not running. Start Docker Desktop and rerun.
    pause
    exit /b 1
)

REM ---- Install @devcontainers/cli once --------------------------------------

where devcontainer >nul 2>&1
if errorlevel 1 (
    echo [SETUP] Installing @devcontainers/cli globally ^(one-time^)...
    call npm install -g @devcontainers/cli
    if errorlevel 1 (
        echo [ERROR] Failed to install @devcontainers/cli.
        echo         Try running this script as Administrator, or run:
        echo             npm install -g @devcontainers/cli
        pause
        exit /b 1
    )
)

REM ---- Step 1/4 — bring up the devcontainer + all model services ------------

echo.
echo [STEP 1/4] Bringing up devcontainer + GPU model services...
echo            First run downloads ~13 GB of weights and takes 15-30 min.
echo            Subsequent runs start in under a minute.
echo.
call devcontainer up --workspace-folder .
if errorlevel 1 (
    echo [ERROR] devcontainer up failed. See output above.
    pause
    exit /b 1
)
echo [OK] Devcontainer + services running.
echo.

REM ---- Step 2/4 — backend (in its own window) -------------------------------

echo [STEP 2/4] Starting backend in a new window...
start "Dograh Backend" cmd /k "devcontainer exec --workspace-folder . bash scripts/start_services_dev.sh"
echo [OK] Backend launched.
echo.

REM ---- Step 3/4 — UI (in its own window) ------------------------------------

echo [STEP 3/4] Starting UI in a new window...
start "Dograh UI" cmd /k "devcontainer exec --workspace-folder . bash -lc \"cd ui && npm run dev -- --hostname 0.0.0.0\""
echo [OK] UI launched.
echo.

REM ---- Step 4/4 — wait for backend health, then open browser ----------------

echo [STEP 4/4] Waiting for backend health check at http://localhost:8000/api/v1/health
echo            ^(timeout: 5 minutes^)

set /a attempts=0
:wait_backend
set /a attempts+=1
if !attempts! gtr 60 (
    echo [WARN] Backend did not become healthy in 5 minutes. Opening browser anyway.
    goto open_browser
)
curl -s -f http://localhost:8000/api/v1/health >nul 2>&1
if !errorlevel! neq 0 (
    timeout /t 5 /nobreak >nul
    goto wait_backend
)
echo [OK] Backend healthy after !attempts! checks.

:open_browser
echo.
echo [INFO] Opening http://127.0.0.1:3000 in your default browser...
start "" http://127.0.0.1:3000

echo.
echo ================================================================
echo  Dograh stack is running.
echo.
echo  Backend logs:    "Dograh Backend" window
echo  UI logs:         "Dograh UI" window
echo  GPU services:    docker compose -f docker-compose-local.yaml ps
echo  Stop everything: docker compose -f docker-compose-local.yaml down
echo                   ^(closes containers but keeps named volumes^)
echo ================================================================
echo.
pause
