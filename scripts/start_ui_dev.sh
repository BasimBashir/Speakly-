#!/usr/bin/env bash
# Launch the Next.js dev server inside the devcontainer.
# Symmetric with scripts/start_services_dev.sh so start.bat can drive both
# through a uniform `bash <script>` invocation without nested-quote escaping.
set -e

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
cd "$BASE_DIR/ui"

echo "Starting Dograh UI (DEV MODE) at $(date) in $(pwd)"
echo "Listening on http://0.0.0.0:3000"
echo

exec npm run dev -- --hostname 0.0.0.0
