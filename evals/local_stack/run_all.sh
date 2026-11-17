#!/usr/bin/env bash
# Host-side runner: one clean Speaches per STT model, then the other suites.
# Usage (from repo root, Git Bash or Linux): bash evals/local_stack/run_all.sh
set -u
export MSYS_NO_PATHCONV=1
WT=$(git rev-parse --show-toplevel)
CWD="/workspaces/dograh${WT#*/dograh}"  # repo path inside the workspace container
fresh_speaches() {
  docker restart dograh-speaches-1 >/dev/null
  until [ "$(docker inspect -f '{{.State.Health.Status}}' dograh-speaches-1)" = healthy ]; do sleep 3; done
}

for m in deepdml/faster-whisper-large-v3-turbo-ct2 Systran/faster-whisper-large-v3; do
  fresh_speaches
  echo "##### STT $m"
  docker exec -w "$CWD" -e BENCH_STT_MODELS="$m" dograh-workspace-1 /workspaces/dograh/venv/bin/python -u evals/local_stack/bench.py stt
done
fresh_speaches
for s in llm tts load; do
  echo "##### $s"
  docker exec -w "$CWD" dograh-workspace-1 /workspaces/dograh/venv/bin/python -u evals/local_stack/bench.py "$s"
done
