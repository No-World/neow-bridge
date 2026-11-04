#!/usr/bin/env bash
# 干净重启真游戏并等待主菜单就绪。
# 用法: scripts/relaunch.sh [port] [timeout_sec]
set -uo pipefail
PORT="${1:-51234}"
TIMEOUT="${2:-300}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

echo "=== kill java ==="
powershell.exe -NoProfile -Command "Get-Process java -ErrorAction SilentlyContinue | Stop-Process -Force" >/dev/null 2>&1
sleep 8

echo "=== clear autosaves ==="
rm -f runtime/saves/*.autosave runtime/saves/*.autosave.backUp 2>/dev/null
echo "remaining: $(ls runtime/saves 2>/dev/null | wc -l)"

echo "=== launch (port $PORT) ==="
NEOW_BRIDGE_PORT="$PORT" ./scripts/launch.sh >/dev/null 2>&1

elapsed=0
while [ "$elapsed" -lt "$TIMEOUT" ]; do
    sleep 6
    elapsed=$((elapsed + 6))
    out=$(NEOW_BRIDGE_PORT="$PORT" timeout 10 python3 scripts/rpc.py ping 2>/dev/null || true)
    if printf '%s' "$out" | grep -q '"main_menu_built": true'; then
        echo "READY after ${elapsed}s"
        printf '%s\n' "$out"
        exit 0
    fi
    if [ $((elapsed % 30)) -eq 0 ]; then
        echo "  ${elapsed}s ... $(printf '%s' "$out" | head -c 120)"
    fi
done

echo "TIMEOUT after ${TIMEOUT}s"
NEOW_BRIDGE_PORT="$PORT" timeout 10 python3 scripts/rpc.py ping 2>&1 | head -2
exit 1
