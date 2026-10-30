package com.neow.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.monsters.AbstractMonster;
import com.megacrit.cardcrawl.potions.AbstractPotion;
import com.megacrit.cardcrawl.potions.PotionSlot;
import com.megacrit.cardcrawl.rewards.RewardItem;
import com.megacrit.cardcrawl.shop.ShopScreen;

import java.util.ArrayList;

/**
 * 合法动作枚举：当前决策面上的全部合法正常动作（docs/protocol.md v0.3）。
 * 选择屏（hand_select / grid_select / scry）为参数化描述符，具体组合在 Actions 内校验。
 */
public final class Legal {

    private Legal() {
    }

    static JsonObject act(String type) {
        JsonObject o = new JsonObject();
        o.addProperty("type", type);
        return o;
    }

    public static JsonArray legalActions() {
        JsonArray out = new JsonArray();
        String scr = Screens.current();
        switch (scr) {
            case Screens.COMBAT: {
                ArrayList<AbstractCard> hand = AbstractDungeon.player.hand.group;
                ArrayList<AbstractMonster> monsters = AbstractDungeon.getCurrRoom().monsters.monsters;
                for (int i = 0; i < hand.size(); i++) {
                    AbstractCard c = hand.get(i);
                    if (c.target == AbstractCard.CardTarget.ENEMY
                            || c.target == AbstractCard.CardTarget.SELF_AND_ENEMY) {
                        for (int m = 0; m < monsters.size(); m++) {
                            AbstractMonster mo = monsters.get(m);
                            if (mo.isDeadOrEscaped() || mo.halfDead && !mo.isDying) {
                                continue;
                            }
                            if (c.canUse(AbstractDungeon.player, mo)) {
                                JsonObject a = act("play");
                                a.addProperty("card_index", i);
                                a.addProperty("target", m);
                                out.add(a);
                            }
                        }
                    } else if (c.canUse(AbstractDungeon.player, null)) {
                        JsonObject a = act("play");
                        a.addProperty("card_index", i);
                        out.add(a);
                    }
                }
                ArrayList<AbstractPotion> potions = AbstractDungeon.player.potions;
                for (int i = 0; i < potions.size(); i++) {
                    AbstractPotion p = potions.get(i);
                    if (p instanceof PotionSlot || !p.canUse()) {
                        continue;
                    }
                    if (p.targetRequired) {
                        for (int m = 0; m < monsters.size(); m++) {
                            AbstractMonster mo = monsters.get(m);
                            if (mo.isDeadOrEscaped()) {
                                continue;
                            }
                            JsonObject a = act("potion");
                            a.addProperty("potion_index", i);
                            a.addProperty("target", m);
                            out.add(a);
                        }
                    } else {
                        JsonObject a = act("potion");
                        a.addProperty("potion_index", i);
                        out.add(a);
                    }
                }
                addPotionDiscards(out);
                out.add(act("end_turn"));
                break;
            }
            case Screens.HAND_SELECT:
                out.add(act("hand_select"));
                break;
            case Screens.GRID_SELECT: {
                JsonObject a = act("grid_select");
                a.addProperty("pile", ObsScreen.gridPile());
                out.add(a);
                if (ObsScreen.gridCancelable()) {
                    out.add(act("cancel"));
                }
                break;
            }
            case Screens.CARD_REWARD: {
                int n = AbstractDungeon.cardRewardScreen.rewardGroup.size();
                for (int i = 0; i < n; i++) {
                    JsonObject a = act("card_pick");
                    a.addProperty("index", i);
                    out.add(a);
                }
                if (ObsScreen.bowlAvailable()) {
                    out.add(act("card_bowl"));
                }
                out.add(act("skip"));
                break;
            }
            case Screens.COMBAT_REWARD: {
                int i = 0;
                boolean potionFull = AbstractDungeon.player.potionSlots > 0
                        && countPotions() >= AbstractDungeon.player.potionSlots;
                for (RewardItem r : AbstractDungeon.combatRewardScreen.rewards) {
                    boolean takeable = true;
                    if (r.type == RewardItem.RewardType.POTION && potionFull
                            && !AbstractDungeon.player.hasRelic("Sozu")) {
                        takeable = false; // 满槽：需先 potion_discard
                    }
                    if (takeable) {
                        JsonObject a = act("reward");
                        JsonArray idx = new JsonArray();
                        idx.add(i);
                        a.add("take_indices", idx);
                        out.add(a);
                    }
                    i++;
                }
                if (potionFull && hasPotions()) {
                    addPotionDiscards(out);
                }
                JsonObject skip = act("reward");
                skip.addProperty("skip", true);
                out.add(skip);
                break;
            }
            case Screens.MAP: {
                for (com.megacrit.cardcrawl.map.MapRoomNode n : Screens.mapChoices()) {
                    JsonObject a = act("map");
                    a.addProperty("node_index", nodeFlatIndex(n));
                    out.add(a);
                }
                if (Screens.bossDoorAvailable()) {
                    out.add(act("boss"));
                }
                if (AbstractDungeon.dungeonMapScreen.dismissable) {
                    out.add(act("cancel"));
                }
                break;
            }
            case Screens.SHOP: {
                ShopScreen s = AbstractDungeon.shopScreen;
                ArrayList<AbstractCard> cs = ObsScreen.shopCards();
                int i = 0;
                for (AbstractCard c : cs) {
                    if (c.price <= AbstractDungeon.player.gold && !ObsScreen.cardSold(c)) {
                        JsonObject a = act("shop_buy_card");
                        a.addProperty("index", i);
                        out.add(a);
                    }
                    i++;
                }
                ArrayList<com.megacrit.cardcrawl.shop.StoreRelic> relics = ObsScreen.shopRelics();
                i = 0;
                for (com.megacrit.cardcrawl.shop.StoreRelic r : relics) {
                    if (r.price <= AbstractDungeon.player.gold) {
                        JsonObject a = act("shop_buy_relic");
                        a.addProperty("index", i);
                        out.add(a);
                    }
                    i++;
                }
                ArrayList<com.megacrit.cardcrawl.shop.StorePotion> potions = ObsScreen.shopPotions();
                i = 0;
                for (com.megacrit.cardcrawl.shop.StorePotion p : potions) {
                    if (p.price <= AbstractDungeon.player.gold) {
                        JsonObject a = act("shop_buy_potion");
                        a.addProperty("index", i);
                        out.add(a);
                    }
                    i++;
                }
                if (s.purgeAvailable && AbstractDungeon.player.gold >= ShopScreen.actualPurgeCost) {
                    out.add(act("shop_card_removal"));
                }
                out.add(act("cancel"));
                break;
            }
            case Screens.SHOP_ROOM:
                out.add(act("shop_enter"));
                out.add(act("proceed")); // 不进店直接走（游戏内同路径：商店房的前进按钮）
                break;
            case Screens.EVENT:
            case Screens.GREMLIN_MATCH:
            case Screens.GREMLIN_WHEEL: {
                int i = 0;
                for (com.megacrit.cardcrawl.ui.buttons.LargeDialogOptionButton b : ObsScreen.eventButtons()) {
                    if (!b.isDisabled) {
                        JsonObject a = act("event");
                        a.addProperty("option_index", i);
                        out.add(a);
                    }
                    i++;
                }
                if (ObsScreen.noDialogButtons()) {
                    // GremlinMatch: 每张未翻开的卡一个选项；GremlinWheel: spin
                    if (Screens.GREMLIN_MATCH.equals(scr) && com.neow.bridge.patch.HoverPatches.GremlinMatch.cards != null) {
                        int n = com.neow.bridge.patch.HoverPatches.GremlinMatch.getOrderedCards().size();
                        for (int k = 0; k < n; k++) {
                            JsonObject a = act("event");
                            a.addProperty("option_index", k);
                            out.add(a);
                        }
                    } else if (Screens.GREMLIN_WHEEL.equals(scr)) {
                        JsonObject a = act("event");
                        a.addProperty("option_index", 0);
                        out.add(a);
                    }
                }
                break;
            }
            case Screens.REST: {
                com.megacrit.cardcrawl.rooms.RestRoom restRoom =
                        (com.megacrit.cardcrawl.rooms.RestRoom) AbstractDungeon.getCurrRoom();
                if (restRoom.campfireUI == null || restRoom.campfireUI.somethingSelected) {
                    break; // 演出中：不提供任何休息选项
                }
                int i = 0;
                for (com.megacrit.cardcrawl.ui.campfire.AbstractCampfireOption b : ObsScreen.restButtons()) {
                    if (b.usable) {
                        JsonObject a = act("rest");
                        a.addProperty("option", restOptionName(b));
                        a.addProperty("option_index", i);
                        out.add(a);
                    }
                    i++;
                }
                break;
            }
            case Screens.CHEST:
                if (!ObsScreen.chestOpened()) {
                    out.add(act("chest_open"));
                }
                break;
            case Screens.BOSS_RELIC: {
                int i = 0;
                for (com.megacrit.cardcrawl.relics.AbstractRelic ignored
                        : AbstractDungeon.bossRelicScreen.relics) {
                    JsonObject a = act("relic");
                    a.addProperty("relic_index", i);
                    out.add(a);
                    i++;
                }
                break;
            }
            case Screens.COMPLETE:
                out.add(act("proceed"));
                break;
            case Screens.GAME_OVER:
                out.add(act("proceed"));
                break;
            default:
                break;
        }
        return out;
    }

    static void addPotionDiscards(JsonArray out) {
        ArrayList<AbstractPotion> potions = AbstractDungeon.player.potions;
        for (int i = 0; i < potions.size(); i++) {
            AbstractPotion p = potions.get(i);
            if (p instanceof PotionSlot) {
                continue;
            }
            JsonObject a = act("potion_discard");
            a.addProperty("potion_index", i);
            out.add(a);
        }
    }

    static int countPotions() {
        int n = 0;
        for (AbstractPotion p : AbstractDungeon.player.potions) {
            if (!(p instanceof PotionSlot)) {
                n++;
            }
        }
        return n;
    }

    static boolean hasPotions() {
        return countPotions() > 0;
    }

    /** 与 Obs.map() 相同的平铺下标。 */
    static int nodeFlatIndex(com.megacrit.cardcrawl.map.MapRoomNode node) {
        int idx = 0;
        for (ArrayList<com.megacrit.cardcrawl.map.MapRoomNode> row : AbstractDungeon.map) {
            for (com.megacrit.cardcrawl.map.MapRoomNode n : row) {
                if (n == node) {
                    return idx;
                }
                idx++;
            }
        }
        return -1;
    }

    /** 协议动作值（docs/protocol.md）：RestOption→sleep 等类名映射。 */
    static String restOptionName(com.megacrit.cardcrawl.ui.campfire.AbstractCampfireOption b) {
        String cls = b.getClass().getSimpleName();
        if (cls.equals("RestOption")) return "sleep";
        return (cls.endsWith("Option") ? cls.substring(0, cls.length() - "Option".length()) : cls).toLowerCase();
    }

    /** 选择屏/事件屏的只读视图，供 Legal 与 Actions 共用。 */
    static final class ObsScreen {
        private ObsScreen() {
        }

        static String gridPile() {
            com.megacrit.cardcrawl.screens.select.GridCardSelectScreen s = AbstractDungeon.gridSelectScreen;
            if (s.targetGroup == null || s.targetGroup.type == null) {
                return "mixed";
            }
            switch (s.targetGroup.type) {
                case DRAW_PILE: return "draw";
                case DISCARD_PILE: return "discard";
                case EXHAUST_PILE: return "exhaust";
                case HAND: return "hand";
                case MASTER_DECK: return "deck";
                default: return "mixed";
            }
        }

        static boolean gridCancelable() {
            com.megacrit.cardcrawl.screens.select.GridCardSelectScreen s = AbstractDungeon.gridSelectScreen;
            boolean canCancel = (Boolean) basemod.ReflectionHacks.getPrivate(s,
                    com.megacrit.cardcrawl.screens.select.GridCardSelectScreen.class, "canCancel");
            return canCancel && (s.forPurge || s.forTransform || s.forUpgrade
                    || AbstractDungeon.previousScreen == AbstractDungeon.CurrentScreen.SHOP)
                    || s.confirmScreenUp;
        }

        static boolean bowlAvailable() {
            Object bowl = basemod.ReflectionHacks.getPrivate(AbstractDungeon.cardRewardScreen,
                    com.megacrit.cardcrawl.screens.CardRewardScreen.class, "bowlButton");
            return !((Boolean) basemod.ReflectionHacks.getPrivate(bowl,
                    com.megacrit.cardcrawl.ui.buttons.SingingBowlButton.class, "isHidden"));
        }

        static ArrayList<AbstractCard> shopCards() {
            ShopScreen s = AbstractDungeon.shopScreen;
            ArrayList<AbstractCard> cs = new ArrayList<AbstractCard>();
            cs.addAll((ArrayList<AbstractCard>) basemod.ReflectionHacks.getPrivate(s, ShopScreen.class, "coloredCards"));
            cs.addAll((ArrayList<AbstractCard>) basemod.ReflectionHacks.getPrivate(s, ShopScreen.class, "colorlessCards"));
            return cs;
        }

        static boolean cardSold(AbstractCard c) {
            return c.price == -1; // 售出卡片价格置 -1
        }

        @SuppressWarnings("unchecked")
        static ArrayList<com.megacrit.cardcrawl.shop.StoreRelic> shopRelics() {
            return (ArrayList<com.megacrit.cardcrawl.shop.StoreRelic>)
                    basemod.ReflectionHacks.getPrivate(AbstractDungeon.shopScreen, ShopScreen.class, "relics");
        }

        @SuppressWarnings("unchecked")
        static ArrayList<com.megacrit.cardcrawl.shop.StorePotion> shopPotions() {
            return (ArrayList<com.megacrit.cardcrawl.shop.StorePotion>)
                    basemod.ReflectionHacks.getPrivate(AbstractDungeon.shopScreen, ShopScreen.class, "potions");
        }

        static ArrayList<com.megacrit.cardcrawl.ui.buttons.LargeDialogOptionButton> eventButtons() {
            return Obs.eventButtons();
        }

        static boolean noDialogButtons() {
            return eventButtons().isEmpty();
        }

        @SuppressWarnings("unchecked")
        static ArrayList<com.megacrit.cardcrawl.ui.campfire.AbstractCampfireOption> restButtons() {
            com.megacrit.cardcrawl.rooms.RestRoom room =
                    (com.megacrit.cardcrawl.rooms.RestRoom) AbstractDungeon.getCurrRoom();
            return (ArrayList<com.megacrit.cardcrawl.ui.campfire.AbstractCampfireOption>)
                    basemod.ReflectionHacks.getPrivate(room.campfireUI,
                            com.megacrit.cardcrawl.rooms.CampfireUI.class, "buttons");
        }

        static boolean chestOpened() {
            com.megacrit.cardcrawl.rooms.AbstractRoom room = AbstractDungeon.getCurrRoom();
            if (room instanceof com.megacrit.cardcrawl.rooms.TreasureRoomBoss) {
                return ((com.megacrit.cardcrawl.rooms.TreasureRoomBoss) room).chest.isOpen;
            }
            if (room instanceof com.megacrit.cardcrawl.rooms.TreasureRoom) {
                return ((com.megacrit.cardcrawl.rooms.TreasureRoom) room).chest.isOpen;
            }
            return true;
        }
    }
}
