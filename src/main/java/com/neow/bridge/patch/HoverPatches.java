package com.neow.bridge.patch;

import basemod.ReflectionHacks;
import com.evacipated.cardcrawl.modthespire.lib.LineFinder;
import com.evacipated.cardcrawl.modthespire.lib.Matcher;
import com.evacipated.cardcrawl.modthespire.lib.SpireInsertPatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePatch;
import com.evacipated.cardcrawl.modthespire.patcher.PatchingException;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.cards.CardGroup;
import com.megacrit.cardcrawl.core.CardCrawlGame;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.events.shrines.GremlinMatchGame;
import com.megacrit.cardcrawl.events.shrines.GremlinWheelGame;
import com.megacrit.cardcrawl.helpers.Hitbox;
import com.megacrit.cardcrawl.helpers.input.InputHelper;
import com.megacrit.cardcrawl.map.DungeonMap;
import com.megacrit.cardcrawl.map.MapRoomNode;
import com.megacrit.cardcrawl.relics.AbstractRelic;
import com.megacrit.cardcrawl.screens.CardRewardScreen;
import com.megacrit.cardcrawl.screens.select.GridCardSelectScreen;
import com.megacrit.cardcrawl.shop.Merchant;
import com.megacrit.cardcrawl.shop.ShopScreen;
import javassist.CannotCompileException;
import javassist.CtBehavior;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/**
 * 鼠标悬停/点击模拟补丁集。
 *
 * 游戏多数屏的点击逻辑内联在 update() 中（依赖 hitbox 的 hovered/clicked 状态），
 * 无法从外部直接调用；这些补丁在 update 流程内注入"程序化悬停"钩子，
 * 使 Actions 可以在不操纵真实鼠标的情况下完成与玩家点击完全等价的路径。
 *
 * 移植自 CommunicationMod（ForgottenArbiter，MIT License）并按 NeowBridge 需要裁剪。
 */
public final class HoverPatches {

    private HoverPatches() {
    }

    // ------------------------------------------------------------------
    // 卡牌奖励屏：hover + 点击指定卡
    // ------------------------------------------------------------------
    public static class CardRewardHover {
        public static boolean doHover = false;
        public static AbstractCard hoverCard;

        @SpirePatch(clz = CardRewardScreen.class, method = "cardSelectUpdate")
        public static class HoverCardPatch {
            @SpireInsertPatch(locator = Locator.class, localvars = {"c"})
            public static void Insert(CardRewardScreen _instance, AbstractCard c) {
                if (doHover) {
                    if (c.equals(hoverCard)) {
                        hoverCard.hb.hovered = true;
                    } else {
                        c.hb.hovered = false;
                    }
                }
            }

            private static class Locator extends SpireInsertLocator {
                public int[] Locate(CtBehavior ctMethodToPatch) throws CannotCompileException, PatchingException {
                    Matcher matcher = new Matcher.MethodCallMatcher(AbstractCard.class, "updateHoverLogic");
                    int[] match = LineFinder.findInOrder(ctMethodToPatch, new ArrayList<Matcher>(), matcher);
                    match[0] += 1;
                    return match;
                }
            }
        }

        @SpirePatch(clz = CardRewardScreen.class, method = "cardSelectUpdate")
        public static class AcquireCardPatch {
            @SpireInsertPatch(locator = Locator.class)
            public static void Insert(CardRewardScreen _instance) {
                doHover = false;
            }

            private static class Locator extends SpireInsertLocator {
                public int[] Locate(CtBehavior ctMethodToPatch) throws CannotCompileException, PatchingException {
                    Matcher matcher = new Matcher.FieldAccessMatcher(CardRewardScreen.class, "skipButton");
                    return LineFinder.findInOrder(ctMethodToPatch, new ArrayList<Matcher>(), matcher);
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // 网格选择屏：把 hoveredCard 替换为指定卡并触发点击
    // ------------------------------------------------------------------
    public static class GridHover {
        public static AbstractCard hoverCard;
        public static boolean replaceHoverCard = false;

        @SpirePatch(clz = GridCardSelectScreen.class, method = "updateCardPositionsAndHoverLogic")
        public static class ReplacePatch {
            public static void Postfix(GridCardSelectScreen _instance) {
                if (replaceHoverCard) {
                    ReflectionHacks.setPrivate(_instance, GridCardSelectScreen.class, "hoveredCard", hoverCard);
                    hoverCard.hb.hovered = true;
                    hoverCard.hb.clicked = true;
                    replaceHoverCard = false;
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // 商店：hover 指定卡（购买走 hb.clicked，由 ShopScreen.update 处理）
    // ------------------------------------------------------------------
    public static class ShopHover {
        public static boolean doHover = false;
        public static AbstractCard hoverCard;

        @SpirePatch(clz = ShopScreen.class, method = "update")
        public static class HoverCardPatch {
            @SuppressWarnings("unchecked")
            @SpireInsertPatch(locator = Locator.class)
            public static void Insert(ShopScreen _instance) {
                if (doHover) {
                    ArrayList<AbstractCard> coloredCards =
                            (ArrayList<AbstractCard>) ReflectionHacks.getPrivate(_instance, ShopScreen.class, "coloredCards");
                    ArrayList<AbstractCard> colorlessCards =
                            (ArrayList<AbstractCard>) ReflectionHacks.getPrivate(_instance, ShopScreen.class, "colorlessCards");
                    for (AbstractCard card : coloredCards) {
                        card.hb.hovered = card == hoverCard;
                    }
                    for (AbstractCard card : colorlessCards) {
                        card.hb.hovered = card == hoverCard;
                    }
                    doHover = false;
                }
            }

            private static class Locator extends SpireInsertLocator {
                public int[] Locate(CtBehavior ctMethodToPatch) throws CannotCompileException, PatchingException {
                    Matcher matcher = new Matcher.MethodCallMatcher(ShopScreen.class, "updateHand");
                    return LineFinder.findInOrder(ctMethodToPatch, new ArrayList<Matcher>(), matcher);
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // 地图：hover 指定节点 / Boss 门
    // ------------------------------------------------------------------
    public static class MapNodeHover {
        public static MapRoomNode hoverNode;
        public static boolean doHover = false;

        @SpirePatch(clz = MapRoomNode.class, method = "update")
        public static class HoverPatch {
            @SpireInsertPatch(locator = Locator.class)
            public static void Insert(MapRoomNode _instance) {
                if (doHover) {
                    _instance.hb.hovered = hoverNode == _instance;
                    if (hoverNode == _instance) {
                        doHover = false;
                    }
                }
            }

            private static class Locator extends SpireInsertLocator {
                public int[] Locate(CtBehavior ctMethodToPatch) throws CannotCompileException, PatchingException {
                    Matcher matcher = new Matcher.MethodCallMatcher(Hitbox.class, "update");
                    int[] results = LineFinder.findInOrder(ctMethodToPatch, new ArrayList<Matcher>(), matcher);
                    results[0] += 1;
                    return results;
                }
            }
        }
    }

    public static class DungeonMapBossHover {
        public static boolean doBossHover = false;

        @SpirePatch(clz = DungeonMap.class, method = "update")
        public static class BossPatch {
            @SpireInsertPatch(locator = Locator.class)
            public static void Insert(DungeonMap _instance) {
                if (doBossHover) {
                    _instance.bossHb.hovered = true;
                    InputHelper.justClickedLeft = true;
                    doBossHover = false;
                }
            }

            private static class Locator extends SpireInsertLocator {
                public int[] Locate(CtBehavior ctMethodToPatch) throws CannotCompileException, PatchingException {
                    Matcher matcher = new Matcher.MethodCallMatcher(Hitbox.class, "update");
                    int[] results = LineFinder.findInOrder(ctMethodToPatch, new ArrayList<Matcher>(), matcher);
                    results[0] += 1;
                    return results;
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // 商店房间：点击商人打开商店
    // ------------------------------------------------------------------
    public static class MerchantClick {
        public static boolean visitMerchant = false;

        @SpirePatch(clz = Merchant.class, method = "update")
        public static class VisitPatch {
            @SpireInsertPatch(locator = Locator.class)
            public static void Insert(Merchant _instance) {
                if (visitMerchant) {
                    _instance.hb.hovered = true;
                    InputHelper.justClickedLeft = true;
                    visitMerchant = false;
                }
            }

            private static class Locator extends SpireInsertLocator {
                public int[] Locate(CtBehavior ctMethodToPatch) throws CannotCompileException, PatchingException {
                    Matcher matcher = new Matcher.MethodCallMatcher(Hitbox.class, "update");
                    int[] results = LineFinder.findInOrder(ctMethodToPatch, new ArrayList<Matcher>(), matcher);
                    results[0] += 1;
                    return results;
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Boss 遗物三选一：hover + 点击指定遗物
    // ------------------------------------------------------------------
    public static class RelicHover {
        public static AbstractRelic hoverRelic;
        public static boolean doHover = false;

        @SpirePatch(clz = AbstractRelic.class, method = "update")
        public static class HoverPatch {
            @SpireInsertPatch(locator = Locator.class)
            public static void Insert(AbstractRelic _instance) {
                if (doHover) {
                    _instance.hb.hovered = hoverRelic == _instance;
                    if (hoverRelic == _instance) {
                        InputHelper.justClickedLeft = true;
                        doHover = false;
                    }
                }
            }

            private static class Locator extends SpireInsertLocator {
                public int[] Locate(CtBehavior ctMethodToPatch) throws CannotCompileException, PatchingException {
                    Matcher matcher = new Matcher.MethodCallMatcher(Hitbox.class, "update");
                    int[] results = LineFinder.findInOrder(ctMethodToPatch, new ArrayList<Matcher>(), matcher);
                    results[0] += 1;
                    return results;
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // 事件正文捕获（RoomEventDialog / GenericEventDialog 的 updateBodyText）
    // ------------------------------------------------------------------
    public static class EventBodyText {
        public static String bodyText = "";

        @SpirePatch(clz = com.megacrit.cardcrawl.events.RoomEventDialog.class, method = "updateBodyText",
                paramtypez = {String.class, com.megacrit.cardcrawl.events.DialogWord.AppearEffect.class})
        public static class RoomPatch {
            public static void Prefix(com.megacrit.cardcrawl.events.RoomEventDialog _instance, String text,
                                      com.megacrit.cardcrawl.events.DialogWord.AppearEffect ae) {
                bodyText = text;
            }
        }

        @SpirePatch(clz = com.megacrit.cardcrawl.events.GenericEventDialog.class, method = "updateBodyText",
                paramtypez = {String.class, com.megacrit.cardcrawl.events.DialogWord.AppearEffect.class})
        public static class ImagePatch {
            public static void Prefix(com.megacrit.cardcrawl.events.GenericEventDialog _instance, String text,
                                      com.megacrit.cardcrawl.events.DialogWord.AppearEffect ae) {
                bodyText = text;
            }
        }
    }

    // ------------------------------------------------------------------
    // 地精配对小游戏：记录卡牌初始位置，提供顺序化访问
    // ------------------------------------------------------------------
    public static class GremlinMatch {
        public static HashMap<UUID, Integer> cardPositions;
        public static CardGroup cards;
        public static Set<UUID> revealedCards = new HashSet<UUID>();

        public static ArrayList<AbstractCard> getOrderedCards() {
            ArrayList<AbstractCard> returnedCards = new ArrayList<AbstractCard>(cards.group);
            returnedCards.sort(Comparator.comparingInt(c -> cardPositions.get(c.uuid)));
            returnedCards.removeIf(c -> !c.isFlipped);
            return returnedCards;
        }

        @SpirePatch(clz = GremlinMatchGame.class, method = SpirePatch.CONSTRUCTOR)
        public static class InitPatch {
            public static void Postfix(GremlinMatchGame _instance) {
                cards = (CardGroup) ReflectionHacks.getPrivate(_instance, GremlinMatchGame.class, "cards");
                revealedCards = new HashSet<UUID>();
                cardPositions = new HashMap<UUID, Integer>();
                for (int i = 0; i < 12; ++i) {
                    AbstractCard currentCard = cards.group.get(i);
                    int target_x = i % 4;
                    int target_y = i % 3;
                    int position = target_x + 4 * target_y;
                    cardPositions.put(currentCard.uuid, position);
                }
            }
        }

        @SpirePatch(clz = GremlinMatchGame.class, method = "updateMatchGameLogic")
        public static class HoverPatch {
            public static AbstractCard hoverCard;
            public static boolean doHover = false;

            @SpireInsertPatch(locator = Locator.class, localvars = {"c"})
            public static void Insert(GremlinMatchGame _instance, AbstractCard c) {
                if (doHover) {
                    if (c.equals(hoverCard)) {
                        c.hb.hovered = true;
                        InputHelper.justClickedLeft = true;
                        doHover = false;
                    } else {
                        c.hb.hovered = false;
                    }
                }
            }

            private static class Locator extends SpireInsertLocator {
                public int[] Locate(CtBehavior ctMethodToPatch) throws CannotCompileException, PatchingException {
                    Matcher matcher = new Matcher.MethodCallMatcher(Hitbox.class, "update");
                    int[] result = LineFinder.findInOrder(ctMethodToPatch, new ArrayList<Matcher>(), matcher);
                    result[0] += 1;
                    return result;
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // 地精转盘：按钮按下
    // ------------------------------------------------------------------
    public static class GremlinWheel {
        @SuppressWarnings("unused")
        public static void spin() {
            GremlinWheelGame event = (GremlinWheelGame) AbstractDungeon.getCurrRoom().event;
            ReflectionHacks.setPrivate(event, GremlinWheelGame.class, "buttonPressed", true);
            CardCrawlGame.sound.play("WHEEL");
        }
    }
}
