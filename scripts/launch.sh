#!/usr/bin/env bash
# 从隔离运行时目录启动游戏（ModTheSpire + BaseMod + NeowBridge）。
# 存档/偏好全部落在 runtime/ 下，不影响玩家真实存档。
#
# 通过 PowerShell Start-Process 启动为 Windows 侧自有进程，
# 避免 WSL interop 子进程随 WSL 实例空闲回收被杀。
#
# 环境变量: NEOW_BRIDGE_PORT(默认37771) NEOW_BRIDGE_BIND(默认127.0.0.1)
# 日志: runtime/game.log
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RT="$ROOT/runtime"
STS_DIR="${1:-}"
if [[ -z "$STS_DIR" ]]; then
  for d in \
    "/mnt/d/Program Files (x86)/Steam/steamapps/common/SlayTheSpire" \
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/SlayTheSpire" \
    "$HOME/.steam/steam/steamapps/common/SlayTheSpire"; do
    [[ -f "$d/desktop-1.0.jar" ]] && STS_DIR="$d" && break
  done
fi
[[ -f "$RT/ModTheSpire.jar" ]] || { echo "runtime 未就绪，先运行 scripts/make_runtime.sh" >&2; exit 1; }

EXTRA=""
[[ "${NEOW_BRIDGE_VISIBLE:-0}" == "1" ]] && EXTRA="-Visible"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$ROOT/scripts/launch.ps1")" \
  -RtDir "$(wslpath -w "$RT")" \
  -JavaExe "$(wslpath -w "$STS_DIR/jre/bin/java.exe")" \
  -Port "${NEOW_BRIDGE_PORT:-37771}" \
  -Bind "${NEOW_BRIDGE_BIND:-127.0.0.1}" $EXTRA

echo "已启动（Windows 侧进程，独立于 WSL 生命周期）。日志: runtime/game.log"
