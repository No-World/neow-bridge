# neow-bridge — NeowBridge：杀戮尖塔模组

真游戏对接层，**做成一个 STS 模组**（ModTheSpire + BaseMod），从 **fork CommunicationMod** 起步。

## 职责

- **观测**：完整游戏状态 → 统一 gym 协议 JSON（`../docs/protocol.md`）
- **控制**：结构化动作 → 游戏内点击等价操作
- **对拍裁判**：同种子、同动作序列与 neow-sim 逐状态 diff（黄金用例见 `../neow-sim/tests/conformance/`）
- **评测/演示**：无人值守多实例跑局、（可选）直播叠加胜率

## 相比 CommunicationMod 原版的增强点

1. 去动画/加速执行（评测与多开的速度关键）
2. 暴露并尽量固定游戏 RNG 种子
3. 批量动作下发、补全原版缺失的状态字段
4. 原生本地 socket 通信（便于多实例管理，stdio 保留兼容）

## 技术栈与构建

- Java + Gradle；运行依赖 ModTheSpire + BaseMod（放入游戏 `mods/` 目录）
- 完整编译需要游戏本体 jar（`desktop-1.0.jar`，位于 Steam 安装目录），在 `gradle.properties` 中配置 `stsDir`
- CI 说明见 `.github/workflows/bridge.yml`：公开 runner 拿不到游戏 jar，只做资源校验；完整构建在本机或自建 runner

## 开发路线

1. fork CommunicationMod，原样跑通（收发一条 JSON）
2. 接入统一协议 schema，替换其序列化
3. 逐项加增强（先做"去动画提速"与"种子暴露"，它们直接决定对拍与评测效率）
