#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

CONFIG_FILE="$ROOT_DIR/config.toml"
RUN_DIR="$ROOT_DIR/.run"
PID_FILE="$RUN_DIR/servers.pid"
LOG_DIR="$ROOT_DIR/logs"

read_config_value() {
  local section="$1"
  local key="$2"

  uv run python -c '
import sys
import tomllib
from pathlib import Path

config_path = Path(sys.argv[1])
section = sys.argv[2]
key = sys.argv[3]
with config_path.open("rb") as file:
    data = tomllib.load(file)
print(data[section][key])
' "$CONFIG_FILE" "$section" "$key"
}

build_reload_args() {
  local enabled
  enabled="$(read_config_value fastapi reload)"
  if [[ "$enabled" == "True" ]]; then
    printf '%s\n' "--reload"
  fi
}

SERVER_A_HOST="$(read_config_value server_a host)"
SERVER_A_PORT="$(read_config_value server_a port)"
SERVER_B_HOST="$(read_config_value server_b host)"
SERVER_B_PORT="$(read_config_value server_b port)"
mapfile -t RELOAD_ARGS < <(build_reload_args)

mkdir -p "$RUN_DIR"

PIDS=()

is_group_alive() {
  local pgid="$1"
  ps -o pgid= -p "-$pgid" >/dev/null 2>&1
}

cleanup() {
  rm -f "$PID_FILE"

  for pgid in "${PIDS[@]:-}"; do
    if kill -0 "-$pgid" 2>/dev/null; then
      kill -TERM "-$pgid" 2>/dev/null || true
    fi
  done

  sleep 1

  for pgid in "${PIDS[@]:-}"; do
    if kill -0 "-$pgid" 2>/dev/null; then
      kill -KILL "-$pgid" 2>/dev/null || true
    fi
  done

  for pgid in "${PIDS[@]:-}"; do
    wait "$pgid" 2>/dev/null || true
  done
}

trap cleanup EXIT INT TERM

if [[ -f "$PID_FILE" ]]; then
  mapfile -t EXISTING_PIDS < "$PID_FILE"
  for pgid in "${EXISTING_PIDS[@]:-}"; do
    if [[ -n "$pgid" ]] && kill -0 "-$pgid" 2>/dev/null; then
      echo "既存の PID ファイルがあります: $PID_FILE"
      echo "./stop_servers.sh を実行してから再度起動してください。"
      exit 1
    fi
  done
  rm -f "$PID_FILE"
fi

setsid env PYTHONPATH="$PYTHONPATH" uv run python -m uvicorn rpc_pubsub_sample.server_a:app --host "$SERVER_A_HOST" --port "$SERVER_A_PORT" "${RELOAD_ARGS[@]}" &
PIDS+=("$!")

setsid env PYTHONPATH="$PYTHONPATH" uv run python -m uvicorn rpc_pubsub_sample.server_b:app --host "$SERVER_B_HOST" --port "$SERVER_B_PORT" "${RELOAD_ARGS[@]}" &
PIDS+=("$!")

printf '%s\n' "${PIDS[@]}" > "$PID_FILE"

echo "server-a: http://$SERVER_A_HOST:$SERVER_A_PORT"
echo "server-b: http://$SERVER_B_HOST:$SERVER_B_PORT"
echo "server-a log: $LOG_DIR/server-a.log"
echo "server-b log: $LOG_DIR/server-b.log"
echo "fastapi reload: ${RELOAD_ARGS[*]:-disabled}"
echo "停止するには Ctrl+C を押してください。"

wait -n "${PIDS[@]}"
