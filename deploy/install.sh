#!/usr/bin/env bash
# RK3576 上安装巡检系统边缘端。在解压后的部署包根目录里运行：
#
#   sudo ./deploy/install.sh                    # 装到 /opt/patrol，注册 systemd 服务
#   ./deploy/install.sh --prefix ~/patrol --no-systemd   # 不要 root，只建环境，手动起
#   PYTHON=/usr/bin/python3.11 ./deploy/install.sh ...   # 指定解释器（默认 python3）
#
# 做的事：校验 SHA256SUMS → 拷到 PREFIX → 建 venv 装依赖 → 跑接口一致性校验 → 注册服务。
# 不会自动启动服务：现场配置（串口、相机、标定表）改好之后再 systemctl start patrol.target。
set -euo pipefail

PY="${PYTHON:-python3}"
PREFIX=/opt/patrol
RUN_USER="${SUDO_USER:-$(id -un)}"
SYSTEMD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --user) RUN_USER="$2"; shift 2 ;;
    --no-systemd) SYSTEMD=0; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "未知参数 $1" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "$0")/.." && pwd)"
say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

say "检查 Python（需要 ≥ 3.10）"
"$PY" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python %s 太旧，需要 3.10 以上" % sys.version.split()[0])
print("Python", sys.version.split()[0])
PY

if [ -f "$SRC/SHA256SUMS" ]; then
  say "校验部署包完整性"
  (cd "$SRC" && sha256sum --quiet -c SHA256SUMS)
fi

if [ "$SRC" != "$(realpath -m "$PREFIX")" ]; then
  say "拷贝到 $PREFIX"
  mkdir -p "$PREFIX"
  cp -a "$SRC"/. "$PREFIX"/
fi
cd "$PREFIX"

say "建 venv 并安装依赖（首次约需几分钟）"
if ! "$PY" -m venv venv; then
  echo "建 venv 失败：多半是没装 venv 模块，Debian/Ubuntu 上先 sudo apt install python3-venv" >&2
  exit 1
fi
venv/bin/pip install --upgrade pip >/dev/null
venv/bin/pip install -r deploy/requirements-rk3576.txt
if [ "$(uname -m)" != "aarch64" ]; then
  echo "注意：当前不是 aarch64，没有安装 rknn-toolkit-lite2。检测器与 L3 需改用 onnx 后端才能在这台机器上跑。"
fi

say "接口一致性校验"
PYTHONPATH="$PREFIX" venv/bin/python -m patrol.tools.validate

if [ "$SYSTEMD" = "1" ]; then
  if [ "$(id -u)" != "0" ]; then
    echo "注册 systemd 服务需要 root：请用 sudo 重跑，或加 --no-systemd 手动启动（deploy/run_edge.sh）" >&2
    exit 1
  fi
  say "注册 systemd 服务（运行用户 $RUN_USER）"
  for f in deploy/systemd/*; do
    sed -e "s#@PREFIX@#$PREFIX#g" -e "s#@USER@#$RUN_USER#g" "$f" > "/etc/systemd/system/$(basename "$f")"
  done
  usermod -aG dialout,video "$RUN_USER" || true
  chown -R "$RUN_USER" "$PREFIX"
  systemctl daemon-reload
  systemctl enable patrol.target
  cat <<EOF

安装完成。启动前先改好现场配置（见 deploy/README-deploy.md 第 3 节）：
  $PREFIX/configs/real.yaml    串口与相机设备号
  $PREFIX/configs/scene.yaml   现场标定表（targets）
然后：
  sudo systemctl start patrol.target
  journalctl -u 'patrol-*' -f
EOF
else
  cat <<EOF

安装完成（未注册服务）。手动启动：
  cd $PREFIX && ./deploy/run_edge.sh
EOF
fi
