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
    private static int lastEvent = -1;
    private static int lastMonster = -1;
    private static String lastMoves = "";
    private static int lastPotion = -1;
    private static int lastRelic = -1;

    private RngWatch() {
    }

    public static void frame() {
        if (!ON) return;
        try {
            // 招式变化检测：即使 RNG counter 不动，也记录（捕捉 SetMoveAction 这类确定性换招）
            String mv = monsters();
            if (!mv.equals(lastMoves)) {
                System.out.println("[RNGW] moves " + mv
                        + " ai=" + aiCounter()
                        + " floor=" + AbstractDungeon.floorNum
                        + " screen=" + safeScreen()
                        + " room=" + roomName());
                lastMoves = mv;
            }
            if (AbstractDungeon.cardRng == null && AbstractDungeon.eventRng == null) return;
            int c = AbstractDungeon.cardRng.counter;
            int po = AbstractDungeon.potionRng == null ? -1 : AbstractDungeon.potionRng.counter;
            int re = AbstractDungeon.relicRng == null ? -1 : AbstractDungeon.relicRng.counter;
            int ev = AbstractDungeon.eventRng == null ? -1 : AbstractDungeon.eventRng.counter;
            int mo = AbstractDungeon.monsterRng == null ? -1 : AbstractDungeon.monsterRng.counter;
            if (c == lastCard && po == lastPotion && re == lastRelic
                    && ev == lastEvent && mo == lastMonster) return;

            String screen;
            try {
                screen = Screens.current();
            } catch (Throwable t) {
                screen = "?";
            }
            System.out.println("[RNGW] card " + lastCard + "->" + c
                    + " potion " + lastPotion + "->" + po
                    + " relic " + lastRelic + "->" + re
                    + " event " + lastEvent + "->" + ev
                    + " monster " + lastMonster + "->" + mo
                    + " ai=" + aiCounter()
                    + " floor=" + AbstractDungeon.floorNum
                    + " screen=" + screen
                    + " room=" + roomName()
                    + " mons=" + monsters());
            lastCard = c;
            lastPotion = po;
            lastRelic = re;
            lastEvent = ev;
            lastMonster = mo;
            lastMoves = monsters();
        } catch (Throwable t) {
            // 静默：观察器不能影响对局
        }
    }

    private static String safeScreen() {
        try {
            return Screens.current();
        } catch (Throwable t) {
            return "?";
        }
    }

    private static String aiCounter() {
        try {
            return AbstractDungeon.aiRng == null ? "-" : String.valueOf(AbstractDungeon.aiRng.counter);
        } catch (Throwable t) {
            return "?";
        }
    }

    /** 每只存活怪的 id / nextMove / moveHistory，用固定格式便于肉眼对齐 */
    private static String monsters() {
        try {
            if (AbstractDungeon.getCurrRoom() == null
                    || AbstractDungeon.getCurrRoom().monsters == null) {
                return "-";
            }
            StringBuilder sb = new StringBuilder();
            for (com.megacrit.cardcrawl.monsters.AbstractMonster m
                    : AbstractDungeon.getCurrRoom().monsters.monsters) {
                if (m == null || m.isDeadOrEscaped()) continue;
                if (sb.length() > 0) sb.append(" | ");
                sb.append(m.id).append(":nm=").append(m.nextMove)
                        .append(":hist=").append(m.moveHistory)
                        .append(":hp=").append(m.currentHealth);
            }
            return sb.length() == 0 ? "-" : sb.toString();
        } catch (Throwable t) {
            return "?";
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
