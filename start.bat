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

REM ---- Step 1/5 — pre-pull every service image (resilient to registry blips) -
REM
REM  devcontainer up runs `docker compose up -d`, which pulls ALL images before
REM  starting ANY container. A single transient ghcr.io TLS/token timeout there
REM  aborts the whole launch (seen with the ~6.8 GB kokoro image at 99%). We
REM  pull everything here first, with retries, so `up` finds images locally and
REM  never has to hit the registry mid-launch. The compose file is the source of
REM  truth for the image list, so this never drifts as services change.

echo.
echo [STEP 1/6] Pulling all service images ^(~13 GB on first run^)...
echo            Retries automatically on transient registry timeouts.
echo.
set /a pull_attempts=0
:pull_images
set /a pull_attempts+=1
docker compose -f docker-compose-local.yaml pull
if errorlevel 1 (
    if !pull_attempts! geq 5 (
        echo [ERROR] Image pull failed after 5 attempts.
        echo         Check your network / registry access and rerun.
        pause
        exit /b 1
    )
    echo [WARN] Pull attempt !pull_attempts! failed ^(likely a transient registry timeout^).
    echo        Retrying in 10s... cached layers resume, so this is quick.
    timeout /t 10 /nobreak >nul
    goto pull_images
)
echo [OK] All service images present locally.
echo.

REM ---- Step 2/5 — bring up the devcontainer + all model services ------------

echo [STEP 2/6] Bringing up devcontainer + GPU model services...
echo            First run also builds the workspace image and can take 15-30 min.
echo            Subsequent runs start in under a minute.
echo.
call devcontainer up --workspace-folder .
if errorlevel 1 (
    echo [ERROR] devcontainer up failed. See output above.
    pause
    exit /b 1
)
REM indic-stt / xtts-urdu bake big (gated) HF weights into the image, so they
REM are NOT in devcontainer runServices: `devcontainer up` rebuilds every
REM runService, and once Docker GCs the build cache that re-download fails
REM without an HF token. `compose up` only builds when the image is missing.
docker compose -f docker-compose-local.yaml up -d indic-stt xtts-urdu
if errorlevel 1 (
    echo [ERROR] Failed to start indic-stt / xtts-urdu. See output above.
    pause
    exit /b 1
)
echo [OK] Devcontainer + services running.
echo.

REM ---- Step 3/5 — backend (in its own window) -------------------------------

echo [STEP 3/6] Starting backend in a new window...
start "Dograh Backend" cmd /k "devcontainer exec --workspace-folder . bash scripts/start_services_dev.sh"
echo [OK] Backend launched.
echo.

REM ---- Step 4/5 — UI (in its own window) ------------------------------------

echo [STEP 4/6] Starting UI in a new window...
start "Dograh UI" cmd /k "devcontainer exec --workspace-folder . bash scripts/start_ui_dev.sh"
echo [OK] UI launched.
echo.

REM ---- Step 5/5 — wait for backend health, then open browser ----------------

echo [STEP 5/6] Waiting for backend health check at http://localhost:8000/api/v1/health
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

REM ---- Step 6/6 — wait for the local AI models to load + report healthy -------
REM
REM  The GPU model services (LLM, embeddings, TTS, STT) download weights and
REM  cold-load on first start, which happens in the background AFTER the stack
REM  is technically "up". Voice/chat will stall until they're healthy, so we
REM  surface each model's status here and wait until all four are ready.
REM    - First run: downloads ~15 GB of weights, so this can take 10-20 min.
REM    - Later runs: weights are cached; only cold-load, usually 1-3 min.

echo.
echo [STEP 6/6] Waiting for local AI models to load and report healthy...
echo            First run downloads ~15 GB of weights ^(10-20 min^); later runs are quick.
echo            Status legend: starting/downloading = not ready, healthy = ready.
echo.
set /a model_attempts=0
:wait_models
set /a model_attempts+=1
call :get_health vllm
set "S_VLLM=!RESULT!"
call :get_health embeddings
set "S_EMB=!RESULT!"
call :get_health kokoro
set "S_KOK=!RESULT!"
call :get_health speaches
set "S_SPE=!RESULT!"
echo   [!TIME!]  LLM=!S_VLLM!  Embeddings=!S_EMB!  TTS=!S_KOK!  STT=!S_SPE!
if "!S_VLLM!!S_EMB!!S_KOK!!S_SPE!"=="healthyhealthyhealthyhealthy" goto models_ready
if !model_attempts! geq 120 goto models_timeout
timeout /t 10 /nobreak >nul
goto wait_models

:models_timeout
echo.
echo [WARN] Not all models became healthy within ~20 minutes.
echo        Inspect logs with:
echo            docker compose -f docker-compose-local.yaml logs -f vllm embeddings kokoro speaches
goto stack_running

:models_ready
echo.
echo [OK] All local AI models are healthy and ready.

:stack_running
echo.
echo ================================================================
echo  Dograh stack is running.
echo.
echo  Backend logs:    "Dograh Backend" window
echo  UI logs:         "Dograh UI" window
echo  Model status:    docker compose -f docker-compose-local.yaml ps
echo  Stop everything: docker compose -f docker-compose-local.yaml down
echo                   ^(closes containers but keeps named volumes^)
echo ================================================================
echo.
pause
exit /b 0

REM ===========================================================================
REM  :get_health <service>  ->  sets RESULT to the container health status
REM  (healthy / starting / unhealthy), or "missing" if the container isn't up.
REM ===========================================================================
:get_health
set "RESULT=missing"
set "CID="
for /f "delims=" %%i in ('docker compose -f docker-compose-local.yaml ps -q %1 2^>nul') do set "CID=%%i"
if defined CID for /f "delims=" %%h in ('docker inspect -f "{{.State.Health.Status}}" !CID! 2^>nul') do set "RESULT=%%h"
goto :eof
