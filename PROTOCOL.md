# NeowBridge 传输协议（transport）v1

> gym 语义（observation / action / legal_actions / reward / done）以 [docs/protocol.md](../docs/protocol.md) v0.3 为唯一真相源。
> 本文只定义**传输封装**：怎么把 gym 五元组在进程间搬运。

## 通道

| 通道 | 默认 | 说明 |
|---|---|---|
| 本地 TCP | `127.0.0.1:37771` | 推荐：便于多实例与外部进程管理 |
| stdio | 关闭 | `NEOW_BRIDGE_STDIO=1` 启用（调试用） |

环境变量：`NEOW_BRIDGE_PORT`、`NEOW_BRIDGE_BIND`（如需从 WSL 访问 Windows 宿主，绑 `0.0.0.0` 或对应网卡 IP）。

分帧：UTF-8 JSON，**一行一条**（`\n` 结尾），请求/回复严格一一对应、按序返回。

## 请求

```jsonc
{"id": 1, "cmd": "ping"}
{"id": 2, "cmd": "new_run", "character": "IRONCLAD", "ascension": 20, "seed": "PMCWKZEF"}
{"id": 3, "cmd": "step", "action": {"type": "play", "card_index": 0, "target": 1}}
{"id": 4, "cmd": "get_state"}
{"id": 5, "cmd": "quit_run"}
{"id": 6, "cmd": "kill_game"}
{"id": 7, "cmd": "config", "fast_mode": true, "settle_stable_frames": 3, "timeout_ms": 60000, "cheat_draw_order": false}
```

- `id`：任意 JSON 值，原样回显（用于请求关联）
- `seed`：字符串（种子代码，如 `"PMCWKZEF"`）或整数；省略则随机
- `cheat_draw_order`：仅对拍研究用——让抽牌堆暴露真实顺序（破坏"不窥探"约束，游戏内形态禁止开启）

## 回复

```jsonc
{"id": 2, "ok": true, "result": { /* gym 五元组或简单对象 */ }}
{"id": 3, "ok": false, "error": "action type 'boss' not legal on screen map", "error_type": "invalid_action"}
```

`error_type`：`bad_request`（JSON 不合法）· `unknown_command` · `invalid_action` / `invalid_params` ·
`busy`（前一命令未完成，串行执行）· `timeout`（落定超时）· `internal`

## gym 五元组（step / new_run / get_state 的 result）

```jsonc
{
  "observation": { /* 见 docs/protocol.md v0.3 */ },
  "legal_actions": [ { "type": "play", "card_index": 0, "target": 1 }, ... ],
  "reward": 0.0,          // 终局 ±1（胜/负），其余 0
  "done": false,
  "info": {
    "protocol_version": "0.3", "bridge_version": "0.2.0",
    "screen": "combat", "in_dungeon": true, "room": "MonsterRoom",
    "act": 1, "floor": 1
  }
}
```

### 语义要点

- **一步一回复**：`step` 在游戏线程执行动作并等待状态落定（多帧指纹稳定 + 队列清空 + 回合按钮就绪）后才返回——一次请求即到达下一个决策点，天然适合请求/回复式 gym 封装
- **非法动作直接报错**：训练侧负责 mask（合法集合已在 `legal_actions` 给全）
- **选择屏参数化**：hand_select / grid_select / scry 在 `legal_actions` 中是描述符（indices 为空数组占位），合法组合约束在 `screen` 字段内（`num` / `num_to_select` / `any_number` / `can_pick_zero`），`step` 时校验具体 indices
- **reward 项下标动态**：领取后列表收缩并重排，`take_indices` 以**当前** `screen.items` 下标为准；遇 CARD 项会停在 `card_reward` 屏
- **药水满槽**：先 `potion_discard` 腾位，再领药水奖励（与游戏 UI 同路径）
- **map 节点下标**：`observation.map.nodes[].index`（全图平铺编号，含无出边的占位节点）；`map.choices` 给出当前可选集
- **null 省略**：gson 默认省略 null 值字段（如未上图时 `map.current` 不存在，`current_x=0,current_y=-1` 表示涅奥起点）

## 隔离与安全

- **runtime 隔离**：自动化对局运行于独立工作目录（`scripts/make_runtime.sh`），saves/preferences/runs 与玩家真实存档互不可见
- **无存档命令**：不提供任何存档/读档操作（游戏内形态硬约束，见 docs/protocol.md 设计原则）
- **抽牌堆默认脱序**：观测按 id+升级数排序输出，不泄露洗牌顺序

## 手动调试

```
scripts/rpc.py ping
scripts/rpc.py get_state
scripts/rpc.py new_run IRONCLAD 20 PMCWKZEF
scripts/rpc.py step '{"type":"event","option_index":0}'
scripts/rpc.py get_state --raw    # 完整 JSON
```
