#!/usr/bin/env bash
# 获取编译依赖（不入库的第三方 jar）：
#   libs/BaseMod.jar     —— GitHub Releases
#   libs/ModTheSpire.jar —— 本地源码构建（JitPack 因需游戏 jar 无法构建）
# 用法: scripts/fetch_libs.sh [STS目录]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/libs"

echo "== BaseMod =="
curl -sL --max-time 90 -o "$ROOT/libs/BaseMod.jar" \
  "https://github.com/daviscook477/BaseMod/releases/download/v5.5.0/BaseMod.jar"
ls -la "$ROOT/libs/BaseMod.jar"

echo "== ModTheSpire（源码构建，需 JDK8 与游戏 jar）=="
STS_DIR="${1:-}"
if [[ -z "$STS_DIR" ]]; then
  for d in \
    "/mnt/d/Program Files (x86)/Steam/steamapps/common/SlayTheSpire" \
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/SlayTheSpire"; do
    [[ -f "$d/desktop-1.0.jar" ]] && STS_DIR="$d" && break
  done
fi
[[ -f "$STS_DIR/desktop-1.0.jar" ]] || { echo "!! 传 入STS目录 以构建 ModTheSpire（或手动放置 libs/ModTheSpire.jar，推荐创意工坊版本）" >&2; exit 0; }

TMP=$(mktemp -d)
git clone --depth 1 -q https://github.com/kiooeht/ModTheSpire.git "$TMP/mts"
mkdir -p "$TMP/lib" && cp "$STS_DIR/desktop-1.0.jar" "$TMP/lib/"
( cd "$TMP/mts" && JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64 mvn -q -DskipTests package \
  && cp target/ModTheSpire.jar "$ROOT/libs/ModTheSpire.jar" ) \
  || echo "!! 构建失败；可直接用创意工坊 ModTheSpire.jar（workshop/content/646570/1605060445）"
ls -la "$ROOT/libs/" || true
rm -rf "$TMP"
