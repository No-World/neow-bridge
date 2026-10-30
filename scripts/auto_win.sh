#!/usr/bin/env bash
# 一键：构建 + 启动游戏（隔离运行时）+ 铸造必赢路线（多次尝试直到胜利）
# 用法: scripts/auto_win.sh [seed] [ascension] [max_attempts]
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SEED="${1:-PMCWKZEF}"
ASC="${2:-0}"
MAXTRY="${3:-8}"

powershell.exe -NoProfile -Command "Get-Process java -ErrorAction SilentlyContinue | Stop-Process -Force" 2>/dev/null
sleep 2

if ! ./scripts/build.sh >/tmp/neow_build.log 2>&1; then
  echo BUILD-FAIL; tail -5 /tmp/neow_build.log; exit 1
fi
cp build/NeowBridge.jar runtime/mods/NeowBridge.jar

[ -f runtime/game.log ] && cp runtime/game.log "runtime/game_$(date +%H%M%S).log"
timeout 15 ./scripts/launch.sh >/dev/null 2>&1 || true

PY=/mnt/c/Users/NoWorld/AppData/Local/Python/bin/python.exe
RPC="$(wslpath -w scripts/rpc.py)"
for i in $(seq 1 12); do
  sleep 10
  P=$(timeout 20 "$PY" "$RPC" ping 2>/dev/null | head -1)
  if echo "$P" | grep -q 'CHAR_SELECT'; then echo MENU-READY; break; fi
done

"$PY" "$(wslpath -w scripts/drive_win.py)" "$SEED" "$ASC" "$MAXTRY"
