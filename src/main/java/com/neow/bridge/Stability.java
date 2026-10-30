package com.neow.bridge;

import com.megacrit.cardcrawl.actions.GameActionManager;
import com.megacrit.cardcrawl.core.CardCrawlGame;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.relics.AbstractRelic;
import com.megacrit.cardcrawl.rooms.AbstractRoom;
import com.megacrit.cardcrawl.rooms.EventRoom;
import com.megacrit.cardcrawl.rooms.NeowRoom;
import com.megacrit.cardcrawl.rooms.VictoryRoom;

/**
 * 状态稳定判定：step 之后等待游戏"落定"到下一个决策点。
 *
 * 策略（比 CommunicationMod 的事件推送模型更简单，因为我们请求/回复式同步交互）：
 *  1. 硬条件：不在淡入淡出、战斗中动作队列清空且回到 WAITING_ON_USER、
 *     战斗未在收尾（isBattleOver 后仍有结算过渡）、事件动画播完、遗物无飞行动画；
 *  2. 指纹条件：连续 N 帧（默认 3）一个廉价状态指纹不变。
 * 两者同时满足才算落定。超时兜底由 Bridge 负责。
 */
public final class Stability {

    private Stability() {
    }

    /** 廉价状态指纹：任何会随结算推进而变化的量的杂糅。 */
    public static long fingerprint() {
        long h = 17;
        h = h * 31 + (CardCrawlGame.mode != null ? CardCrawlGame.mode.ordinal() : -1);
        if (AbstractDungeon.isPlayerInDungeon() && AbstractDungeon.getCurrRoom() != null) {
            AbstractRoom room = AbstractDungeon.getCurrRoom();
            h = h * 31 + room.getClass().getName().hashCode();
            h = h * 31 + (room.phase != null ? room.phase.ordinal() : -1);
            h = h * 31 + (room.event != null ? room.event.getClass().getName().hashCode() : 0);
            h = h * 31 + (AbstractDungeon.screen != null ? AbstractDungeon.screen.ordinal() : -1);
            h = h * 31 + (AbstractDungeon.isScreenUp ? 1 : 0);
            if (AbstractDungeon.player != null) {
                h = h * 31 + AbstractDungeon.player.currentHealth;
                h = h * 31 + AbstractDungeon.player.currentBlock;
                h = h * 31 + AbstractDungeon.player.gold;
                h = h * 31 + (AbstractDungeon.player.hand != null ? AbstractDungeon.player.hand.size() : 0);
                h = h * 31 + (AbstractDungeon.player.drawPile != null ? AbstractDungeon.player.drawPile.size() : 0);
                h = h * 31 + (AbstractDungeon.player.discardPile != null ? AbstractDungeon.player.discardPile.size() : 0);
                h = h * 31 + (AbstractDungeon.player.masterDeck != null ? AbstractDungeon.player.masterDeck.size() : 0);
                h = h * 31 + AbstractDungeon.player.relics.size();
                h = h * 31 + (AbstractDungeon.player.isDying ? 1 : 0);
                h = h * 31 + (AbstractDungeon.player.isDead ? 1 : 0);
            }
            if (room.monsters != null) {
                int n = 0;
                int alive = 0;
                for (com.megacrit.cardcrawl.monsters.AbstractMonster m : room.monsters.monsters) {
                    n = n * 31 + m.currentHealth;
                    n = n * 31 + m.currentBlock;
                    n = n * 31 + (m.isDeadOrEscaped() ? 1 : 0);
                    n = n * 31 + (m.isDying ? 1 : 0);
                    if (!m.isDeadOrEscaped()) {
                        alive++;
                    }
                }
                h = h * 31 + n;
                h = h * 31 + alive;
            }
            GameActionManager am = AbstractDungeon.actionManager;
            h = h * 31 + am.actions.size();
            h = h * 31 + am.preTurnActions.size();
            h = h * 31 + am.cardQueue.size();
            h = h * 31 + am.monsterQueue.size();
            h = h * 31 + (am.currentAction != null ? am.currentAction.hashCode() : 0);
            h = h * 31 + (am.phase != null ? am.phase.ordinal() : -1);
            h = h * 31 + (AbstractDungeon.isFadingOut ? 1 : 0);
            h = h * 31 + (AbstractDungeon.isFadingIn ? 1 : 0);
            if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.GRID && AbstractDungeon.gridSelectScreen != null) {
                h = h * 31 + AbstractDungeon.gridSelectScreen.selectedCards.size();
                h = h * 31 + (AbstractDungeon.gridSelectScreen.confirmScreenUp ? 1 : 0);
            }
            if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.HAND_SELECT && AbstractDungeon.handCardSelectScreen != null) {
                h = h * 31 + AbstractDungeon.handCardSelectScreen.selectedCards.size();
            }
            if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.COMBAT_REWARD && AbstractDungeon.combatRewardScreen != null) {
                h = h * 31 + AbstractDungeon.combatRewardScreen.rewards.size();
            }
        } else {
            h = h * 31 + (CardCrawlGame.mainMenuScreen != null ? 1 : 0);
            h = h * 31 + CardCrawlGame.mode.ordinal();
        }
        return h;
    }

    /**
     * 硬条件：当前帧是否处于"可决策/可汇报"的静止态。
     */
    public static boolean quiescent() {
        if (CardCrawlGame.mode != CardCrawlGame.GameMode.GAMEPLAY) {
            return true; // 菜单/角色选择——由上层判断是否目标态
        }
        if (!AbstractDungeon.isPlayerInDungeon() || AbstractDungeon.getCurrRoom() == null) {
            return true;
        }
        if (AbstractDungeon.isFadingOut || AbstractDungeon.isFadingIn) {
            return false;
        }
        AbstractRoom room = AbstractDungeon.getCurrRoom();
        // 事件房间：等事件自身动画
        if ((room instanceof EventRoom || room instanceof NeowRoom
                || (room instanceof VictoryRoom && ((VictoryRoom) room).eType == VictoryRoom.EventType.HEART))
                && room.event != null && room.event.waitTimer != 0.0f) {
            return false;
        }
        if (room.phase == AbstractRoom.RoomPhase.COMBAT) {
            GameActionManager am = AbstractDungeon.actionManager;
            if (am.phase != GameActionManager.Phase.WAITING_ON_USER) {
                return false;
            }
            if (!am.actions.isEmpty() || !am.preTurnActions.isEmpty()
                    || !am.cardQueue.isEmpty() || !am.monsterQueue.isEmpty() || am.currentAction != null) {
                return false;
            }
            if (AbstractDungeon.player != null && AbstractDungeon.player.isEndingTurn) {
                return false;
            }
            if (room.isBattleOver) {
                return false; // 战斗收尾计时/开奖励过渡中
            }
            if (room.monsters != null && room.monsters.areMonstersBasicallyDead()) {
                return false;
            }
            if (!AbstractDungeon.isScreenUp) {
                return true;
            }
        }
        // 遗物飞行/装备动画
        if (AbstractDungeon.player != null) {
            for (AbstractRelic r : AbstractDungeon.player.relics) {
                if (!r.isDone) {
                    return false;
                }
            }
        }
        // 非战斗且有屏：屏本身即决策点（GRID 过渡中的 confirmScreenUp 也算稳定态）
        return true;
    }
}
