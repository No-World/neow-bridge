#!/usr/bin/env bash
# NeowBridge 构建：纯 javac + jar，不依赖 gradle。
# 用法: scripts/build.sh [STS目录]   （默认自动探测 Steam 常见路径）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build"

STS_DIR="${1:-}"
if [[ -z "$STS_DIR" ]]; then
  for d in \
    "/mnt/d/Program Files (x86)/Steam/steamapps/common/SlayTheSpire" \
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/SlayTheSpire" \
    "$HOME/.steam/steam/steamapps/common/SlayTheSpire"; do
    [[ -f "$d/desktop-1.0.jar" ]] && STS_DIR="$d" && break
  done
fi
if [[ -z "$STS_DIR" || ! -f "$STS_DIR/desktop-1.0.jar" ]]; then
  echo "ERROR: 找不到 desktop-1.0.jar，请传入 STS 安装目录: scripts/build.sh <stsDir>" >&2
  exit 1
fi

CP="$STS_DIR/desktop-1.0.jar:$ROOT/libs/ModTheSpire.jar:$ROOT/libs/BaseMod.jar"
rm -rf "$OUT/classes"
mkdir -p "$OUT/classes"

echo "== javac (release 8) =="
javac --release 8 -encoding UTF-8 -nowarn -cp "$CP" \
  -d "$OUT/classes" \
  $(find "$ROOT/src/main/java" -name '*.java')

echo "== jar =="
jar cf "$OUT/NeowBridge.jar" -C "$OUT/classes" . -C "$ROOT/src/main/resources" .
echo "OK: $OUT/NeowBridge.jar"
