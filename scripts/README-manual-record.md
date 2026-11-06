# 手动对局录制（局外自动 + 局内真人）

拿到「完整碎心轨迹」的最实际路径：**非战斗屏按存档自动推进，战斗屏交给你自己打**。

## 为什么这样分工

`*.run` 存档记录了完整的**决策链**（路线 / 选牌 / 营火 / 事件 / 遗物 / 购物），
但**没有战斗内逐回合出牌**。所以：

| 屏 | 谁决策 | 理由 |
|---|---|---|
| 地图 / 选牌 / 营火 / 事件 / 商店 / 奖励 / Boss 遗物 | **脚本**（按存档） | 存档有完整记录，可精确复刻 |
| 战斗（出牌 / 结束回合 / 药水） | **你** | 存档只有结果（回合数/伤害），无逐回合动作 |

这样你只需负责「打牌」，其它全自动 → 稳定拿到一条完整碎心轨迹。

## 用法

```bash
# 1. 启动游戏（到主菜单即可）
cd neow-bridge && NEOW_BRIDGE_PORT=45000 bash scripts/launch.sh

# 2. 选一个碎心存档（tools/heart_runs.py 可列出所有）
cd neow-sim && python3 tools/heart_runs.py --min-asc 10

# 3. 启动辅助对局（会自己 new_run，然后非战斗屏自动推进）
cd neow-bridge && NEOW_BRIDGE_PORT=45000 python3 scripts/assist_run.py \
    "/mnt/d/Program Files (x86)/Steam/steamapps/common/SlayTheSpire/runs/IRONCLAD/1737563900.run"

# 4. 游戏窗口里打战斗；非战斗屏会自动跳过
#    打完（或想停）按 Ctrl+C

# 5. 转成对拍轨迹
python3 scripts/manual_to_trace.py runtime/assist_2TG2WYWJ0Q041.jsonl

# 6. 用引擎对拍
cd ../neow-sim && PYTHONPATH=python python3 tools/parity_replay.py \
    ../neow-bridge/runtime/assist_2TG2WYWJ0Q041_trace.jsonl
```

## S/L 硬约束（自动强制）

项目产品原则 #1：**一次连续对局，绝不通过存档/读档重试**。
`assist_run.py` 内置 `ContinuityMonitor`，录制期间检测到以下迹象即**整段作废**
（文件改名为 `*.SL_VIOLATION`）：

- `floor` 回退
- 同一 floor 内 `turn` 回退
- 同一 turn 内怪物血量大幅回升（>5，排除合法回血）

## 产物与用途

| 文件 | 内容 | 用途 |
|---|---|---|
| `runtime/assist_<seed>.jsonl` | 原始录制：状态序列 + 每步 legal_actions | 中间产物 |
| `runtime/assist_<seed>_trace.jsonl` | 推断出动作后的 golden trace | 喂 `parity_replay.py` |

⚠️ **用途硬约束**（docs/parity-spec.md §1.5）：只用于**对拍验证**
（验证引擎与真游戏一致），**禁止**进训练集 / 行为克隆 / 评估基线。
