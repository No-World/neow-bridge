package com.neow.bridge;

import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.random.Random;

/**
 * cardRng 消耗点观察器（**纯轮询**，不 patch 任何游戏方法）。
 *
 * 背景：CardGroup.getRandomCard / CardRewardScreen.open 上的 SpirePatch 都会让
 * ModTheSpire 卡死在 "Adding Postfix..." 阶段（游戏进程响应但永远进不去主菜单）。
 * 而只靠 `cardRng.counter` 的终值又无法区分「哪几次消耗属于哪一步」。
 *
 * 做法：每帧对比 counter，一旦变化就打印「新值 + 当前屏 + 层数 + 增量」。
 * 这样能看到消耗发生在「战斗结束奖励生成」还是「点开卡牌奖励」等具体时机。
 *
 * 开关：环境变量 NEOW_TRACE_RNG=1
 */
public final class RngWatch {

    private static final boolean ON = "1".equals(System.getenv("NEOW_TRACE_RNG"));

    private static int lastCard = -1;
    private static int lastPotion = -1;
    private static int lastRelic = -1;

    private RngWatch() {
    }

    public static void frame() {
        if (!ON) return;
        try {
            if (AbstractDungeon.cardRng == null) return;
            int c = AbstractDungeon.cardRng.counter;
            int po = AbstractDungeon.potionRng == null ? -1 : AbstractDungeon.potionRng.counter;
            int re = AbstractDungeon.relicRng == null ? -1 : AbstractDungeon.relicRng.counter;
            if (c == lastCard && po == lastPotion && re == lastRelic) return;

            String screen;
            try {
                screen = Screens.current();
            } catch (Throwable t) {
                screen = "?";
            }
            System.out.println("[RNGW] card " + lastCard + "->" + c
                    + " potion " + lastPotion + "->" + po
                    + " relic " + lastRelic + "->" + re
                    + " floor=" + AbstractDungeon.floorNum
                    + " screen=" + screen
                    + " room=" + roomName());
            lastCard = c;
            lastPotion = po;
            lastRelic = re;
        } catch (Throwable t) {
            // 静默：观察器不能影响对局
        }
    }

    private static String roomName() {
        try {
            if (AbstractDungeon.getCurrRoom() == null) return "-";
            return AbstractDungeon.getCurrRoom().getClass().getSimpleName();
        } catch (Throwable t) {
            return "?";
        }
    }
}
