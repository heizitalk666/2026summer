#!/usr/bin/env bash
# 前台启动边缘端四进程（不起云端）。调试或没有 systemd 时用。
#
#   ./deploy/run_edge.sh                         # 用 configs/rk3576.yaml
#   PATROL_CONFIG=configs/system.yaml ./deploy/run_edge.sh --seconds 300   # 桩模式试跑
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONPATH="$PWD"
exec "$PY" -m patrol.tools.run_all --config "${PATROL_CONFIG:-configs/rk3576.yaml}" --no-cloud "$@"
