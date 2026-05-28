#!/usr/bin/env bash
# Report how far this fork has drifted from the latest dograh-vX.Y.Z release tag
# on the upstream remote. Run weekly (or from CI) so merges never get scary.
#
# Exits 1 if drift exceeds DRIFT_WARN_THRESHOLD commits behind (default 30).

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

DRIFT_WARN_THRESHOLD="${DRIFT_WARN_THRESHOLD:-30}"
UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-upstream}"

if ! git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1; then
    echo -e "${RED}Error: remote '$UPSTREAM_REMOTE' not configured. Run scripts/setup_fork.sh first.${NC}"
    exit 1
fi

echo -e "${BLUE}Fetching upstream tags...${NC}"
git fetch "$UPSTREAM_REMOTE" --tags --quiet

LATEST_TAG=$(git tag --sort=-creatordate | grep '^dograh-v' | head -1)
if [[ -z "$LATEST_TAG" ]]; then
    echo -e "${RED}No dograh-vX.Y.Z tags found. Did the upstream fetch succeed?${NC}"
    exit 1
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
MERGE_BASE=$(git merge-base HEAD "$LATEST_TAG")
BEHIND=$(git rev-list --count HEAD.."$LATEST_TAG")
AHEAD=$(git rev-list --count "$LATEST_TAG"..HEAD)
ANCESTOR_SUBJECT=$(git log -1 --format="%h %s" "$MERGE_BASE")

echo ""
echo -e "${BLUE}Branch:${NC}        $CURRENT_BRANCH"
echo -e "${BLUE}Latest upstream:${NC} $LATEST_TAG"
echo -e "${BLUE}Common ancestor:${NC} $ANCESTOR_SUBJECT"
echo -e "${BLUE}Ahead:${NC}         $AHEAD commits (your fork's work)"
echo -e "${BLUE}Behind:${NC}        $BEHIND commits"
echo ""

if [[ "$BEHIND" -eq 0 ]]; then
    echo -e "${GREEN}✓ Up to date with $LATEST_TAG${NC}"
    exit 0
fi

if [[ "$BEHIND" -gt "$DRIFT_WARN_THRESHOLD" ]]; then
    echo -e "${RED}⚠ Drift exceeds $DRIFT_WARN_THRESHOLD commits — merge soon to keep conflicts manageable.${NC}"
    echo -e "  Suggested: ${BLUE}git checkout -b merge/${LATEST_TAG} && git merge ${LATEST_TAG}${NC}"
    exit 1
fi

echo -e "${YELLOW}Drift within tolerance ($BEHIND / $DRIFT_WARN_THRESHOLD). No action required yet.${NC}"
echo -e "  When ready: ${BLUE}git checkout -b merge/${LATEST_TAG} && git merge ${LATEST_TAG}${NC}"
