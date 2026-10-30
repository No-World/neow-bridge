package com.neow.bridge;

import basemod.BaseMod;
import basemod.ReflectionHacks;
import basemod.interfaces.PostInitializeSubscriber;
import basemod.interfaces.PostUpdateSubscriber;
import basemod.interfaces.PreUpdateSubscriber;
import com.evacipated.cardcrawl.modthespire.lib.SpireInitializer;
import com.megacrit.cardcrawl.core.CardCrawlGame;
import com.megacrit.cardcrawl.core.Settings;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

/**
 * NeowBridge 模组入口。
 *
 * 把杀戮尖塔的全部功能以统一 gym 协议（docs/protocol.md v0.3）开放为外部可调用接口：
 *  - 观测：完整游戏状态 → observation JSON
 *  - 控制：结构化动作 → 与玩家操作等价的游戏内路径（逐帧任务序列）
 *  - 命令：new_run / step / get_state / quit_run / kill_game / config（本地 TCP 或 stdio）
 *
 * 公平性约束（游戏内形态）：不提供任何存档/读档命令；观测只含玩家合法可知信息
 * （抽牌堆默认只暴露集合；cheat_draw_order 仅供离线对拍研究，需显式开启）。
 */
@SpireInitializer
public class NeowBridgeMod implements PreUpdateSubscriber, PostUpdateSubscriber, PostInitializeSubscriber {

    private static final Logger logger = LogManager.getLogger(NeowBridgeMod.class.getName());

    public static void initialize() {
        logger.info("NeowBridge {} initializing (protocol {})",
                Bridge.BRIDGE_VERSION, Bridge.PROTOCOL_VERSION);
        new NeowBridgeMod();
    }

    public NeowBridgeMod() {
        BaseMod.subscribe(this);
    }

    @Override
    public void receivePostInitialize() {
        if (Bridge.CFG.fastMode) {
            Settings.FAST_MODE = true;
        }
        Server.start();
        logger.info("NeowBridge ready. TCP 127.0.0.1:37771 (NEOW_BRIDGE_PORT / NEOW_BRIDGE_BIND / NEOW_BRIDGE_STDIO)");
    }

    @Override
    public void receivePreUpdate() {
        Bridge.onPreUpdate();
    }

    @Override
    public void receivePostUpdate() {
        skipSplashIfNeeded();
        Bridge.onPostUpdate();
    }

    /**
     * 跳过启动闪屏：某些环境下（音频设备争用/内存压力）SplashScreen 的阶段计时永不推进。
     * 直接置 FADE_OUT + 短计时，绕过全部动画与音效路径。
     */
    private static int splashSkipFrames = -1;

    private static void skipSplashIfNeeded() {
        if (CardCrawlGame.mode == CardCrawlGame.GameMode.SPLASH && CardCrawlGame.splashScreen != null) {
            if (splashSkipFrames < 0) {
                splashSkipFrames = 0;
            }
            if (++splashSkipFrames > 30) { // 等半秒，给正常启动让路
                Object phase = ReflectionHacks.getPrivate(CardCrawlGame.splashScreen,
                        com.megacrit.cardcrawl.screens.splash.SplashScreen.class, "phase");
                @SuppressWarnings({"unchecked", "rawtypes"})
                Object fadeOut = Enum.valueOf((Class<Enum>) phase.getClass(), "FADE_OUT");
                ReflectionHacks.setPrivate(CardCrawlGame.splashScreen,
                        com.megacrit.cardcrawl.screens.splash.SplashScreen.class, "phase", fadeOut);
                ReflectionHacks.setPrivate(CardCrawlGame.splashScreen,
                        com.megacrit.cardcrawl.screens.splash.SplashScreen.class, "timer", 0.05f);
            }
        } else {
            splashSkipFrames = -1;
        }
    }
}
