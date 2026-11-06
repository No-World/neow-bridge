package com.neow.bridge;

import basemod.ReflectionHacks;
import com.megacrit.cardcrawl.core.CardCrawlGame;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.dungeons.TheEnding;
import com.megacrit.cardcrawl.events.AbstractImageEvent;
import com.megacrit.cardcrawl.events.shrines.GremlinMatchGame;
import com.megacrit.cardcrawl.events.shrines.GremlinWheelGame;
import com.megacrit.cardcrawl.map.MapRoomNode;
import com.megacrit.cardcrawl.rooms.AbstractRoom;
import com.megacrit.cardcrawl.rooms.RestRoom;
import com.megacrit.cardcrawl.rooms.ShopRoom;
import com.megacrit.cardcrawl.rooms.TreasureRoom;
import com.megacrit.cardcrawl.rooms.TreasureRoomBoss;
import com.megacrit.cardcrawl.rooms.VictoryRoom;

import java.util.ArrayList;

/**
 * 当前决策面识别：把游戏状态（CurrentScreen / RoomPhase / 房间类型 / 事件）
 * 归一化为协议 v0.3 的 screen.type，并回答相关谓词。
 */
public final class Screens {

    public static final String MENU = "menu";
    public static final String COMBAT = "combat";
    public static final String HAND_SELECT = "hand_select";
    public static final String GRID_SELECT = "grid_select";
    public static final String CARD_REWARD = "card_reward";
    public static final String COMBAT_REWARD = "combat_reward";
    public static final String MAP = "map";
    public static final String SHOP = "shop";
    public static final String SHOP_ROOM = "shop_room";
    public static final String EVENT = "event";
    public static final String REST = "rest";
    public static final String CHEST = "chest";
    public static final String BOSS_RELIC = "boss_relic";
    public static final String GAME_OVER = "game_over";
    public static final String COMPLETE = "complete";
    public static final String GREMLIN_MATCH = "gremlin_match";
    public static final String GREMLIN_WHEEL = "gremlin_wheel";
    public static final String TRANSIT = "transit"; // 稳定判定用：尚在过渡，不应出现于最终响应

    private Screens() {
    }

    public static boolean inDungeon() {
        return CardCrawlGame.mode == CardCrawlGame.GameMode.GAMEPLAY
                && AbstractDungeon.isPlayerInDungeon()
                && AbstractDungeon.currMapNode != null;
    }

    /** 地图上是否已到 Boss 行（下一步是 Boss 门）。 */
    public static boolean bossDoorAvailable() {
        MapRoomNode curr = AbstractDungeon.getCurrMapNode();
        return curr != null && (curr.y == 14
                || (TheEnding.ID.equals(AbstractDungeon.id) && curr.y == 2));
    }

    public static String current() {
        try {
            return currentUnsafe();
        } catch (Throwable t) {
            return TRANSIT; // 幕切换等半初始化状态
        }
    }

    private static String currentUnsafe() {
        if (!inDungeon()) {
            return MENU;
        }
        AbstractRoom room = AbstractDungeon.getCurrRoom();
        if (AbstractDungeon.isScreenUp) {
            switch (AbstractDungeon.screen) {
                case GRID:
                    return GRID_SELECT;
                case HAND_SELECT:
                    return HAND_SELECT;
                case CARD_REWARD:
                    return CARD_REWARD;
                case COMBAT_REWARD:
                    return COMBAT_REWARD;
                case MAP:
                    return MAP;
                case SHOP:
                    return SHOP;
                case BOSS_REWARD:
                    return BOSS_RELIC;
                case DEATH:
                case VICTORY:
                case UNLOCK:
                case NEOW_UNLOCK:
                    return GAME_OVER;
                default:
                    return TRANSIT;
            }
        }
        // 无屏：按房间/阶段判定
        if (room.phase == AbstractRoom.RoomPhase.EVENT
                || (room.event != null && room.phase == AbstractRoom.RoomPhase.COMPLETE)) {
            if (room.event instanceof GremlinMatchGame) {
                return GREMLIN_MATCH;
            }
            if (room.event instanceof GremlinWheelGame) {
                return GREMLIN_WHEEL;
            }
            return EVENT;
        }
        if (room instanceof TreasureRoomBoss || room instanceof TreasureRoom) {
            // 宝箱房进门 phase 即 COMPLETE（同商店房），判据应是箱子开没开：
            // 未开=CHEST（可 chest_open），已开=COMPLETE（可 proceed）
            boolean opened;
            if (room instanceof TreasureRoomBoss) {
                opened = ((TreasureRoomBoss) room).chest.isOpen;
            } else {
                opened = ((TreasureRoom) room).chest.isOpen;
            }
            return opened ? COMPLETE : CHEST;
        }
        if (room instanceof ShopRoom) {
            return SHOP_ROOM; // 商店房进门即 COMPLETE：不按 phase 判，恒为 SHOP_ROOM（进店/离开并存）
        }
        if (room instanceof RestRoom) {
            return room.phase == AbstractRoom.RoomPhase.COMPLETE ? COMPLETE : REST;
        }
        if (room instanceof VictoryRoom) {
            return GAME_OVER; // 击杀心脏后的结算归入 game_over
        }
        if (room.phase == AbstractRoom.RoomPhase.COMBAT) {
            return COMBAT;
        }
        if (room.phase == AbstractRoom.RoomPhase.COMPLETE) {
            boolean eventHasFocus = room.event != null
                    && (room.event instanceof AbstractImageEvent || room.event.hasFocus);
            return eventHasFocus ? EVENT : COMPLETE;
        }
        return TRANSIT;
    }

    /** 事件对话框是否为图片型（GenericEventDialog）而非房间型。 */
    /** 供 Recorder 复用的 info 片段（screen/act/floor）。 */
    public static com.google.gson.JsonObject info() {
        com.google.gson.JsonObject info = new com.google.gson.JsonObject();
        info.addProperty("screen", current());
        info.addProperty("in_dungeon", inDungeon());
        if (inDungeon()) {
            info.addProperty("act", com.megacrit.cardcrawl.dungeons.AbstractDungeon.actNum);
            info.addProperty("floor", com.megacrit.cardcrawl.dungeons.AbstractDungeon.floorNum);
        }
        return info;
    }

    public static boolean imageEventDialogShown() {
        return (Boolean) ReflectionHacks.getPrivateStatic(
                com.megacrit.cardcrawl.events.GenericEventDialog.class, "show");
    }

    /** 地图可选节点：与游戏内点击路径同一判定（普通边 + 翅边）。 */
    public static ArrayList<MapRoomNode> mapChoices() {
        ArrayList<MapRoomNode> choices = new ArrayList<MapRoomNode>();
        MapRoomNode curr = AbstractDungeon.getCurrMapNode();
        ArrayList<ArrayList<MapRoomNode>> map = AbstractDungeon.map;
        if (!AbstractDungeon.firstRoomChosen) {
            for (MapRoomNode node : map.get(0)) {
                if (node.hasEdges()) {
                    choices.add(node);
                }
            }
        } else {
            for (ArrayList<MapRoomNode> rows : map) {
                for (MapRoomNode node : rows) {
                    if (!node.hasEdges()) {
                        continue;
                    }
                    if (curr.isConnectedTo(node) || curr.wingedIsConnectedTo(node)) {
                        choices.add(node);
                    }
                }
            }
        }
        return choices;
    }
}
