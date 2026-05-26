# Local Development Setup (Windows)

Personal runbook for setting up and running the full stack on a fresh Windows machine.

---

## Prerequisites

Install these before anything else:

| Tool | Where |
|---|---|
| Git | https://git-scm.com |
| Docker Desktop | https://www.docker.com/products/docker-desktop |
| Node.js (for devcontainer CLI) | https://nodejs.org |

Make sure Docker Desktop is **running** before proceeding.

---

## One-time Setup

### 1. Clone the repo

```powershell
git clone https://github.com/<YOUR_GITHUB_HANDLE>/<YOUR_REPO_NAME>
cd <YOUR_REPO_NAME>
```

### 2. Install the devcontainer CLI

```powershell
npm install -g @devcontainers/cli
```

### 3. Initialize git submodules

```powershell
git submodule update --init --recursive
```

### 4. Start the devcontainer

This builds the Docker images, starts all services (Postgres, Redis, MinIO), and runs the full bootstrap (Python venv, npm install, DB migrations, env file creation). Takes 5–10 minutes the first time.

```powershell
devcontainer up --workspace-folder .
```

Wait for the line:
```
Devcontainer bootstrap complete in XXs.
```

If it fails, see the Troubleshooting section at the bottom.

---

## Daily Workflow

Every time you want to work on the project, run these steps in order.

### Step 1 — Start the backend (Terminal 1)

Run this once and let it exit. Services run in the background inside the container.

```powershell
devcontainer exec --workspace-folder . bash scripts/start_services_dev.sh
```

Wait for:
```
✓ uvicorn healthy (attempt N)
```

### Step 2 — Verify backend is reachable

```powershell
curl http://localhost:8000/api/v1/health
```

Expected response: `{"status":"ok"}` (or similar JSON). If you get an error, see Troubleshooting.

### Step 3 — Start the UI (Terminal 2, keep it running)

Open a **new** terminal window and leave this running:

```powershell
devcontainer exec --workspace-folder . bash -c "cd /workspaces/dograh/ui && npm run dev -- --hostname 0.0.0.0"
```

Wait for:
```
✓ Ready in Xs
```

### Step 4 — Open the app

Go to http://localhost:3000

---

## Stopping Everything

### Stop backend services

```powershell
devcontainer exec --workspace-folder . bash scripts/stop_services.sh
```

### Stop the UI

Press `Ctrl+C` in the terminal running `npm run dev`.

### Stop all containers

```powershell
docker compose -f docker-compose-local.yaml -f .devcontainer/docker-compose.yml down
```

---

## Starting Fresh (full reset)

Use this when you want to wipe containers and start from scratch. Named volumes (venv, node_modules, DB data) are preserved for speed.

```powershell
# 1. Stop services
devcontainer exec --workspace-folder . bash scripts/stop_services.sh

# 2. Stop and remove containers
docker compose -f docker-compose-local.yaml -f .devcontainer/docker-compose.yml down

# 3. Rebuild devcontainer from scratch
devcontainer up --workspace-folder .
```

To also wipe the database and all volumes (truly fresh):

```powershell
docker compose -f docker-compose-local.yaml -f .devcontainer/docker-compose.yml down -v
devcontainer up --workspace-folder .
```

---

## Service URLs

| Service | URL |
|---|---|
| App (UI) | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| MinIO Console | http://localhost:9001 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

---

## Project Structure

```
.
├── api/              # FastAPI backend (Python)
├── ui/               # Next.js frontend (TypeScript)
├── scripts/          # Start/stop/migrate helper scripts
├── pipecat/          # Pipecat submodule (voice pipeline)
├── docs/             # Mintlify docs
├── docker-compose-local.yaml   # Dev services (Postgres, Redis, MinIO)
└── .devcontainer/    # Devcontainer config and Dockerfile
```

Config files (auto-created from `.example` on first `devcontainer up`):
- `api/.env` — backend settings (DB, Redis, MinIO, LLM keys)
- `ui/.env` — frontend settings

---

## Adding API Keys

Edit `api/.env` inside the container (or directly from Windows since it's bind-mounted):

```
api/.env
```

Key variables:

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | GPT-4o, Whisper |
| `ANTHROPIC_API_KEY` | Claude models |
| `DEEPGRAM_API_KEY` | Speech-to-text |
| `ELEVENLABS_API_KEY` | Text-to-speech |
| `JWT_SECRET` | Auth token signing (auto-generated) |

After editing, restart the backend:
```powershell
devcontainer exec --workspace-folder . bash scripts/stop_services.sh
devcontainer exec --workspace-folder . bash scripts/start_services_dev.sh
```

---

## Updating the Auto-generated API Client

When you add a new backend route and want to use it in the UI:

```powershell
devcontainer exec --workspace-folder . bash -c "cd /workspaces/dograh/ui && npm run generate-client"
```

---

## Troubleshooting

### `devcontainer up` fails with exit code 2

Most likely a line-ending issue (Windows CRLF vs Linux LF). The `.gitattributes` in this repo enforces LF on all shell scripts and env files. If you see `\r: command not found` or `set: pipefail: invalid option`:

```powershell
# Re-normalize all files to LF
git rm --cached -r .
git reset --hard
```

### `curl localhost:8000` returns `Empty reply` (exit code 52)

The backend accepted the TCP connection but isn't responding. Uvicorn may not be running. Check:

```powershell
devcontainer exec --workspace-folder . bash -c "ss -tlnp | grep 8000"
```

If nothing shows, restart the backend (Step 1 of Daily Workflow).

### `curl localhost:8000` returns `Connection refused` (exit code 7)

Docker port binding is not active. Confirm the containers are running:

```powershell
docker ps --format "table {{.Names}}\t{{.Ports}}"
```

You should see `0.0.0.0:8000->8000/tcp` in the workspace container's ports. If not, the container may need to be recreated:

```powershell
docker compose -f docker-compose-local.yaml -f .devcontainer/docker-compose.yml down
devcontainer up --workspace-folder .
```

### UI starts on port 3001 instead of 3000

A previous UI process is still running inside the container using port 3000. Stop all services and restart:

```powershell
devcontainer exec --workspace-folder . bash scripts/stop_services.sh
docker compose -f docker-compose-local.yaml -f .devcontainer/docker-compose.yml down
devcontainer up --workspace-folder .
```

### `fatal: detected dubious ownership` during bootstrap

Git refuses to run inside the container due to UID mismatch between Windows host and the container user. This is already handled in the bootstrap script. If it appears again:

```powershell
devcontainer exec --workspace-folder . bash -c "git config --global --add safe.directory /workspaces/dograh && git config --global --add safe.directory /workspaces/dograh/pipecat"
```

### `devcontainer exec bash -lc '...'` fails with unexpected EOF

Single quotes don't work inside `devcontainer exec` on Windows CMD/PowerShell. Always use double quotes with `bash -c`:

```powershell
# Wrong (breaks on Windows)
devcontainer exec --workspace-folder . bash -lc 'cd ui && npm run dev'

# Correct
devcontainer exec --workspace-folder . bash -c "cd /workspaces/dograh/ui && npm run dev -- --hostname 0.0.0.0"
```

---

## Staying Updated with the Upstream Repo

This repo is a fork of [dograh-hq/dograh](https://github.com/dograh-hq/dograh). The official contribution setup docs live at:

**https://docs.dograh.com/contribution/setup**

Check that page for:
- New prerequisites or tooling changes
- Updated bootstrap steps
- New environment variables added to `.env.example`
- Changes to how services are started

### Pulling upstream changes into your fork

```powershell
# Add upstream remote once
git remote add upstream https://github.com/dograh-hq/dograh.git

# Fetch and merge upstream changes
git fetch upstream
git merge upstream/main
```

After merging, re-check `api/.env.example` and `ui/.env.example` for new variables that may have been added, and copy them into your local `.env` files manually.

### Windows-specific changes in this fork

The following files were modified from upstream to fix Windows compatibility. When merging upstream changes, review these files carefully to preserve the Windows fixes:

| File | What was changed |
|---|---|
| `.gitattributes` | Added — enforces LF line endings on `.sh` and `.env` files |
| `.devcontainer/scripts/post-create.sh` | Added `sed -i 's/\r//'` to strip CRLF from copied env files; added `git config --global --add safe.directory` for pipecat submodule |
| `.devcontainer/docker-compose.yml` | Added port bindings `127.0.0.1:8000:8000` and `127.0.0.1:3000:3000` for host access |
| `.devcontainer/devcontainer.json` | Added `8000` and `3000` to `forwardPorts` |
| `scripts/start_services_dev.sh` | Added `nohup` and `disown` to keep services alive after `devcontainer exec` exits |
