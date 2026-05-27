#!/usr/bin/env bash
# Start the optional GPU model stack (vllm + embeddings + kokoro + speaches).
#
# Devcontainer's runServices only starts core infra (postgres/redis/minio/
# workspace) so the heavy GPU services stay opt-in. Run this script from
# the host (not from inside the workspace container) to bring them up.
#
# Usage:
#   bash scripts/start_local_models.sh          # start all four
#   bash scripts/start_local_models.sh logs     # tail logs for all four
#   bash scripts/start_local_models.sh stop     # stop all four
#   bash scripts/start_local_models.sh status   # show health
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="docker-compose-local.yaml"
PROJECT="dograh"
SERVICES=(vllm embeddings kokoro speaches)

action="${1:-up}"

case "$action" in
  up|start)
    echo "Starting GPU services: ${SERVICES[*]}"
    echo "First start downloads ~25 GB; vLLM may take 5-10 minutes to load."
    docker compose -f "$COMPOSE_FILE" --project-name "$PROJECT" up -d "${SERVICES[@]}"
    echo ""
    echo "Watch progress:  bash scripts/start_local_models.sh logs"
    echo "Check health:    bash scripts/start_local_models.sh status"
    ;;
  logs)
    docker compose -f "$COMPOSE_FILE" --project-name "$PROJECT" logs -f "${SERVICES[@]}"
    ;;
  stop|down)
    docker compose -f "$COMPOSE_FILE" --project-name "$PROJECT" stop "${SERVICES[@]}"
    ;;
  status|ps)
    docker compose -f "$COMPOSE_FILE" --project-name "$PROJECT" ps "${SERVICES[@]}"
    ;;
  *)
    echo "Usage: $0 [up|logs|stop|status]"
    exit 1
    ;;
esac
