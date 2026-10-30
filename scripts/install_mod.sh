#!/usr/bin/env bash
# 部署 NeowBridge（及其依赖）到 STS mods 目录，并放好 ModTheSpire.jar。
# 用法: scripts/install_mod.sh [STS目录]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STS_DIR="${1:-}"
if [[ -z "$STS_DIR" ]]; then
  for d in \
    "/mnt/d/Program Files (x86)/Steam/steamapps/common/SlayTheSpire" \
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/SlayTheSpire" \
    "$HOME/.steam/steam/steamapps/common/SlayTheSpire"; do
    [[ -f "$d/desktop-1.0.jar" ]] && STS_DIR="$d" && break
  done
fi
[[ -f "$STS_DIR/desktop-1.0.jar" ]] || { echo "ERROR: 找不到 STS 目录" >&2; exit 1; }

"$ROOT/scripts/build.sh" "$STS_DIR"
mkdir -p "$STS_DIR/mods"
cp "$ROOT/build/NeowBridge.jar" "$STS_DIR/mods/NeowBridge.jar"
cp "$ROOT/libs/BaseMod.jar" "$STS_DIR/mods/BaseMod.jar"
[[ -f "$STS_DIR/ModTheSpire.jar" ]] || cp "$ROOT/libs/ModTheSpire.jar" "$STS_DIR/ModTheSpire.jar"
echo "已部署到 $STS_DIR/mods/："
ls -la "$STS_DIR/mods/"
