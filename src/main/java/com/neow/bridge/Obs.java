package com.neow.bridge;

import basemod.ReflectionHacks;
import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import com.megacrit.cardcrawl.actions.GameActionManager;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.cards.CardGroup;
import com.megacrit.cardcrawl.characters.AbstractPlayer;
import com.megacrit.cardcrawl.core.Settings;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.events.AbstractEvent;
import com.megacrit.cardcrawl.helpers.SeedHelper;
import com.megacrit.cardcrawl.map.MapEdge;
import com.megacrit.cardcrawl.map.MapRoomNode;
import com.megacrit.cardcrawl.monsters.AbstractMonster;
import com.megacrit.cardcrawl.monsters.EnemyMoveInfo;
import com.megacrit.cardcrawl.orbs.AbstractOrb;
import com.megacrit.cardcrawl.potions.AbstractPotion;
import com.megacrit.cardcrawl.potions.PotionSlot;
import com.megacrit.cardcrawl.powers.AbstractPower;
import com.megacrit.cardcrawl.relics.AbstractRelic;
import com.megacrit.cardcrawl.relics.RunicDome;
import com.megacrit.cardcrawl.rewards.RewardItem;
import com.megacrit.cardcrawl.rooms.AbstractRoom;
import com.megacrit.cardcrawl.rooms.RestRoom;
import com.megacrit.cardcrawl.rooms.TreasureRoom;
import com.megacrit.cardcrawl.rooms.TreasureRoomBoss;
import com.megacrit.cardcrawl.rooms.VictoryRoom;
import com.megacrit.cardcrawl.screens.GameOverScreen;
import com.megacrit.cardcrawl.shop.ShopScreen;
import com.megacrit.cardcrawl.shop.StorePotion;
import com.megacrit.cardcrawl.shop.StoreRelic;
import com.megacrit.cardcrawl.ui.buttons.LargeDialogOptionButton;
import com.megacrit.cardcrawl.ui.campfire.AbstractCampfireOption;
import com.neow.bridge.patch.HoverPatches;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 观测构建器：游戏状态 → docs/protocol.md v0.3 的 observation JSON。
 *
 * 隐藏信息规则：抽牌堆只暴露集合（默认按 id+升级数排序；cheat_draw_order 仅供对拍研究）。
 */
public final class Obs {

    private Obs() {
    }

    /** 事件选项结构化效果表（resources/event_effects.json，与 sim 数据同源共建）。 */
    /** 本回合最后打出的牌类型名（无记录返回空串）。 */
    private static String lastCardTypeName() {
        java.util.ArrayList<com.megacrit.cardcrawl.cards.AbstractCard> played =
                com.megacrit.cardcrawl.dungeons.AbstractDungeon.actionManager.cardsPlayedThisTurn;
        if (played == null || played.isEmpty()) return "";
        com.megacrit.cardcrawl.cards.AbstractCard last = played.get(played.size() - 1);
        return last.type != null ? last.type.name() : "";
    }

    private static com.google.gson.JsonObject EVENT_EFFECTS = null;
    private static synchronized com.google.gson.JsonObject eventEffects() {
        if (EVENT_EFFECTS == null) {
            try {
                java.io.InputStream in = Obs.class.getResourceAsStream("/event_effects.json");
                java.util.Scanner sc = new java.util.Scanner(in, "UTF-8").useDelimiter("\\A");
                String txt = sc.hasNext() ? sc.next() : "{}";
                EVENT_EFFECTS = new com.google.gson.JsonParser().parse(txt).getAsJsonObject();
            } catch (Throwable t) {
                EVENT_EFFECTS = new com.google.gson.JsonObject();
            }
        }
        return EVENT_EFFECTS;
    }

    // ------------------------------------------------------------------
    // 卡牌
    // ------------------------------------------------------------------

    public static JsonObject card(AbstractCard c) {
        JsonObject o = new JsonObject();
        o.addProperty("id", c.cardID);
        o.addProperty("name", c.name);
        o.addProperty("upgraded", c.timesUpgraded);
        o.addProperty("cost", c.cost);
        o.addProperty("cost_for_turn", c.costForTurn);
        o.addProperty("type", c.type.name());
        o.addProperty("target_type", c.target.name().toLowerCase());
        o.addProperty("rarity", c.rarity.name());
        o.addProperty("color", c.color.name());
        o.addProperty("damage", c.damage >= 0 ? c.damage : c.baseDamage);
        o.addProperty("block", c.block >= 0 ? c.block : c.baseBlock);
        o.addProperty("magic_number", c.magicNumber);
        o.addProperty("exhaust", c.exhaust);
        o.addProperty("ethereal", c.isEthereal);
        o.addProperty("innate", c.isInnate);
        o.addProperty("can_upgrade", c.canUpgrade());
        if (c.misc != 0) {
            o.addProperty("misc", c.misc);
        }
        o.addProperty("uuid", c.uuid.toString());
        if (Screens.inDungeon() && AbstractDungeon.getCurrRoom() != null
                && AbstractDungeon.getCurrRoom().phase == AbstractRoom.RoomPhase.COMBAT) {
            o.addProperty("playable", c.canUse(AbstractDungeon.player, null));
        }
        return o;
    }

    static JsonArray cards(List<AbstractCard> list) {
        JsonArray a = new JsonArray();
        for (AbstractCard c : list) {
            a.add(card(c));
        }
        return a;
    }

    static List<AbstractCard> sortedCopy(List<AbstractCard> list, boolean keepOrder) {
        if (keepOrder) {
            return list;
        }
        ArrayList<AbstractCard> copy = new ArrayList<AbstractCard>(list);
        copy.sort(Comparator
                .comparing((AbstractCard c) -> c.cardID)
                .thenComparingInt(c -> c.timesUpgraded)
                .thenComparing(c -> c.uuid.toString()));
        return copy;
    }

    // ------------------------------------------------------------------
    // 卡牌堆
    // ------------------------------------------------------------------

    static JsonObject pileSorted(com.megacrit.cardcrawl.cards.CardGroup group, boolean keepOrder) {
        JsonObject o = new JsonObject();
        o.addProperty("count", group.size());
        o.add("cards", cards(sortedCopy(group.group, keepOrder)));
        o.addProperty("order", keepOrder ? "true" : "hidden"); // true=真实顺序（仅对拍研究）
        return o;
    }

    /** 可见顺序的牌堆（弃牌/消耗）：index 0 = 堆底（最先进入），末尾 = 堆顶（最新）。 */
    static JsonObject pileChrono(com.megacrit.cardcrawl.cards.CardGroup group) {
        JsonObject o = new JsonObject();
        o.addProperty("count", group.size());
        o.add("cards", cards(group.group)); // 原样输出，不脱序
        o.addProperty("order", "chrono");
        return o;
    }

    /** 无序集合（牌组）：按 id+升级数排序输出。 */
    static JsonObject pileSortedDeck(com.megacrit.cardcrawl.cards.CardGroup group) {
        JsonObject o = new JsonObject();
        o.addProperty("count", group.size());
        o.add("cards", cards(sortedCopy(group.group, false)));
        o.addProperty("order", "sorted");
        return o;
    }

    // ------------------------------------------------------------------
    // 能力（powers）
    // ------------------------------------------------------------------

    static JsonArray powers(List<AbstractPower> list) {
        JsonArray a = new JsonArray();
        for (AbstractPower p : list) {
            JsonObject o = new JsonObject();
            o.addProperty("id", p.ID);
            o.addProperty("name", p.name);
            o.addProperty("amount", p.amount);
            o.addProperty("type", p.type != null ? p.type.name().toLowerCase() : null);
            a.add(o);
        }
        return a;
    }

    // ------------------------------------------------------------------
    // 怪物
    // ------------------------------------------------------------------

    static JsonObject monster(AbstractMonster m, int index) {
        JsonObject o = new JsonObject();
        o.addProperty("index", index);
        o.addProperty("id", m.id);
        o.addProperty("name", m.name);
        o.addProperty("hp", m.currentHealth);
        o.addProperty("max_hp", m.maxHealth);
        o.addProperty("block", m.currentBlock);
        o.add("powers", powers(m.powers));
        if (Screens.inDungeon() && AbstractDungeon.player != null
                && AbstractDungeon.player.hasRelic(RunicDome.ID)) {
            JsonObject intent = new JsonObject();
            intent.addProperty("type", "hidden");
            o.add("intent", intent);
        } else {
            JsonObject intent = new JsonObject();
            intent.addProperty("type", m.intent.name());
            EnemyMoveInfo move = (EnemyMoveInfo) ReflectionHacks.getPrivate(m, AbstractMonster.class, "move");
            if (move != null && move.baseDamage > 0) {
                intent.addProperty("damage", m.getIntentDmg());
                int hits = move.isMultiDamage ? move.multiplier : 1;
                intent.addProperty("count", hits);
            } else if (move != null) {
                intent.addProperty("count", move.isMultiDamage ? move.multiplier : 1);
            }
            o.add("intent", intent);
            if (move != null) {
                o.addProperty("move_id", move.nextMove);
                o.addProperty("move_base_damage", move.baseDamage);
            }
        }
        JsonArray hist = new JsonArray();
        for (byte b : m.moveHistory) {
            hist.add(new JsonPrimitive(b));
        }
        o.add("move_history", hist);
        o.addProperty("half_dead", m.halfDead);
        o.addProperty("alive", !m.isDeadOrEscaped());
        o.addProperty("escaped", m.isEscaping);
        o.addProperty("is_gone", m.isDeadOrEscaped());
        // Bronze Orb stasis：被吸走的卡（stasisCard 为私有字段，用反射读）
        try {
            if (com.megacrit.cardcrawl.dungeons.AbstractDungeon.getCurrRoom() != null
                    && com.megacrit.cardcrawl.dungeons.AbstractDungeon.getCurrRoom().monsters != null) {
                Object orb = null;
                for (Object mm : com.megacrit.cardcrawl.dungeons.AbstractDungeon.getCurrRoom().monsters.monsters) {
                    if (mm instanceof com.megacrit.cardcrawl.monsters.city.BronzeOrb) {
                        orb = mm;
                        break;
                    }
                }
                if (orb != null) {
                    Object sc = basemod.ReflectionHacks.getPrivate(orb,
                            com.megacrit.cardcrawl.monsters.city.BronzeOrb.class, "stasisCard");
                    if (sc instanceof com.megacrit.cardcrawl.cards.AbstractCard) {
                        o.addProperty("stasis_card",
                                ((com.megacrit.cardcrawl.cards.AbstractCard) sc).cardID);
                    }
                }
            }
        } catch (Exception e) { /* no Bronze Orb present */ }
        return o;
    }

    // ------------------------------------------------------------------
    // 地图
    // ------------------------------------------------------------------

    static String symbolToType(String s) {
        if (s == null) {
            return "NONE";
        }
        switch (s) {
            case "M": return "MONSTER";
            case "$": return "SHOP";
            case "T": return "TREASURE";
            case "R": return "REST";
            case "E": return "ELITE";
            case "B": return "BOSS";
            case "?": return "EVENT";
            default: return "NONE";
        }
    }

    public static JsonObject map() {
        JsonObject o = new JsonObject();
        ArrayList<ArrayList<MapRoomNode>> map = AbstractDungeon.map;
        MapRoomNode currNode = AbstractDungeon.getCurrMapNode();
        // 第一趟：为所有节点分配平铺下标（边指向更高行，必须先全部编号再转换）
        Map<String, Integer> indexOf = new HashMap<String, Integer>();
        int idx = 0;
        for (ArrayList<MapRoomNode> row : map) {
            for (MapRoomNode node : row) {
                indexOf.put(node.x + "," + node.y, idx++);
            }
        }
        // 第二趟：输出全部节点与边（无损全图拓扑；客户端自行过滤不可达点）
        JsonArray nodes = new JsonArray();
        for (ArrayList<MapRoomNode> row : map) {
            for (MapRoomNode node : row) {
                JsonObject n = new JsonObject();
                n.addProperty("index", indexOf.get(node.x + "," + node.y));
                n.addProperty("x", node.x);
                n.addProperty("y", node.y);
                n.addProperty("type", symbolToType(node.getRoomSymbol(true)));
                JsonArray edges = new JsonArray();
                for (MapEdge e : node.getEdges()) {
                    if (e.srcX == node.x && e.srcY == node.y) {
                        Integer di = indexOf.get(e.dstX + "," + e.dstY);
                        if (di != null) {
                            edges.add(new JsonPrimitive(di));
                        }
                    }
                }
                n.add("edges", edges);
                nodes.add(n);
            }
        }
        o.add("nodes", nodes);
        o.add("current", currNode != null && indexOf.containsKey(currNode.x + "," + currNode.y)
                ? new JsonPrimitive(indexOf.get(currNode.x + "," + currNode.y)) : JsonNull.INSTANCE);
        if (currNode != null) {
            o.addProperty("current_x", currNode.x);
            o.addProperty("current_y", currNode.y);
        }
        o.addProperty("first_room_chosen", AbstractDungeon.firstRoomChosen);
        o.addProperty("boss_door", Screens.bossDoorAvailable());
        JsonArray choices = new JsonArray();
        for (MapRoomNode n : Screens.mapChoices()) {
            choices.add(new JsonPrimitive(indexOf.get(n.x + "," + n.y)));
        }
        o.add("choices", choices);
        return o;
    }

    // ------------------------------------------------------------------
    // screen 各类型专有字段
    // ------------------------------------------------------------------

    private static String optionLabel(String msg) {
        String unformatted = msg == null ? "" : msg.replaceAll("#.|NL", "");
        java.util.regex.Matcher matcher = java.util.regex.Pattern.compile("\\[(.*?)]").matcher(unformatted);
        return matcher.find() ? matcher.group(1).trim() : unformatted.trim();
    }

    static JsonObject screen() {
        JsonObject o = new JsonObject();
        String type = Screens.current();
        o.addProperty("type", type);
        AbstractRoom room = Screens.inDungeon() ? AbstractDungeon.getCurrRoom() : null;
        switch (type) {
            case Screens.HAND_SELECT: {
                com.megacrit.cardcrawl.screens.select.HandCardSelectScreen s = AbstractDungeon.handCardSelectScreen;
                boolean anyNumber = (Boolean) ReflectionHacks.getPrivate(
                        s, com.megacrit.cardcrawl.screens.select.HandCardSelectScreen.class, "anyNumber");
                o.addProperty("num_to_select", anyNumber || s.upTo ? -1 : s.numCardsToSelect);
                o.addProperty("max_to_select", anyNumber ? AbstractDungeon.player.hand.size() : s.numCardsToSelect);
                o.addProperty("can_pick_zero", s.canPickZero || anyNumber || s.upTo);
                o.addProperty("selection_reason", s.selectionReason == null ? "" : s.selectionReason);
                JsonArray sel = new JsonArray();
                for (AbstractCard c : s.selectedCards.group) {
                    int i = AbstractDungeon.player.hand.group.indexOf(c);
                    if (i >= 0) {
                        sel.add(new JsonPrimitive(i));
                    }
                }
                o.add("selected", sel);
                break;
            }
            case Screens.GRID_SELECT: {
                com.megacrit.cardcrawl.screens.select.GridCardSelectScreen s = AbstractDungeon.gridSelectScreen;
                int numCards = (Integer) ReflectionHacks.getPrivate(
                        s, com.megacrit.cardcrawl.screens.select.GridCardSelectScreen.class, "numCards");
                boolean canCancel = (Boolean) ReflectionHacks.getPrivate(
                        s, com.megacrit.cardcrawl.screens.select.GridCardSelectScreen.class, "canCancel");
                String pile = "mixed";
                if (s.targetGroup != null && s.targetGroup.type != null) {
                    switch (s.targetGroup.type) {
                        case DRAW_PILE: pile = "draw"; break;
                        case DISCARD_PILE: pile = "discard"; break;
                        case EXHAUST_PILE: pile = "exhaust"; break;
                        case HAND: pile = "hand"; break;
                        case MASTER_DECK: pile = "deck"; break;
                        default: pile = "mixed";
                    }
                }
                o.addProperty("pile", pile);
                o.addProperty("num", s.anyNumber ? -1 : numCards);
                o.addProperty("any_number", s.anyNumber);
                o.addProperty("can_pick_zero", s.anyNumber);
                o.addProperty("for_upgrade", s.forUpgrade);
                o.addProperty("for_transform", s.forTransform);
                o.addProperty("for_purge", s.forPurge);
                o.addProperty("can_cancel", canCancel || s.confirmScreenUp);
                o.addProperty("confirm_up", s.confirmScreenUp || s.isJustForConfirming);
                o.add("cards", cards(s.targetGroup != null ? s.targetGroup.group : new ArrayList<AbstractCard>()));
                JsonArray sel = new JsonArray();
                if (s.targetGroup != null) {
                    for (AbstractCard c : s.selectedCards) {
                        int i = s.targetGroup.group.indexOf(c);
                        if (i >= 0) {
                            sel.add(new JsonPrimitive(i));
                        }
                    }
                }
                o.add("selected", sel);
                break;
            }
            case Screens.CARD_REWARD: {
                com.megacrit.cardcrawl.screens.CardRewardScreen s = AbstractDungeon.cardRewardScreen;
                boolean bowlShown = !((Boolean) ReflectionHacks.getPrivate(
                        ReflectionHacks.getPrivate(s, com.megacrit.cardcrawl.screens.CardRewardScreen.class, "bowlButton"),
                        com.megacrit.cardcrawl.ui.buttons.SingingBowlButton.class, "isHidden"));
                boolean skipShown = !((Boolean) ReflectionHacks.getPrivate(
                        ReflectionHacks.getPrivate(s, com.megacrit.cardcrawl.screens.CardRewardScreen.class, "skipButton"),
                        com.megacrit.cardcrawl.ui.buttons.SkipCardButton.class, "isHidden"));
                o.addProperty("bowl_available", bowlShown);
                o.addProperty("skip_available", skipShown);
                o.add("cards", cards(s.rewardGroup));
                break;
            }
            case Screens.COMBAT_REWARD: {
                JsonArray items = new JsonArray();
                int i = 0;
                for (RewardItem r : AbstractDungeon.combatRewardScreen.rewards) {
                    JsonObject item = new JsonObject();
                    item.addProperty("index", i++);
                    item.addProperty("type", r.type.name());
                    switch (r.type) {
                        case GOLD:
                        case STOLEN_GOLD:
                            item.addProperty("gold", r.goldAmt + r.bonusGold);
                            break;
                        case RELIC:
                            item.addProperty("relic_id", r.relic.relicId);
                            break;
                        case POTION:
                            item.addProperty("potion_id", r.potion.ID);
                            break;
                        case CARD:
                            item.add("cards", cards(r.cards));
                            break;
                        default:
                            break;
                    }
                    items.add(item);
                }
                o.add("items", items);
                break;
            }
            case Screens.SHOP: {
                ShopScreen s = AbstractDungeon.shopScreen;
                ArrayList<AbstractCard> cs = new ArrayList<AbstractCard>();
                cs.addAll((ArrayList<AbstractCard>) ReflectionHacks.getPrivate(s, ShopScreen.class, "coloredCards"));
                cs.addAll((ArrayList<AbstractCard>) ReflectionHacks.getPrivate(s, ShopScreen.class, "colorlessCards"));
                JsonArray cardsA = new JsonArray();
                int i = 0;
                for (AbstractCard c : cs) {
                    JsonObject entry = new JsonObject();
                    entry.addProperty("index", i++);
                    entry.add("card", card(c));
                    entry.addProperty("price", c.price);
                    cardsA.add(entry);
                }
                o.add("cards", cardsA);
                JsonArray relicsA = new JsonArray();
                i = 0;
                for (StoreRelic r : (ArrayList<StoreRelic>) ReflectionHacks.getPrivate(s, ShopScreen.class, "relics")) {
                    JsonObject entry = new JsonObject();
                    entry.addProperty("index", i++);
                    entry.addProperty("id", r.relic.relicId);
                    entry.addProperty("price", r.price);
                    relicsA.add(entry);
                }
                o.add("relics", relicsA);
                JsonArray potionsA = new JsonArray();
                i = 0;
                for (StorePotion p : (ArrayList<StorePotion>) ReflectionHacks.getPrivate(s, ShopScreen.class, "potions")) {
                    JsonObject entry = new JsonObject();
                    entry.addProperty("index", i++);
                    entry.addProperty("id", p.potion.ID);
                    entry.addProperty("price", p.price);
                    potionsA.add(entry);
                }
                o.add("potions", potionsA);
                o.addProperty("purge_available", s.purgeAvailable);
                o.addProperty("purge_cost", ShopScreen.actualPurgeCost);
                break;
            }
            case Screens.EVENT:
            case Screens.GREMLIN_MATCH:
            case Screens.GREMLIN_WHEEL: {
                AbstractEvent event = room.event;
                o.addProperty("id", eventId(event));
                Object name = ReflectionHacks.getPrivateStatic(event.getClass(), "NAME");
                o.addProperty("name", name != null ? name.toString() : Bridge.safeSimpleName(event.getClass()));
                o.addProperty("body_text", stripFormatting(HoverPatches.EventBodyText.bodyText));
                JsonArray options = new JsonArray();
                ArrayList<LargeDialogOptionButton> buttons = eventButtons();
                int i = 0;
                for (LargeDialogOptionButton b : buttons) {
                    JsonObject opt = new JsonObject();
                    opt.addProperty("index", i++);
                    opt.addProperty("text", stripFormatting(b.msg));
                    opt.addProperty("label", optionLabel(b.msg));
                    opt.addProperty("disabled", b.isDisabled);
                    com.google.gson.JsonObject tbl = eventEffects().getAsJsonObject(o.get("id").getAsString());
                    if (tbl != null) {
                        com.google.gson.JsonElement eff = tbl.get(optionLabel(b.msg).toLowerCase());
                        if (eff != null && eff.isJsonObject()) {
                            opt.add("effects", eff);
                        }
                    }
                    options.add(opt);
                }
                o.add("options", options);
                if (Screens.GREMLIN_MATCH.equals(type) && HoverPatches.GremlinMatch.cards != null) {
                    o.add("match_cards", cards(HoverPatches.GremlinMatch.getOrderedCards()));
                }
                break;
            }
            case Screens.REST: {
                JsonArray options = new JsonArray();
                ArrayList<AbstractCampfireOption> buttons = (ArrayList<AbstractCampfireOption>)
                        ReflectionHacks.getPrivate(((RestRoom) room).campfireUI,
                                com.megacrit.cardcrawl.rooms.CampfireUI.class, "buttons");
                int i = 0;
                for (AbstractCampfireOption b : buttons) {
                    JsonObject opt = new JsonObject();
                    opt.addProperty("index", i++);
                    String cls = b.getClass().getSimpleName();
                    String name = cls.equals("RestOption") ? "sleep"
                            : (cls.endsWith("Option") ? cls.substring(0, cls.length() - "Option".length()) : cls).toLowerCase();
                    opt.addProperty("name", name);
                    opt.addProperty("usable", b.usable);
                    // 选项专有信息：候选牌列表顺序 = 随后 grid_select 的顺序（下标直接可用）
                    if ("sleep".equals(name) && b.usable) {
                        int heal = (int) Math.floor(AbstractDungeon.player.maxHealth * 0.3);
                        if (AbstractDungeon.player.hasRelic("Regal Pillow")) {
                            heal += 15;
                        }
                        opt.addProperty("heal", heal);
                    } else if ("smith".equals(name) && b.usable) {
                        opt.add("cards", cards(AbstractDungeon.player.masterDeck.getUpgradableCards().group));
                    } else if ("toke".equals(name) && b.usable) {
                        opt.add("cards", cards(CardGroup.getGroupWithoutBottledCards(
                                AbstractDungeon.player.masterDeck.getPurgeableCards()).group));
                    }
                    options.add(opt);
                }
                o.add("options", options);
                break;
            }
            case Screens.CHEST: {
                // 奖励进房时已 roll（AbstractChest 构造器 randomizeReward；BossChest 构造时 roll 3 遗物），
                // 前置到观测零 RNG 代价，开不开箱的决策信息完备
                boolean boss = room instanceof TreasureRoomBoss;
                com.megacrit.cardcrawl.rewards.chests.AbstractChest chest = boss
                        ? ((TreasureRoomBoss) room).chest : ((TreasureRoom) room).chest;
                o.addProperty("boss", boss);
                o.addProperty("opened", chest.isOpen);
                String ctype = chest instanceof com.megacrit.cardcrawl.rewards.chests.SmallChest ? "small"
                        : chest instanceof com.megacrit.cardcrawl.rewards.chests.MediumChest ? "medium"
                        : chest instanceof com.megacrit.cardcrawl.rewards.chests.LargeChest ? "large" : "boss";
                o.addProperty("chest_type", ctype);
                JsonObject contents = new JsonObject();
                if (chest instanceof com.megacrit.cardcrawl.rewards.chests.BossChest) {
                    JsonArray arr = new JsonArray();
                    for (AbstractRelic r : ((com.megacrit.cardcrawl.rewards.chests.BossChest) chest).relics) {
                        arr.add(new com.google.gson.JsonPrimitive(r.relicId));
                    }
                    contents.add("relics", arr);
                    contents.addProperty("choose", 3);
                } else {
                    contents.addProperty("gold", chest.goldReward);
                    contents.addProperty("gold_base", chest.GOLD_AMT);
                    contents.addProperty("relic_tier", chest.relicReward.name().replace("_RELIC", "").toLowerCase());
                }
                o.add("contents", contents);
                break;
            }
            case Screens.BOSS_RELIC: {
                JsonArray relics = new JsonArray();
                int i = 0;
                for (AbstractRelic r : AbstractDungeon.bossRelicScreen.relics) {
                    JsonObject entry = new JsonObject();
                    entry.addProperty("index", i++);
                    entry.addProperty("id", r.relicId);
                    relics.add(entry);
                }
                o.add("relics", relics);
                break;
            }
            case Screens.GAME_OVER: {
                boolean won;
                int score = 0;
                if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.DEATH) {
                    won = GameOverScreen.isVictory;
                    Object sc = ReflectionHacks.getPrivate(AbstractDungeon.deathScreen, GameOverScreen.class, "score");
                    score = sc instanceof Integer ? (Integer) sc : 0;
                } else {
                    won = AbstractDungeon.screen == AbstractDungeon.CurrentScreen.VICTORY
                            || Screens.inDungeon() && AbstractDungeon.getCurrRoom() instanceof VictoryRoom;
                    Object sc = AbstractDungeon.victoryScreen != null
                            ? ReflectionHacks.getPrivate(AbstractDungeon.victoryScreen, GameOverScreen.class, "score") : 0;
                    score = sc instanceof Integer ? (Integer) sc : 0;
                }
                o.addProperty("won", won);
                o.addProperty("score", score);
                break;
            }
            default:
                break;
        }
        return o;
    }

    static ArrayList<LargeDialogOptionButton> eventButtons() {
        AbstractEvent event = AbstractDungeon.getCurrRoom().event;
        // ⚠️ 不能只看 GenericEventDialog.show：Dead Adventurer 等事件在「被偷袭」
        // 后把选项放进 imageEventText，但 show 已翻 false，导致 optionList 读错、
        // legal_actions 为空 → 驱动卡死（实测 floor11 3 Sentries 事件战）。
        // 改为「取非空的那个」，两个都空才算无按钮（Gremlin 小游戏走另一条路）。
        if (event == null) {
            return com.megacrit.cardcrawl.events.RoomEventDialog.optionList;
        }
        ArrayList<LargeDialogOptionButton> img = event.imageEventText.optionList;
        ArrayList<LargeDialogOptionButton> room =
                com.megacrit.cardcrawl.events.RoomEventDialog.optionList;
        // 优先按「当前显示的是哪个 dialog」取（show 标志是权威）：
        //   show=true → imageEventText（ImageEvent 系）
        //   show=false → roomEventText（AbstractEvent 系，如 Golden Idol / Dead Adventurer）
        // ⚠️ 不能「取非空的那个」：ImageEvent 的 optionList 会残留上一次内容，
        //    两个都非空时会取错 → 点到的按钮不属于当前事件（实测 Golden Idol 的
        //    Take 点了没反应，事件停在原屏）。
        if (Screens.imageEventDialogShown()) {
            return img != null ? img : room;
        }
        if (room != null && !room.isEmpty()) {
            return room;
        }
        return img != null ? img : room;
    }

    static String eventId(AbstractEvent event) {
        if (event instanceof com.megacrit.cardcrawl.neow.NeowEvent) {
            return "Neow";
        }
        try {
            Field f = event.getClass().getDeclaredField("ID");
            f.setAccessible(true);
            return (String) f.get(null);
        } catch (Exception e) {
            return Bridge.safeSimpleName(event.getClass());
        }
    }

    static String stripFormatting(String text) {
        if (text == null) {
            return "";
        }
        return text.replaceAll("~|@(\\S+)~|@", "$1").replaceAll("#.|NL", "");
    }

    // ------------------------------------------------------------------
    // 顶层 observation
    // ------------------------------------------------------------------

    /**
     * 录制用快照：与 neow-sim/tools/parity_replay.py 期望的字段对齐。
     * 复用 observation() 的输出，只做字段重命名/裁剪。
     */
    public static JsonObject snapshotForTrace(JsonObject obs, JsonObject info) {
        JsonObject o = new JsonObject();
        o.addProperty("screen", info.has("screen") ? info.get("screen").getAsString() : null);
        if (info.has("act")) o.addProperty("act", info.get("act").getAsInt());
        if (info.has("floor")) o.addProperty("floor", info.get("floor").getAsInt());
        JsonObject meta = obs.has("meta") ? obs.getAsJsonObject("meta") : new JsonObject();
        o.add("meta", meta);
        if (meta.has("turn")) o.add("turn", meta.get("turn"));
        o.add("player", obs.has("player") ? obs.get("player") : new JsonObject());
        o.add("hand", obs.has("hand") ? obs.get("hand") : new JsonArray());
        o.add("monsters", obs.has("monsters") ? obs.get("monsters") : new JsonArray());
        JsonObject dp = obs.has("draw_pile") ? obs.getAsJsonObject("draw_pile") : new JsonObject();
        o.addProperty("draw_pile_n", dp.has("count") ? dp.get("count").getAsInt() : 0);
        if (dp.has("cards") && !dp.get("cards").isJsonNull()) {
            JsonArray ids = new JsonArray();
            for (com.google.gson.JsonElement e : dp.getAsJsonArray("cards")) {
                ids.add(e.getAsJsonObject().get("id"));
            }
            o.add("draw_order", ids);
        }
        JsonObject di = obs.has("discard_pile") ? obs.getAsJsonObject("discard_pile") : new JsonObject();
        o.addProperty("discard_pile_n", di.has("count") ? di.get("count").getAsInt() : 0);
        JsonObject ex = obs.has("exhaust_pile") ? obs.getAsJsonObject("exhaust_pile") : new JsonObject();
        o.addProperty("exhaust_pile_n", ex.has("count") ? ex.get("count").getAsInt() : 0);
        JsonObject md = obs.has("master_deck") ? obs.getAsJsonObject("master_deck") : new JsonObject();
        o.add("deck", md.has("cards") ? md.get("cards") : new JsonArray());
        JsonObject scr = obs.has("screen") ? obs.getAsJsonObject("screen") : new JsonObject();
        if (scr.has("type") && "event".equals(scr.get("type").getAsString())) {
            JsonObject pay = new JsonObject();
            pay.addProperty("type", "event");
            if (scr.has("id")) pay.add("id", scr.get("id"));
            pay.add("options", scr.has("options") ? scr.get("options") : new JsonArray());
            o.add("screen_payload", pay);
        }
        o.add("relics", obs.has("relics") ? obs.get("relics") : new JsonArray());
        o.add("rng", obs.has("rng") ? obs.get("rng") : new JsonObject());
        return o;
    }

    public static JsonObject observation(Bridge.Config cfg) {
        JsonObject o = new JsonObject();
        boolean inDungeon = Screens.inDungeon();
        AbstractPlayer p = inDungeon ? AbstractDungeon.player : null;

        JsonObject screenObj = screen();
        String scr = screenObj.get("type").getAsString();

        JsonObject meta = new JsonObject();
        if (p != null) {
            meta.addProperty("character", p.chosenClass.name());
            meta.addProperty("act", AbstractDungeon.actNum);
            if (AbstractDungeon.name != null) {
                meta.addProperty("act_name", AbstractDungeon.name);
            }
            meta.addProperty("floor", AbstractDungeon.floorNum);
            meta.addProperty("ascension", AbstractDungeon.ascensionLevel);
            if (AbstractDungeon.getCurrRoom() != null
                    && AbstractDungeon.getCurrRoom().phase == AbstractRoom.RoomPhase.COMBAT) {
                meta.addProperty("turn", GameActionManager.turn);
            } else {
                meta.add("turn", JsonNull.INSTANCE);
            }
        } else {
            meta.add("character", JsonNull.INSTANCE);
            meta.add("act", JsonNull.INSTANCE);
            meta.add("floor", JsonNull.INSTANCE);
            meta.add("ascension", JsonNull.INSTANCE);
            meta.add("turn", JsonNull.INSTANCE);
        }
        if (Settings.seed != null) {
            meta.addProperty("seed", Settings.seed);
            meta.addProperty("seed_code", SeedHelper.getString(Settings.seed));
        } else {
            meta.add("seed", JsonNull.INSTANCE);
        }
        boolean over = Screens.GAME_OVER.equals(scr);
        meta.addProperty("over", over);
        if (over && screenObj.has("won")) {
            meta.add("won", screenObj.get("won"));
        } else {
            meta.add("won", JsonNull.INSTANCE);
        }
        o.add("meta", meta);

        if (!inDungeon) {
            o.addProperty("in_dungeon", false);
            o.add("screen", screenObj);
            return o;
        }
        o.addProperty("in_dungeon", true);

        // RNG 计数器（对拍与研究用）
        JsonObject rng = new JsonObject();
        putRng(rng, "monster", AbstractDungeon.monsterRng);
        putRng(rng, "map", AbstractDungeon.mapRng);
        putRng(rng, "event", AbstractDungeon.eventRng);
        putRng(rng, "merchant", AbstractDungeon.merchantRng);
        putRng(rng, "card", AbstractDungeon.cardRng);
        putRng(rng, "treasure", AbstractDungeon.treasureRng);
        putRng(rng, "relic", AbstractDungeon.relicRng);
        putRng(rng, "potion", AbstractDungeon.potionRng);
        putRng(rng, "monster_hp", AbstractDungeon.monsterHpRng);
        putRng(rng, "ai", AbstractDungeon.aiRng);
        putRng(rng, "shuffle", AbstractDungeon.shuffleRng);
        putRng(rng, "card_random", AbstractDungeon.cardRandomRng);
        putRng(rng, "misc", AbstractDungeon.miscRng);
        o.add("rng", rng);

        JsonObject player = new JsonObject();
        player.addProperty("hp", p.currentHealth);
        player.addProperty("max_hp", p.maxHealth);
        player.addProperty("block", p.currentBlock);
        player.addProperty("energy", com.megacrit.cardcrawl.ui.panels.EnergyPanel.totalCount);
        player.addProperty("energy_cap", p.energy.energyMaster);
        player.addProperty("gold", p.gold);
        player.addProperty("potion_slots", p.potionSlots);
        player.addProperty("hand_size", p.gameHandSize);
        // ── 回合内计数器（全部走真实存在的字段/推导） ──
        // 本回合打出的牌数（AbstractPlayer.cardsPlayedThisTurn）与其中攻击牌数
        player.addProperty("cards_played_this_turn", p.cardsPlayedThisTurn);
        int atk = 0;
        int lastTypeIdx = -1;
        for (com.megacrit.cardcrawl.cards.CardQueueItem qi : AbstractDungeon.actionManager.cardQueue) {
            if (qi.card == null) continue;
            if (qi.card.type == AbstractCard.CardType.ATTACK) ++atk;
            lastTypeIdx = qi.card.type.ordinal();
        }
        // 已结算的那部分：GameActionManager.cardsPlayedThisTurn（ArrayList）
        for (Object o2 : AbstractDungeon.actionManager.cardsPlayedThisTurn) {
            if (!(o2 instanceof AbstractCard)) continue;
            AbstractCard c2 = (AbstractCard) o2;
            if (c2.type == AbstractCard.CardType.ATTACK) ++atk;
        }
        player.addProperty("attacks_this_turn", atk);
        // 本回合最后打出的牌类型（无则空串）
        player.addProperty("last_card_type", lastCardTypeName());
        player.add("powers", powers(p.powers));
        JsonObject keys = new JsonObject();
        keys.addProperty("ruby", Settings.hasRubyKey);
        keys.addProperty("emerald", Settings.hasEmeraldKey);
        keys.addProperty("sapphire", Settings.hasSapphireKey);
        player.add("keys", keys);
        JsonArray orbs = new JsonArray();
        for (AbstractOrb orb : p.orbs) {
            if (orb == null) {
                orbs.add(JsonNull.INSTANCE);
                continue;
            }
            JsonObject ob = new JsonObject();
            ob.addProperty("id", orb.ID);
            ob.addProperty("passive", orb.passiveAmount);
            ob.addProperty("evoke", orb.evokeAmount);
            orbs.add(ob);
        }
        player.add("orbs", orbs);
        player.addProperty("stance", p.stance != null ? p.stance.ID : "None");
        o.add("player", player);

        o.add("hand", cards(p.hand.group)); // 手牌：数组顺序即显示序，card_index 直接引用
        o.add("draw_pile", pileSorted(p.drawPile, cfg.cheatDrawOrder));
        o.add("discard_pile", pileChrono(p.discardPile));
        o.add("exhaust_pile", pileChrono(p.exhaustPile));
        o.add("master_deck", pileSortedDeck(p.masterDeck));
        if (p.cardInUse != null) {
            o.add("card_in_use", card(p.cardInUse));
        }

        JsonArray relics = new JsonArray();
        int ri = 0;
        for (AbstractRelic r : p.relics) {
            JsonObject entry = new JsonObject();
            entry.addProperty("index", ri++);
            entry.addProperty("id", r.relicId);
            entry.addProperty("name", r.name);
            entry.addProperty("counter", r.counter);
            entry.addProperty("tier", r.tier.name());
            entry.addProperty("enabled", !r.grayscale);
            // v0.5: counter period（周期类遗物的满值）
            int period = com.neow.bridge.RelicPeriods.getPeriod(r.relicId);
            if (period > 0) {
                entry.addProperty("period", period);
                entry.addProperty("ready", r.counter >= period);
            }
            entry.addProperty("used_this_turn", r.counter == 0 && period > 0);
            relics.add(entry);
        }
        o.add("relics", relics);

        JsonArray potions = new JsonArray();
        for (AbstractPotion potion : p.potions) {
            if (potion instanceof PotionSlot) {
                potions.add(JsonNull.INSTANCE);
            } else {
                JsonObject po = new JsonObject();
                po.addProperty("id", potion.ID);
                po.addProperty("name", potion.name);
                po.addProperty("can_use", potion.canUse());
                po.addProperty("can_discard", potion.canDiscard());
                po.addProperty("target_required", potion.targetRequired);
                potions.add(po);
            }
        }
        o.add("potions", potions);

        JsonArray monsters = new JsonArray();
        if (AbstractDungeon.getCurrRoom() != null && AbstractDungeon.getCurrRoom().monsters != null) {
            int mi = 0;
            for (AbstractMonster m : AbstractDungeon.getCurrRoom().monsters.monsters) {
                monsters.add(monster(m, mi++));
            }
        }
        o.add("monsters", monsters);

        o.add("map", map());
        o.add("screen", screen());
        return o;
    }

    private static void putRng(JsonObject o, String name, com.megacrit.cardcrawl.random.Random r) {
        if (r != null) {
            o.addProperty(name, r.counter);
        }
    }
}
