package com.neow.bridge;

import basemod.ReflectionHacks;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.megacrit.cardcrawl.actions.utility.HandCheckAction;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.cards.CardGroup;
import com.megacrit.cardcrawl.cards.CardQueueItem;
import com.megacrit.cardcrawl.characters.AbstractPlayer;
import com.megacrit.cardcrawl.core.Settings;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.map.MapRoomNode;
import com.megacrit.cardcrawl.monsters.AbstractMonster;
import com.megacrit.cardcrawl.potions.AbstractPotion;
import com.megacrit.cardcrawl.potions.PotionSlot;
import com.megacrit.cardcrawl.relics.AbstractRelic;
import com.megacrit.cardcrawl.rewards.RewardItem;
import com.megacrit.cardcrawl.screens.select.GridCardSelectScreen;
import com.megacrit.cardcrawl.screens.select.HandCardSelectScreen;
import com.megacrit.cardcrawl.shop.ShopScreen;
import com.megacrit.cardcrawl.ui.buttons.LargeDialogOptionButton;
import com.megacrit.cardcrawl.ui.campfire.AbstractCampfireOption;
import com.neow.bridge.patch.HoverPatches;

import java.lang.reflect.Method;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * 动作执行器：协议动作 → 逐帧任务序列（游戏线程每帧消费一个任务）。
 *
 * 设计：所有对游戏状态的修改必须发生在游戏线程（libGDX 渲染循环），
 * 且部分屏的点击逻辑要求"点击 → 下一帧 update 消化"的节奏，
 * 因此每个动作被编译为 ArrayDeque&lt;Runnable&gt;，由 Bridge 每帧弹出一个执行。
 */
public final class Actions {

    private Actions() {
    }

    public static class InvalidActionException extends Exception {
        public InvalidActionException(String msg) {
            super(msg);
        }
    }

    /** 把协议动作编译为任务序列（在游戏线程调用一次；耗时忽略不计）。 */
    public static ArrayDeque<Runnable> compile(JsonObject action) throws InvalidActionException {
        String type = str(action, "type");
        if (type == null) {
            throw new InvalidActionException("action.type missing");
        }
        switch (type) {
            case "play": return play(action);
            case "end_turn": return endTurn();
            case "potion": return potion(action);
            case "potion_discard": return potionDiscard(action);
            case "relic": return relic(action);
            case "map": return mapMove(action);
            case "boss": return bossDoor();
            case "shop_enter": return shopEnter();
            case "shop_buy_card": return shopBuyCard(action);
            case "shop_buy_relic": return shopBuyRelic(action);
            case "shop_buy_potion": return shopBuyPotion(action);
            case "shop_card_removal": return shopCardRemoval();
            case "event": return event(action);
            case "rest": return rest(action);
            case "reward": return reward(action);
            case "card_pick": return cardPick(action);
            case "card_bowl": return cardBowl();
            case "skip": return skip();
            case "chest_open": return chestOpen();
            case "proceed": return proceed();
            case "cancel": return cancel();
            case "hand_select": return handSelect(action);
            case "grid_select": return gridSelect(action, false);
            case "scry": return gridSelect(action, true);
            default:
                throw new InvalidActionException("unknown action type: " + type);
        }
    }

    // ------------------------------------------------------------------
    // 战斗
    // ------------------------------------------------------------------

    private static ArrayDeque<Runnable> play(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.COMBAT);
        ArrayList<AbstractCard> hand = AbstractDungeon.player.hand.group;
        int ci;
        String wantUuid = str(a, "card_uuid");
        String wantId = str(a, "card_id");
        if (wantUuid != null || wantId != null) {
            // 优先 UUID/ID 定位（免疫出牌后手牌下标漂移）；index 仅作兜底
            ci = -1;
            for (int i = 0; i < hand.size(); i++) {
                AbstractCard c = hand.get(i);
                if (wantUuid != null && c.uuid.toString().equals(wantUuid)) { ci = i; break; }
                if (wantId != null && c.cardID.equals(wantId)) { ci = i; break; }
            }
            if (ci < 0) {
                throw new InvalidActionException("card not in hand: " + (wantUuid != null ? wantUuid : wantId));
            }
        } else if (a.has("card_index")) {
            ci = intOf(a, "card_index");
        } else {
            throw new InvalidActionException("play 需要 card_uuid / card_id / card_index 之一");
        }
        if (ci < 0 || ci >= hand.size()) {
            throw new InvalidActionException("card_index out of bounds: " + ci);
        }
        AbstractCard card = hand.get(ci);
        AbstractMonster target = null;
        boolean needTarget = card.target == AbstractCard.CardTarget.ENEMY
                || card.target == AbstractCard.CardTarget.SELF_AND_ENEMY;
        ArrayList<AbstractMonster> monsters = AbstractDungeon.getCurrRoom().monsters.monsters;
        if (a.has("target") && !a.get("target").isJsonNull()) {
            int mi = intOf(a, "target");
            if (mi < 0 || mi >= monsters.size()) {
                throw new InvalidActionException("target out of bounds: " + mi);
            }
            target = monsters.get(mi);
        }
        if (needTarget && target == null) {
            throw new InvalidActionException("card requires a target: " + card.cardID);
        }
        if (target != null && target.isDeadOrEscaped()) {
            throw new InvalidActionException("target is dead/escaped");
        }
        if (!card.canUse(AbstractDungeon.player, target)) {
            throw new InvalidActionException("card cannot be played now: " + card.cardID);
        }
        final AbstractCard c = card;
        final AbstractMonster m = target;
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> AbstractDungeon.actionManager.cardQueue.add(
                new CardQueueItem(c, m, com.megacrit.cardcrawl.ui.panels.EnergyPanel.totalCount)));
        return tasks;
    }

    private static ArrayDeque<Runnable> endTurn() throws InvalidActionException {
        requireScreen(Screens.COMBAT);
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> AbstractDungeon.overlayMenu.endTurnButton.disable(true));
        return tasks;
    }

    private static ArrayDeque<Runnable> potion(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.COMBAT);
        int pi = intOf(a, "potion_index");
        ArrayList<AbstractPotion> potions = AbstractDungeon.player.potions;
        if (pi < 0 || pi >= potions.size()) {
            throw new InvalidActionException("potion_index out of bounds: " + pi);
        }
        AbstractPotion potion = potions.get(pi);
        if (potion instanceof PotionSlot) {
            throw new InvalidActionException("empty potion slot: " + pi);
        }
        if (!potion.canUse()) {
            throw new InvalidActionException("potion cannot be used now: " + potion.ID);
        }
        AbstractMonster target = null;
        if (potion.targetRequired) {
            ArrayList<AbstractMonster> monsters = AbstractDungeon.getCurrRoom().monsters.monsters;
            int mi = intOf(a, "target");
            if (mi < 0 || mi >= monsters.size()) {
                throw new InvalidActionException("potion requires a target");
            }
            target = monsters.get(mi);
            if (target.isDeadOrEscaped()) {
                throw new InvalidActionException("target is dead/escaped");
            }
        }
        final AbstractPotion p = potion;
        final AbstractMonster m = target;
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            p.use(m);
            if (AbstractDungeon.getCurrRoom().phase == com.megacrit.cardcrawl.rooms.AbstractRoom.RoomPhase.COMBAT) {
                AbstractDungeon.actionManager.addToBottom(new HandCheckAction());
            }
            for (AbstractRelic r : AbstractDungeon.player.relics) {
                r.onUsePotion();
            }
            AbstractDungeon.topPanel.destroyPotion(p.slot);
        });
        return tasks;
    }

    private static ArrayDeque<Runnable> potionDiscard(JsonObject a) throws InvalidActionException {
        String scr = Screens.current();
        if (!Screens.COMBAT.equals(scr) && !Screens.COMBAT_REWARD.equals(scr)) {
            throw new InvalidActionException("potion_discard only legal in combat or on reward screen");
        }
        int pi = intOf(a, "potion_index");
        ArrayList<AbstractPotion> potions = AbstractDungeon.player.potions;
        if (pi < 0 || pi >= potions.size()) {
            throw new InvalidActionException("potion_index out of bounds: " + pi);
        }
        if (potions.get(pi) instanceof PotionSlot) {
            throw new InvalidActionException("empty potion slot: " + pi);
        }
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> AbstractDungeon.topPanel.destroyPotion(pi));
        return tasks;
    }

    private static ArrayDeque<Runnable> relic(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.BOSS_RELIC);
        int ri = intOf(a, "relic_index");
        ArrayList<AbstractRelic> relics = AbstractDungeon.bossRelicScreen.relics;
        if (ri < 0 || ri >= relics.size()) {
            throw new InvalidActionException("relic_index out of bounds: " + ri);
        }
        final AbstractRelic r = relics.get(ri);
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            HoverPatches.RelicHover.hoverRelic = r;
            HoverPatches.RelicHover.doHover = true;
        });
        return tasks;
    }

    // ------------------------------------------------------------------
    // 地图
    // ------------------------------------------------------------------

    private static ArrayDeque<Runnable> mapMove(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.MAP);
        int ni = intOf(a, "node_index");
        MapRoomNode node = null;
        for (MapRoomNode n : Screens.mapChoices()) {
            if (Legal.nodeFlatIndex(n) == ni) {
                node = n;
                break;
            }
        }
        if (node == null) {
            throw new InvalidActionException("node_index not reachable: " + ni);
        }
        final MapRoomNode target = node;
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            HoverPatches.MapNodeHover.hoverNode = target;
            HoverPatches.MapNodeHover.doHover = true;
            AbstractDungeon.dungeonMapScreen.clicked = true;
        });
        return tasks;
    }

    private static ArrayDeque<Runnable> bossDoor() throws InvalidActionException {
        requireScreen(Screens.MAP);
        if (!Screens.bossDoorAvailable()) {
            throw new InvalidActionException("boss door not available");
        }
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> HoverPatches.DungeonMapBossHover.doBossHover = true);
        return tasks;
    }

    // ------------------------------------------------------------------
    // 商店
    // ------------------------------------------------------------------

    private static ArrayDeque<Runnable> shopEnter() throws InvalidActionException {
        requireScreen(Screens.SHOP_ROOM);
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> HoverPatches.MerchantClick.visitMerchant = true);
        return tasks;
    }

    private static ArrayDeque<Runnable> shopBuyCard(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.SHOP);
        int i = intOf(a, "index");
        ArrayList<AbstractCard> cards = Legal.ObsScreen.shopCards();
        if (i < 0 || i >= cards.size()) {
            throw new InvalidActionException("shop card index out of bounds: " + i);
        }
        AbstractCard c = cards.get(i);
        if (c.price > AbstractDungeon.player.gold) {
            throw new InvalidActionException("not enough gold");
        }
        final AbstractCard card = c;
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            HoverPatches.ShopHover.hoverCard = card;
            HoverPatches.ShopHover.doHover = true;
            card.hb.clicked = true;
        });
        return tasks;
    }

    private static ArrayDeque<Runnable> shopBuyRelic(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.SHOP);
        int i = intOf(a, "index");
        ArrayList<com.megacrit.cardcrawl.shop.StoreRelic> relics = Legal.ObsScreen.shopRelics();
        if (i < 0 || i >= relics.size()) {
            throw new InvalidActionException("shop relic index out of bounds: " + i);
        }
        if (relics.get(i).price > AbstractDungeon.player.gold) {
            throw new InvalidActionException("not enough gold");
        }
        final com.megacrit.cardcrawl.shop.StoreRelic r = relics.get(i);
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> r.relic.hb.clicked = true);
        return tasks;
    }

    private static ArrayDeque<Runnable> shopBuyPotion(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.SHOP);
        int i = intOf(a, "index");
        ArrayList<com.megacrit.cardcrawl.shop.StorePotion> potions = Legal.ObsScreen.shopPotions();
        if (i < 0 || i >= potions.size()) {
            throw new InvalidActionException("shop potion index out of bounds: " + i);
        }
        if (potions.get(i).price > AbstractDungeon.player.gold) {
            throw new InvalidActionException("not enough gold");
        }
        final com.megacrit.cardcrawl.shop.StorePotion p = potions.get(i);
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> p.potion.hb.clicked = true);
        return tasks;
    }

    private static ArrayDeque<Runnable> shopCardRemoval() throws InvalidActionException {
        requireScreen(Screens.SHOP);
        ShopScreen s = AbstractDungeon.shopScreen;
        if (!s.purgeAvailable || AbstractDungeon.player.gold < ShopScreen.actualPurgeCost) {
            throw new InvalidActionException("card removal not available/affordable");
        }
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            AbstractDungeon.previousScreen = AbstractDungeon.CurrentScreen.SHOP;
            AbstractDungeon.gridSelectScreen.open(
                    CardGroup.getGroupWithoutBottledCards(AbstractDungeon.player.masterDeck.getPurgeableCards()),
                    1, ShopScreen.NAMES[13], false, false, true, true);
        });
        return tasks;
    }

    // ------------------------------------------------------------------
    // 事件 / 营火
    // ------------------------------------------------------------------

    private static ArrayDeque<Runnable> event(JsonObject a) throws InvalidActionException {
        String scr = Screens.current();
        if (!Screens.EVENT.equals(scr) && !Screens.GREMLIN_MATCH.equals(scr) && !Screens.GREMLIN_WHEEL.equals(scr)) {
            throw new InvalidActionException("event action not legal on screen: " + scr);
        }
        int oi = intOf(a, "option_index");
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        ArrayList<LargeDialogOptionButton> buttons = Obs.eventButtons();
        if (!buttons.isEmpty()) {
            int idx = -1;
            for (int i = 0; i < buttons.size(); i++) {
                if (!buttons.get(i).isDisabled) {
                    idx++;
                    if (idx == oi) {
                        final LargeDialogOptionButton b = buttons.get(i);
                        tasks.add(() -> b.pressed = true);
                        return tasks;
                    }
                }
            }
            throw new InvalidActionException("event option_index out of bounds: " + oi);
        }
        // 无对话框按钮的小游戏
        if (Screens.GREMLIN_WHEEL.equals(scr)) {
            if (oi != 0) {
                throw new InvalidActionException("gremlin wheel has a single option");
            }
            tasks.add(HoverPatches.GremlinWheel::spin);
            return tasks;
        }
        if (Screens.GREMLIN_MATCH.equals(scr) && HoverPatches.GremlinMatch.cards != null) {
            ArrayList<AbstractCard> pickable = HoverPatches.GremlinMatch.getOrderedCards();
            if (oi < 0 || oi >= pickable.size()) {
                throw new InvalidActionException("gremlin match option out of bounds: " + oi);
            }
            final AbstractCard c = pickable.get(oi);
            tasks.add(() -> {
                HoverPatches.GremlinMatch.HoverPatch.hoverCard = c;
                HoverPatches.GremlinMatch.HoverPatch.doHover = true;
            });
            return tasks;
        }
        throw new InvalidActionException("event has no selectable options");
    }

    private static ArrayDeque<Runnable> rest(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.REST);
        ArrayList<AbstractCampfireOption> buttons = Legal.ObsScreen.restButtons();
        String name = str(a, "option");
        Integer byIndex = null;
        if (a.has("option_index")) {
            byIndex = intOf(a, "option_index");
        }
        AbstractCampfireOption chosen = null;
        for (int i = 0; i < buttons.size(); i++) {
            AbstractCampfireOption b = buttons.get(i);
            if (!b.usable) {
                continue;
            }
            if (name != null && Legal.restOptionName(b).equals(name)) {
                chosen = b;
                break;
            }
            if (byIndex != null && byIndex == i) {
                chosen = b;
                break;
            }
        }
        if (chosen == null) {
            throw new InvalidActionException("rest option not available: " + name);
        }
        final AbstractCampfireOption b = chosen;
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            b.useOption();
            ((com.megacrit.cardcrawl.rooms.RestRoom) AbstractDungeon.getCurrRoom())
                    .campfireUI.somethingSelected = true;
        });
        return tasks;
    }

    // ------------------------------------------------------------------
    // 奖励
    // ------------------------------------------------------------------

    private static ArrayDeque<Runnable> reward(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.COMBAT_REWARD);
        boolean skip = a.has("skip") && a.get("skip").getAsBoolean();
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        if (skip) {
            tasks.add(AbstractDungeon::closeCurrentScreen);
            return tasks;
        }
        JsonArray takes = a.has("take_indices") ? a.getAsJsonArray("take_indices") : new JsonArray();
        if (takes.size() == 0) {
            throw new InvalidActionException("reward requires take_indices or skip");
        }
        ArrayList<RewardItem> rewards = AbstractDungeon.combatRewardScreen.rewards;
        int cardTakeIdx = -1;
        for (JsonElement e : takes) {
            int i = e.isJsonNull() ? -1 : e.getAsInt();
            if (i < 0 || i >= rewards.size()) {
                throw new InvalidActionException("reward take index out of bounds: " + i);
            }
            RewardItem r = rewards.get(i);
            if (r.isDone) {
                throw new InvalidActionException("reward already taken: " + i);
            }
            if (r.type == RewardItem.RewardType.POTION && AbstractDungeon.player.potionSlots > 0
                    && Legal.countPotions() >= AbstractDungeon.player.potionSlots
                    && !AbstractDungeon.player.hasRelic("Sozu")) {
                throw new InvalidActionException("potion slots full; potion_discard first");
            }
            if (r.type == RewardItem.RewardType.CARD) {
                if (cardTakeIdx >= 0) {
                    throw new InvalidActionException("一次 step 只能内联选一张卡（CARD 项多个时分开 step）");
                }
                cardTakeIdx = i;
            }
            final RewardItem item = r;
            tasks.add(() -> item.isDone = true);
        }
        // v0.4 内联：reward 带 card_pick 时一步完成 CARD 项——int=选第k张，"skip"=跳过该奖励
        if (a.has("card_pick")) {
            if (cardTakeIdx < 0) {
                throw new InvalidActionException("card_pick 需要 take_indices 中包含 CARD 项");
            }
            boolean isSkip = a.get("card_pick").isJsonPrimitive()
                    && a.get("card_pick").getAsJsonPrimitive().isString()
                    && "skip".equalsIgnoreCase(a.get("card_pick").getAsString());
            final int idx;
            if (!isSkip) {
                int pick = intOf(a, "card_pick");
                ArrayList<AbstractCard> pool = rewards.get(cardTakeIdx).cards;
                if (pick < 0 || pick >= pool.size()) {
                    throw new InvalidActionException("card_pick out of bounds: " + pick + " (共 " + pool.size() + " 张)");
                }
                idx = pick;
            } else {
                idx = -1;
            }
            // 任务逐帧执行：第1帧领奖开屏，第2帧起选卡/跳过（最多等12帧防过渡）
            for (int w = 0; w < 12; w++) {
                tasks.add(() -> {
                    if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.CARD_REWARD
                            && !AbstractDungeon.cardRewardScreen.rewardGroup.isEmpty()) {
                        if (idx >= 0) {
                            AbstractCard c = AbstractDungeon.cardRewardScreen.rewardGroup.get(idx);
                            HoverPatches.CardRewardHover.hoverCard = c;
                            HoverPatches.CardRewardHover.doHover = true;
                            c.hb.clicked = true;
                        } else {
                            Object skipBtn = ReflectionHacks.getPrivate(AbstractDungeon.cardRewardScreen,
                                    com.megacrit.cardcrawl.screens.CardRewardScreen.class, "skipButton");
                            ((com.megacrit.cardcrawl.ui.buttons.SkipCardButton) skipBtn).hb.clicked = true;
                        }
                    }
                });
            }
        }
        return tasks;
    }

    private static ArrayDeque<Runnable> cardPick(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.CARD_REWARD);
        int i = intOf(a, "index");
        ArrayList<AbstractCard> cards = AbstractDungeon.cardRewardScreen.rewardGroup;
        if (i < 0 || i >= cards.size()) {
            throw new InvalidActionException("card_pick index out of bounds: " + i);
        }
        final AbstractCard c = cards.get(i);
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            HoverPatches.CardRewardHover.hoverCard = c;
            HoverPatches.CardRewardHover.doHover = true;
            c.hb.clicked = true;
        });
        return tasks;
    }

    private static ArrayDeque<Runnable> cardBowl() throws InvalidActionException {
        requireScreen(Screens.CARD_REWARD);
        if (!Legal.ObsScreen.bowlAvailable()) {
            throw new InvalidActionException("singing bowl not available");
        }
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            Object bowl = ReflectionHacks.getPrivate(AbstractDungeon.cardRewardScreen,
                    com.megacrit.cardcrawl.screens.CardRewardScreen.class, "bowlButton");
            ((com.megacrit.cardcrawl.ui.buttons.SingingBowlButton) bowl).onClick();
            AbstractDungeon.cardRewardScreen.closeFromBowlButton();
            AbstractDungeon.closeCurrentScreen();
        });
        return tasks;
    }

    private static ArrayDeque<Runnable> skip() throws InvalidActionException {
        requireScreen(Screens.CARD_REWARD);
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            Object skipBtn = ReflectionHacks.getPrivate(AbstractDungeon.cardRewardScreen,
                    com.megacrit.cardcrawl.screens.CardRewardScreen.class, "skipButton");
            ((com.megacrit.cardcrawl.ui.buttons.SkipCardButton) skipBtn).hb.clicked = true;
        });
        return tasks;
    }

    // ------------------------------------------------------------------
    // 宝箱 / 推进 / 后退
    // ------------------------------------------------------------------

    private static ArrayDeque<Runnable> chestOpen() throws InvalidActionException {
        requireScreen(Screens.CHEST);
        com.megacrit.cardcrawl.rooms.AbstractRoom room = AbstractDungeon.getCurrRoom();
        if (Legal.ObsScreen.chestOpened()) {
            throw new InvalidActionException("chest already opened");
        }
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            com.megacrit.cardcrawl.rewards.chests.AbstractChest chest;
            if (room instanceof com.megacrit.cardcrawl.rooms.TreasureRoomBoss) {
                chest = ((com.megacrit.cardcrawl.rooms.TreasureRoomBoss) room).chest;
            } else {
                chest = ((com.megacrit.cardcrawl.rooms.TreasureRoom) room).chest;
            }
            chest.isOpen = true;
            chest.open(false);
        });
        return tasks;
    }

    private static ArrayDeque<Runnable> proceed() throws InvalidActionException {
        String scr = Screens.current();
        // ⚠️ EVENT 也要允许 proceed：事件内精英战打完后 phase=COMPLETE、dialog 清空，
        //    真游戏只剩「继续」按钮（Dead Adventurer / Mushrooms / Colosseum 等）。
        //    否则 Legal 提供了 proceed 却发不出去 → 驱动死循环。
        boolean eventDone = Screens.EVENT.equals(scr)
                && AbstractDungeon.getCurrRoom() != null
                && AbstractDungeon.getCurrRoom().phase
                    == com.megacrit.cardcrawl.rooms.AbstractRoom.RoomPhase.COMPLETE
                && AbstractDungeon.getCurrRoom().monsters != null
                && AbstractDungeon.getCurrRoom().monsters.areMonstersBasicallyDead()
                && Obs.eventButtons().isEmpty();
        if (!Screens.COMPLETE.equals(scr) && !Screens.GAME_OVER.equals(scr)
                && !Screens.SHOP_ROOM.equals(scr) && !Screens.CHEST.equals(scr)
                && !eventDone) {
            throw new InvalidActionException("proceed not legal on screen: " + scr);
        }
        if (Screens.CHEST.equals(scr) && Legal.ObsScreen.chestOpened()) {
            throw new InvalidActionException("chest already opened");
        }
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        if (Screens.GAME_OVER.equals(scr)) {
            tasks.add(() -> {
                AbstractDungeon.unlocks.clear();
                Settings.isTrial = false;
                Settings.isDailyRun = false;
                Settings.isEndless = false;
                com.megacrit.cardcrawl.core.CardCrawlGame.trial = null;
                com.megacrit.cardcrawl.core.CardCrawlGame.startOver();
            });
        } else {
            tasks.add(() -> {
                AbstractDungeon.overlayMenu.proceedButton.show();
                Object hb = ReflectionHacks.getPrivate(AbstractDungeon.overlayMenu.proceedButton,
                        com.megacrit.cardcrawl.ui.buttons.ProceedButton.class, "hb");
                ((com.megacrit.cardcrawl.helpers.Hitbox) hb).clicked = true;
            });
        }
        return tasks;
    }

    private static ArrayDeque<Runnable> cancel() throws InvalidActionException {
        String scr = Screens.current();
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        switch (scr) {
            case Screens.MAP:
                tasks.add(() -> AbstractDungeon.overlayMenu.cancelButton.hb.clicked = true);
                return tasks;
            case Screens.SHOP:
                tasks.add(() -> AbstractDungeon.overlayMenu.cancelButton.hb.clicked = true);
                return tasks;
            case Screens.GRID_SELECT:
                if (!Legal.ObsScreen.gridCancelable()) {
                    throw new InvalidActionException("grid cancel not available");
                }
                tasks.add(() -> AbstractDungeon.overlayMenu.cancelButton.hb.clicked = true);
                return tasks;
            case Screens.BOSS_RELIC:
                // 跳过 Boss 遗物交换（保留原遗物）
                tasks.add(() -> {
                    Object btn = ReflectionHacks.getPrivate(AbstractDungeon.bossRelicScreen,
                            com.megacrit.cardcrawl.screens.select.BossRelicSelectScreen.class, "cancelButton");
                    ((com.megacrit.cardcrawl.screens.mainMenu.MenuCancelButton) btn).hb.clicked = true;
                });
                return tasks;
            default:
                throw new InvalidActionException("cancel not legal on screen: " + scr);
        }
    }

    // ------------------------------------------------------------------
    // 选择屏
    // ------------------------------------------------------------------

    private static ArrayDeque<Runnable> handSelect(JsonObject a) throws InvalidActionException {
        requireScreen(Screens.HAND_SELECT);
        HandCardSelectScreen s = AbstractDungeon.handCardSelectScreen;
        boolean anyNumber = (Boolean) ReflectionHacks.getPrivate(s, HandCardSelectScreen.class, "anyNumber");
        ArrayList<AbstractCard> hand = AbstractDungeon.player.hand.group;
        Set<Integer> indices = readIndices(a.get("indices"), hand.size(), "hand_select");
        int min = (anyNumber || s.upTo || s.canPickZero) ? 0 : s.numCardsToSelect;
        int max = anyNumber ? hand.size() : s.numCardsToSelect;
        if (indices.size() < min || indices.size() > max) {
            throw new InvalidActionException("hand_select count " + indices.size()
                    + " not in [" + min + "," + max + "]");
        }
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        for (int i : indices) {
            final AbstractCard c = hand.get(i);
            tasks.add(() -> selectHandCard(s, c));
        }
        tasks.add(() -> {
            if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.HAND_SELECT) {
                s.button.hb.clicked = true;
            }
        });
        return tasks;
    }

    private static void selectHandCard(HandCardSelectScreen s, AbstractCard c) {
        s.hoveredCard = c;
        s.hoveredCard.setAngle(0.0f, false);
        try {
            Method m = HandCardSelectScreen.class.getDeclaredMethod("selectHoveredCard");
            m.setAccessible(true);
            m.invoke(s);
        } catch (Exception e) {
            throw new RuntimeException("selectHoveredCard invoke failed", e);
        }
    }

    private static ArrayDeque<Runnable> gridSelect(JsonObject a, boolean scry) throws InvalidActionException {
        requireScreen(Screens.GRID_SELECT);
        GridCardSelectScreen s = AbstractDungeon.gridSelectScreen;
        if (s.confirmScreenUp && !s.isJustForConfirming) {
            // 预览确认阶段：indices 必须为空，动作等价于点击确认
            Set<Integer> idx = readIndices(a.get("indices"), 0, "grid_select");
            if (!idx.isEmpty()) {
                throw new InvalidActionException("grid is in preview-confirm state; indices must be empty");
            }
            ArrayDeque<Runnable> confirm = new ArrayDeque<Runnable>();
            confirm.add(() -> s.confirmButton.hb.clicked = true);
            return confirm;
        }
        if (s.isJustForConfirming) {
            Set<Integer> idx = readIndices(a.get("indices"), 0, "grid_select");
            if (!idx.isEmpty()) {
                throw new InvalidActionException("grid is confirm-only; indices must be empty");
            }
            ArrayDeque<Runnable> confirm = new ArrayDeque<Runnable>();
            confirm.add(() -> s.confirmButton.hb.clicked = true);
            return confirm;
        }
        ArrayList<AbstractCard> cards = s.targetGroup != null ? s.targetGroup.group : new ArrayList<AbstractCard>();
        int numCards = (Integer) ReflectionHacks.getPrivate(s, GridCardSelectScreen.class, "numCards");
        JsonElement raw = scry ? a.get("discard_indices") : a.get("indices");
        Set<Integer> indices = readIndices(raw, cards.size(), "grid_select");
        if (scry && a.has("top_indices")) {
            Set<Integer> top = readIndices(a.get("top_indices"), cards.size(), "scry");
            for (int t : top) {
                if (indices.contains(t)) {
                    throw new InvalidActionException("scry indices overlap: " + t);
                }
            }
        }
        int min = s.anyNumber ? 0 : numCards;
        int max = s.anyNumber ? cards.size() : numCards;
        if (indices.size() < min || indices.size() > max) {
            throw new InvalidActionException("grid_select count " + indices.size()
                    + " not in [" + min + "," + max + "]");
        }
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        for (int i : indices) {
            final AbstractCard c = cards.get(i);
            tasks.add(() -> {
                HoverPatches.GridHover.hoverCard = c;
                HoverPatches.GridHover.replaceHoverCard = true;
            });
        }
        if (s.anyNumber || s.forClarity) {
            tasks.add(() -> {
                if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.GRID) {
                    s.confirmButton.hb.clicked = true;
                }
            });
        } else if (s.forUpgrade || s.forTransform || s.forPurge) {
            // 单卡预览流程：点卡后出现确认按钮，再点确认
            tasks.add(() -> {
                if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.GRID && s.confirmScreenUp) {
                    s.confirmButton.hb.clicked = true;
                }
            });
        }
        return tasks;
    }

    // ------------------------------------------------------------------
    // 工具
    // ------------------------------------------------------------------

    private static void requireScreen(String expected) throws InvalidActionException {
        String cur = Screens.current();
        if (!expected.equals(cur)) {
            throw new InvalidActionException("action not legal on screen " + cur + " (expected " + expected + ")");
        }
    }

    private static Set<Integer> readIndices(JsonElement e, int bound, String what) throws InvalidActionException {
        Set<Integer> out = new LinkedHashSet<Integer>();
        if (e == null || e.isJsonNull()) {
            return out;
        }
        if (!e.isJsonArray()) {
            throw new InvalidActionException(what + " indices must be an array");
        }
        for (JsonElement el : e.getAsJsonArray()) {
            if (!el.isJsonPrimitive() || !el.getAsJsonPrimitive().isNumber()) {
                throw new InvalidActionException(what + " indices must be integers");
            }
            int i = el.getAsInt();
            if (i < 0 || (bound >= 0 && i >= bound)) {
                throw new InvalidActionException(what + " index out of bounds: " + i + " (bound=" + bound + ")");
            }
            if (!out.add(i)) {
                throw new InvalidActionException(what + " duplicate index: " + i);
            }
        }
        return out;
    }

    static String str(JsonObject o, String key) {
        if (o.has(key) && !o.get(key).isJsonNull()) {
            return o.get(key).getAsString();
        }
        return null;
    }

    static int intOf(JsonObject o, String key) throws InvalidActionException {
        if (!o.has(key) || o.get(key).isJsonNull()) {
            throw new InvalidActionException(key + " missing");
        }
        try {
            return o.get(key).getAsInt();
        } catch (NumberFormatException e) {
            throw new InvalidActionException(key + " not an integer");
        }
    }
}
