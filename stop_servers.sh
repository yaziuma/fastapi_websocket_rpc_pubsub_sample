#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT_DIR/.run/servers.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "PID ファイルが見つかりません: $PID_FILE"
  echo "サーバーは起動していない可能性があります。"
  exit 1
fi

mapfile -t PIDS < "$PID_FILE"

for pgid in "${PIDS[@]}"; do
  if [[ -n "$pgid" ]] && kill -0 "-$pgid" 2>/dev/null; then
    kill -TERM "-$pgid"
  fi
done

for pgid in "${PIDS[@]}"; do
  if [[ -n "$pgid" ]] && kill -0 "-$pgid" 2>/dev/null; then
    for _ in {1..50}; do
      if ! kill -0 "-$pgid" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
  fi
done

for pgid in "${PIDS[@]}"; do
  if [[ -n "$pgid" ]] && kill -0 "-$pgid" 2>/dev/null; then
    kill -KILL "-$pgid" 2>/dev/null || true
  fi
done

rm -f "$PID_FILE"

echo "サーバーを停止しました。"
