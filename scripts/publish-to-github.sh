#!/usr/bin/env bash
set -euo pipefail

REPO_NAME="${1:-smhi-weather-mail}"
VISIBILITY="${2:-private}"

if [[ "$VISIBILITY" != "private" && "$VISIBILITY" != "public" ]]; then
  echo "Usage: $0 [repo-name] [private|public]" >&2
  exit 2
fi

for cmd in git gh; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Fel: '$cmd' saknas." >&2
    exit 1
  fi
done

gh auth status >/dev/null

if [[ ! -d .git ]]; then
  git init
  git branch -M main
fi

git add .
if ! git diff --cached --quiet; then
  git commit -m "Initial SMHI weather mail service"
fi

if git remote get-url origin >/dev/null 2>&1; then
  echo "Git-remote 'origin' finns redan: $(git remote get-url origin)"
  echo "Pushar main..."
  git push -u origin main
else
  echo "Skapar GitHub-repot '$REPO_NAME' som $VISIBILITY..."
  gh repo create "$REPO_NAME" "--$VISIBILITY" --source=. --remote=origin --push
fi

echo
echo "Klart:"
gh repo view --web
