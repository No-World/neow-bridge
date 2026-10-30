#!/usr/bin/env bash
# 构建 NeowBridge 隔离运行时目录 runtime/：
#   - 独立 cwd ⇒ 游戏的 saves/ preferences/ runs/ 全部落在 runtime/ 下，
#     与玩家真实存档（游戏目录 saves/）完全隔离，互不可见
#   - 全解锁偏好（四角色 / 全卡牌遗物 / 进阶 20）+ 抑制新手教程弹窗
#   - 硬链接游戏本体 jar（0 拷贝），ModTheSpire 用创意工坊已知可用版本
# 用法: scripts/make_runtime.sh [STS目录]
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
[[ -f "$STS_DIR/desktop-1.0.jar" ]] || { echo "ERROR: 找不到 STS 目录" >&2; exit 1; }

WORKSHOP_MTS=""
for w in "/mnt/d/Program Files (x86)/Steam/steamapps/workshop/content/646570/1605060445/ModTheSpire.jar" \
         "/mnt/c/Program Files (x86)/Steam/steamapps/workshop/content/646570/1605060445/ModTheSpire.jar"; do
  [[ -f "$w" ]] && WORKSHOP_MTS="$w" && break
done

mkdir -p "$RT/saves" "$RT/runs" "$RT/mods"
RT_PREFS="$RT/preferences" ; export RT_PREFS

# ------------------------------------------------------------------
# 全解锁偏好。注意：游戏偏好是 gson JSON（值全为字符串），非 properties！
# 键名依据 UnlockTracker / CharStat / TipTracker / SaveSlotScreen 反编译勘察。
# ------------------------------------------------------------------
python3 - <<'PYEOF'
import json, os
P = os.environ['RT_PREFS']
os.makedirs(P, exist_ok=True)

def w(name, d):
    with open(os.path.join(P, name), 'w', encoding='utf-8') as f:
        json.dump({k: str(v) for k, v in d.items()}, f, indent=2, ensure_ascii=False)

# 默认锁定的卡牌/遗物/角色（UnlockTracker.refresh()）全部置 2 = 已解锁
unlocks = {"The Silent": 2, "Defect": 2, "Watcher": 2}
unlocks.update({k: 2 for k in [
    "Havoc","Sentinel","Exhume","Wild Strike","Evolve","Immolate",
    "Heavy Blade","Spot Weakness","Limit Break",
    "Concentrate","Setup","Grand Finale","Cloak And Dagger","Accuracy","Storm of Steel",
    "Bane","Catalyst","Corpse Explosion",
    "Rebound","Undo","Echo Form","Turbo","Sunder","Meteor Strike","Hyperbeam","Recycle","Core Surge",
    "Prostrate","Blasphemy","Devotion","ForeignInfluence","Alpha","MentalFortress","SpiritShield","Wish","Wireheading",
    "Omamori","Prayer Wheel","Shovel","Art of War","The Courier","Pandora's Box","Blue Candle","Dead Branch",
    "Singing Bowl","Du-Vu Doll","Smiling Mask","Tiny Chest","Cables","DataDisk","Emotion Chip","Runic Capacitor",
    "Turnip","Symbiotic Virus","Akabeko","Yang","CeramicFish","StrikeDummy","TeardropLocket","CloakClasp",
]})
w("STSUnlocks", unlocks)

# 解锁进度：UnlockLevel 拉满（启动时 retroactiveUnlock 会补齐一切）
w("STSUnlockProgress", {
    "IRONCLADUnlockLevel": 5, "THE_SILENTUnlockLevel": 5,
    "DEFECTUnlockLevel": 5, "WATCHERUnlockLevel": 5,
    "IRONCLADProgress": 99999, "THE_SILENTProgress": 99999,
    "DEFECTProgress": 99999, "WATCHERProgress": 99999,
    "IRONCLADCurrentCost": 300, "THE_SILENTCurrentCost": 300,
    "DEFECTCurrentCost": 300, "WATCHERCurrentCost": 300,
})

# 每角色：WIN_COUNT≥1 解锁进阶模式；ASCENSION_LEVEL=20 解锁进阶 20
for f in ("STSDataVagabond", "STSDataTheSilent", "STSDataDefect", "STSDataWatcher"):
    w(f, {"WIN_COUNT": 1, "ASCENSION_LEVEL": 20, "LAST_ASCENSION_LEVEL": 20,
          "LOSE_COUNT": 0, "WIN_STREAK": 0, "BEST_WIN_STREAK": 0})

# 抑制全部新手教程弹窗（否则 FTUE 以 CurrentScreen.FTUE 阻塞自动化）
w("STSTips", {k: "true" for k in [
    "NEOW_SKIP","NEOW_INTRO","NO_FTUE","COMBAT_TIP","BLOCK TIP","POWER_TIP","M_POWER_TIP",
    "ENERGY_USE_TIP","SHUFFLE_TIP","POTION_TIP","CARD_REWARD_TIP","INTENT_TIP","RELIC_TIP"]})

# 存档位：预置已命名的默认档（空位上新建，避免首次启动弹存档位选择屏）
w("STSSaveSlots", {"DEFAULT_SLOT": 0, "PROFILE_NAME": "Neow"})
w("STSPlayer", {"name": "Neow", "alias": "Neow", "ftuePopupShown": "true"})
print("preferences OK")
PYEOF

# ------------------------------------------------------------------
# jar 布置：游戏本体硬链接（只读）；MTS 优先创意工坊版；mod 拷贝
# ------------------------------------------------------------------
winpath() { wslpath -w "$1"; }

if [[ ! -f "$RT/desktop-1.0.jar" ]]; then
  cmd.exe /c mklink /H "$(winpath "$RT/desktop-1.0.jar")" "$(winpath "$STS_DIR/desktop-1.0.jar")" >/dev/null \
    || cp "$STS_DIR/desktop-1.0.jar" "$RT/desktop-1.0.jar"
fi

# MTS 内部子进程需要游戏目录旁的 jre/（目录联接，0 拷贝）
if [[ ! -e "$RT/jre" ]]; then
  cmd.exe /c mklink /J "$(winpath "$RT/jre")" "$(winpath "$STS_DIR/jre")" >/dev/null \
    || echo "WARN: jre junction 创建失败（MTS 子进程可能找不到 JRE）" >&2
fi

if [[ -n "$WORKSHOP_MTS" ]]; then
  cp -f "$WORKSHOP_MTS" "$RT/ModTheSpire.jar"
  echo "ModTheSpire: 创意工坊版（已知可用）"
else
  cp -f "$ROOT/libs/ModTheSpire.jar" "$RT/ModTheSpire.jar"
  echo "ModTheSpire: 自建版（未找到创意工坊版）"
fi

[[ -f "$ROOT/build/NeowBridge.jar" ]] || "$ROOT/scripts/build.sh" "$STS_DIR"
cp -f "$ROOT/build/NeowBridge.jar" "$RT/mods/NeowBridge.jar"
cp -f "$ROOT/libs/BaseMod.jar" "$RT/mods/BaseMod.jar"

cat > "$RT/README.md" <<'EOF'
# NeowBridge 自动化运行时（隔离沙盒）

此目录是游戏的独立工作目录：对局自动存档、偏好、解锁、跑局记录全部落在这里，
**不会读写游戏安装目录下的 saves/ preferences/**（玩家真实存档不受影响）。

- preferences/ —— 全解锁 + 进阶20 + 无教程弹窗的独立档案"Neow"（可随时删除重建）
- saves/ runs/ —— 自动化对局的存档与历史
- desktop-1.0.jar —— 指向游戏本体的硬链接（只读）
- jre/ —— 指向游戏自带 JRE 的目录联接
- mods/ —— BaseMod + NeowBridge

启动: scripts/launch.sh
EOF

echo "OK: $RT"
