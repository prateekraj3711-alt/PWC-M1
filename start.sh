#!/usr/bin/env bash
set -euo pipefail

export PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-"/tmp/pw-browsers"}

python -m pip install --upgrade pip
python -m pip install -r python/requirements.txt

node -v
npm -v
npm install
npx playwright install chromium || true

# Run Python and Node concurrently
uvicorn python.main:app --host 0.0.0.0 --port 8000 &
PY_PID=$!

node node/scheduler.js &
NODE_PID=$!

trap "kill $PY_PID $NODE_PID || true" EXIT
wait


