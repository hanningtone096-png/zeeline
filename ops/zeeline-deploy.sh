#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY=/var/www/zeeline-insurance
readonly BRANCH=main

cd "$REPOSITORY"

if [[ ! -f backend/.env ]]; then
    echo "Refusing deployment: backend/.env is missing." >&2
    exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Refusing deployment: the production checkout has uncommitted changes." >&2
    exit 1
fi

previous_revision=$(git rev-parse HEAD)
git fetch --prune origin "refs/heads/${BRANCH}"
target_revision=$(git rev-parse "origin/${BRANCH}")

if [[ "$previous_revision" == "$target_revision" ]]; then
    echo "Production is already at ${target_revision}."
    exit 0
fi

git reset --hard "$target_revision"

if ! git diff --quiet "$previous_revision" "$target_revision" -- backend/requirements.txt; then
    .venv/bin/pip install --disable-pip-version-check --no-input -r backend/requirements.txt
fi

.venv/bin/python -c "compile(open('backend/app.py', encoding='utf-8').read(), 'backend/app.py', 'exec')"
systemctl restart zeeline-insurance
systemctl is-active --quiet zeeline-insurance

echo "Deployed ${target_revision}."
