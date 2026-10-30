# neow-bridge — NeowBridge：杀戮尖塔模组层

真游戏对接层：**把杀戮尖塔的全部功能以统一 gym 协议开放为外部可调用接口**（观测 + 控制 + 开局/收局）。
协议真相源：[../docs/protocol.md](../docs/protocol.md) v0.3；传输封装：[PROTOCOL.md](PROTOCOL.md)。

## 状态（P2 前置，已跑通端到端）

- [x] TCP/stdio 服务器，逐行 JSON，`ping/new_run/step/get_state/quit_run/kill_game/config`
- [x] 观测：meta/玩家/五牌堆（含顺序标记）/遗物/药水/怪物（意图/格挡/招式史）/全图拓扑/RNG 计数器/各决策屏专有字段
- [x] 动作：play/end_turn/potion/potion_discard/relic/map/boss/shop_*/event/rest/reward/card_pick/card_bowl/skip/
      chest_open/proceed/cancel/hand_select/grid_select/scry
- [x] 合法动作枚举 + 非法动作拒绝；选择屏参数化描述符
- [x] 落定检测（队列清空 + 结束回合按钮就绪 + 多帧指纹稳定 + 地图/遗物动画等待）
- [x] 隔离运行时：`runtime/` 独立 cwd，存档/偏好/跑局记录与玩家真实存档互不可见；
      全解锁 + 进阶20 + 无教程弹窗的独立档案；FAST_MODE 去动画
- [x] 已验证：涅奥事件→赐福（变牌/加血）→地图→战斗（出牌/结束回合/格挡/意图/敌人回合）→
      奖励（金币/卡牌三选一/唱歌碗/跳过）→ complete→地图循环

## 快速开始（Windows 游戏 + WSL 开发环境）

```bash
scripts/fetch_libs.sh                 # BaseMod + ModTheSpire（或用创意工坊版）
scripts/build.sh                      # javac --release 8 + jar（自动探测 STS 目录）
scripts/make_runtime.sh               # 隔离运行时（全解锁档案/硬链接/junction）
scripts/launch.sh                     # PowerShell Start-Process 启动（Windows 侧进程）
# NEOW_BRIDGE_VISIBLE=1 scripts/launch.sh   # 调试时显示游戏窗口
python scripts/rpc.py ping            # 手动单发调试（Windows 侧 python）
python scripts/rpc.py new_run IRONCLAD 20 PMCWKZEF
python scripts/rpc.py step '{"type":"play","card_index":0,"target":0}'
```

gradle 构建（Windows 侧）也能用：`gradle build packageMod`（见 build.gradle）。

## 架构

```
com.neow.bridge
├── NeowBridgeMod   入口：PreUpdate/PostUpdate 钩子 + 闪屏自动跳过
├── Bridge          中枢：命令队列 + 执行状态机（RUNNING→SETTLING→响应）+ 配置
├── Server          传输：TCP(默认 127.0.0.1:37771)/stdio，逐行 JSON
├── Commands        命令分发 + gym 五元组组装
├── Actions         动作 → 逐帧任务序列（编译期校验 + 与玩家点击等价的游戏内路径）
├── Legal           合法动作枚举（各决策面全覆盖）
├── Obs             观测构建（协议 v0.3；抽牌堆默认脱序，cheat_draw_order 仅供对拍）
├── Screens         决策面识别（CurrentScreen/RoomPhase/房间类型归一化）
├── Stability       落定判定（硬条件 + 指纹稳定）
└── patch/          鼠标悬停/点击模拟补丁（移植自 CommunicationMod，MIT）
```

关键设计：**一步一回复**——`step` 在游戏线程逐帧执行任务、等待落定后返回完整五元组；
所有游戏状态读写都发生在游戏线程（libGDX），外部线程只做 IO。

## 与 CommunicationMod 的差异

1. 统一 gym 协议（结构化动作/合法动作集/终局奖励）替代文本命令
2. 一步一回复的请求/回复模型（原版为事件推送 + 状态机）
3. 隔离运行时（原版直接用游戏目录存档）+ 全解锁档案 + 首房间/闪屏等自动化坑全部绕过
4. 观测补全：RNG 计数器、地图全拓扑、牌堆顺序标记、意图伤害/连击数等

## 已知环境问题（非代码）

- 本机提交内存紧张时（页面文件仅 14GB），游戏的原生贴图解码会失败/卡住
  （libgdx 1.9.5 jpgd 原生分配失败）。对策：页面文件调大或释放内存后再跑；
  无人值守跑局需要监督进程自动重启实例
- 双实例会抢 37771 端口：`scripts/launch.sh` 前先确保无残留 java 进程

## 对拍（M4，待做）

`neow-sim/tests/conformance/` 黄金用例 ↔ 本桥 runner 逐状态 diff（见 docs/roadmap.md P2）。
