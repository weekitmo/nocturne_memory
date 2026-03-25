#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${PROJECT_ROOT}/backend"
FRONTEND_DIR="${PROJECT_ROOT}/frontend"
UV_CACHE_DIR="${PROJECT_ROOT}/.uv-cache"

backend_pid=""
frontend_pid=""

cleanup() {
  local exit_code=$?

  if [[ -n "${frontend_pid}" ]] && kill -0 "${frontend_pid}" 2>/dev/null; then
    kill "${frontend_pid}" 2>/dev/null || true
    wait "${frontend_pid}" 2>/dev/null || true
  fi

  if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" 2>/dev/null; then
    kill "${backend_pid}" 2>/dev/null || true
    wait "${backend_pid}" 2>/dev/null || true
  fi

  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

if ! command -v uv >/dev/null 2>&1; then
  echo "Missing 'uv'. Install uv first, then re-run this script."
  exit 1
fi

if ! command -v pnpm >/dev/null 2>&1; then
  echo "Missing 'pnpm'. Install pnpm first, then re-run this script."
  exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  echo "Missing ${PROJECT_ROOT}/.env"
  echo "Copy .env.example to .env before starting the dashboard."
  exit 1
fi

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "Frontend dependencies are missing."
  echo "Run: cd ${FRONTEND_DIR} && pnpm i"
  exit 1
fi

echo "Starting backend on http://127.0.0.1:8000"
(
  cd "${BACKEND_DIR}"
  export UV_CACHE_DIR
  uv run uvicorn main:app --port 8000
) &
backend_pid=$!

echo "Starting frontend on http://localhost:3000"
(
  cd "${FRONTEND_DIR}"
  pnpm dev
) &
frontend_pid=$!

echo "Backend PID: ${backend_pid}"
echo "Frontend PID: ${frontend_pid}"
echo "Dashboard: http://localhost:3000"
echo "Press Ctrl+C to stop both services."

wait "${backend_pid}"
