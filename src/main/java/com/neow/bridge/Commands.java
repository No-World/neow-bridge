package com.neow.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import com.megacrit.cardcrawl.characters.AbstractPlayer;
import com.megacrit.cardcrawl.characters.CharacterManager;
import com.megacrit.cardcrawl.core.CardCrawlGame;
import com.megacrit.cardcrawl.core.Settings;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.helpers.SeedHelper;
import com.megacrit.cardcrawl.random.Random;
import com.neow.bridge.Actions.InvalidActionException;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.util.ArrayDeque;

/**
 * 命令分发：把传输层请求编译为任务序列，或直接组装 gym 响应。
 *
 * 命令集：ping / new_run / step / get_state / quit_run / kill_game / config
 * （封装规格见 neow-bridge/PROTOCOL.md；gym 语义见 docs/protocol.md v0.3）
 */
public final class Commands {

    public static final String NEW_RUN = "new_run";
    public static final String QUIT_RUN = "quit_run";

    private static final Logger logger = LogManager.getLogger(Commands.class.getName());

    private Commands() {
    }

    // ------------------------------------------------------------------
    // 响应封装
    // ------------------------------------------------------------------

    public static JsonObject error(JsonObject request, String type, String message) {
        JsonObject o = new JsonObject();
        if (request != null && request.has("id")) {
            o.add("id", request.get("id"));
        }
        o.addProperty("ok", false);
        o.addProperty("error", message);
        o.addProperty("error_type", type);
        return o;
    }

    private static JsonObject ok(JsonObject request) {
        JsonObject o = new JsonObject();
        if (request.has("id")) {
            o.add("id", request.get("id"));
        }
        o.addProperty("ok", true);
        return o;
    }

    /** 在游戏线程构建 gym 五元组响应。 */
    public static JsonObject respond(JsonObject request) {
        String cmd = Actions.str(request, "cmd");
        JsonObject resp = ok(request);
        JsonObject result = new JsonObject();
        if ("ping".equals(cmd)) {
            result.addProperty("protocol_version", Bridge.PROTOCOL_VERSION);
            result.addProperty("bridge_version", Bridge.BRIDGE_VERSION);
            result.addProperty("in_dungeon", Screens.inDungeon());
            result.addProperty("screen", Screens.current());
            result.addProperty("bridge_state", Bridge.diagState());
            // 启动诊断：主菜单未就绪时看卡在哪
            result.addProperty("mode", String.valueOf(com.megacrit.cardcrawl.core.CardCrawlGame.mode));
            result.addProperty("main_menu_built", com.megacrit.cardcrawl.core.CardCrawlGame.mainMenuScreen != null);
            if (com.megacrit.cardcrawl.core.CardCrawlGame.mainMenuScreen != null) {
                result.addProperty("menu_screen", String.valueOf(com.megacrit.cardcrawl.core.CardCrawlGame.mainMenuScreen.screen));
                result.addProperty("save_slot_shown", com.megacrit.cardcrawl.core.CardCrawlGame.mainMenuScreen.saveSlotScreen.shown);
            }
        } else if (NEW_RUN.equals(cmd) || "step".equals(cmd) || "get_state".equals(cmd)
                || QUIT_RUN.equals(cmd)) {
            result = gymState();
        } else if ("kill_game".equals(cmd) || "config".equals(cmd)) {
            result.addProperty("done", true);
        } else {
            return error(request, "unknown_command", "unknown cmd: " + cmd);
        }
        resp.add("result", result);
        return resp;
    }

    /** gym 五元组：observation / legal_actions / reward / done / info。 */
    private static JsonObject gymState() {
        JsonObject result = new JsonObject();
        boolean inDungeon = Screens.inDungeon();
        result.add("observation", Obs.observation(Bridge.CFG));

        JsonArray legal = new JsonArray();
        boolean done = false;
        double reward = 0.0;
        if (inDungeon) {
            legal = Legal.legalActions();
            String scr = Screens.current();
            done = Screens.GAME_OVER.equals(scr);
            if (done) {
                JsonObject obs = result.getAsJsonObject("observation");
                JsonObject meta = obs.getAsJsonObject("meta");
                boolean won = meta.has("won") && !meta.get("won").isJsonNull() && meta.get("won").getAsBoolean();
                reward = won ? 1.0 : -1.0;
            }
        }
        result.add("legal_actions", legal);
        result.addProperty("reward", reward);
        result.addProperty("done", done);

        JsonObject info = new JsonObject();
        info.addProperty("protocol_version", Bridge.PROTOCOL_VERSION);
        info.addProperty("bridge_version", Bridge.BRIDGE_VERSION);
        info.addProperty("screen", Screens.current());
        info.addProperty("in_dungeon", inDungeon);
        if (inDungeon) {
            info.addProperty("room", Bridge.safeSimpleName(AbstractDungeon.getCurrRoom().getClass()));
            info.addProperty("act", AbstractDungeon.actNum);
            info.addProperty("floor", AbstractDungeon.floorNum);
        }
        result.add("info", info);
        return result;
    }

    // ------------------------------------------------------------------
    // 编译：请求 → 任务序列（游戏线程调用）
    // ------------------------------------------------------------------

    /** @return null 表示无需任务/直接响应。 */
    public static ArrayDeque<Runnable> compile(JsonObject request) throws InvalidActionException {
        String cmd = Actions.str(request, "cmd");
        if (cmd == null) {
            throw new InvalidActionException("cmd missing");
        }
        switch (cmd) {
            case "ping":
            case "get_state":
                return null;
            case "step": {
                if (!request.has("action") || !request.get("action").isJsonObject()) {
                    throw new InvalidActionException("step requires action object");
                }
                JsonObject action = request.getAsJsonObject("action");
                checkLegal(action);
                return Actions.compile(action);
            }
            case NEW_RUN:
                return newRun(request);
            case QUIT_RUN:
                return quitRun();
            case "kill_game":
                return killGame();
            case "config":
                return config(request);
            default:
                throw new InvalidActionException("unknown cmd: " + cmd);
        }
    }

    /** step 前置合法性检查：动作类型必须出现在当前合法动作集合中（选择屏描述符按类型匹配）。 */
    private static void checkLegal(JsonObject action) throws InvalidActionException {
        String scr = Screens.current();
        if (!Screens.inDungeon()) {
            throw new InvalidActionException("not in a run; send new_run first (screen=" + scr + ")");
        }
        String type = Actions.str(action, "type");
        JsonArray legal = Legal.legalActions();
        boolean found = false;
        for (int i = 0; i < legal.size(); i++) {
            JsonObject l = legal.get(i).getAsJsonObject();
            if (Actions.str(l, "type").equals(type)) {
                found = true;
                break;
            }
        }
        if (!found) {
            throw new InvalidActionException("action type '" + type + "' not legal on screen " + scr);
        }
    }

    private static ArrayDeque<Runnable> newRun(JsonObject request) throws InvalidActionException {
        if (Screens.inDungeon()) {
            throw new InvalidActionException("already in a run; send quit_run first");
        }
        if (CardCrawlGame.mainMenuScreen == null) {
            throw new InvalidActionException("main menu not ready yet");
        }
        String ch = Actions.str(request, "character");
        AbstractPlayer.PlayerClass cls;
        try {
            cls = AbstractPlayer.PlayerClass.valueOf(ch == null ? "IRONCLAD" : ch.toUpperCase());
        } catch (IllegalArgumentException e) {
            throw new InvalidActionException("unknown character: " + ch);
        }
        int ascension = request.has("ascension") && !request.get("ascension").isJsonNull()
                ? request.get("ascension").getAsInt() : 0;
        if (ascension < 0 || ascension > 20) {
            throw new InvalidActionException("ascension must be in [0,20]");
        }
        long seed;
        boolean seedSet = request.has("seed") && !request.get("seed").isJsonNull();
        if (seedSet) {
            JsonPrimitive p = request.get("seed").getAsJsonPrimitive();
            if (p.isString()) {
                try {
                    seed = Long.parseLong(p.getAsString());
                } catch (NumberFormatException e) {
                    seed = SeedHelper.getLong(p.getAsString().toUpperCase());
                }
            } else {
                seed = p.getAsLong();
            }
        } else {
            seed = SeedHelper.generateUnoffensiveSeed(new Random(System.nanoTime()));
        }
        final long fSeed = seed;
        final boolean fSeedSet = seedSet;
        final int fAsc = ascension;
        final AbstractPlayer.PlayerClass fCls = cls;
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> {
            Settings.seed = fSeed;
            Settings.seedSet = fSeedSet;
            Settings.isDailyRun = false;
            Settings.isTrial = false;
            Settings.isEndless = false;
            Settings.specialSeed = null;
            AbstractDungeon.generateSeeds();
            AbstractDungeon.ascensionLevel = fAsc;
            AbstractDungeon.isAscensionMode = fAsc > 0;
            CardCrawlGame.startOver = true;
            CardCrawlGame.mainMenuScreen.isFadingOut = true;
            CardCrawlGame.mainMenuScreen.fadeOutMusic();
            CharacterManager manager = new CharacterManager();
            manager.setChosenCharacter(fCls);
            CardCrawlGame.chosenCharacter = fCls;
            logger.info("NeowBridge new_run: {} asc={} seed={}", fCls, fAsc, fSeed);
        });
        return tasks;
    }

    private static ArrayDeque<Runnable> quitRun() {
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> CardCrawlGame.startOver());
        return tasks;
    }

    private static ArrayDeque<Runnable> killGame() {
        ArrayDeque<Runnable> tasks = new ArrayDeque<Runnable>();
        tasks.add(() -> com.badlogic.gdx.Gdx.app.exit());
        return tasks;
    }

    private static ArrayDeque<Runnable> config(JsonObject request) throws InvalidActionException {
        Bridge.CFG.fastMode = optBool(request, "fast_mode", Bridge.CFG.fastMode);
        Bridge.CFG.cheatDrawOrder = optBool(request, "cheat_draw_order", Bridge.CFG.cheatDrawOrder);
        if (request.has("settle_stable_frames")) {
            Bridge.CFG.settleStableFrames = Math.max(1, request.get("settle_stable_frames").getAsInt());
        }
        if (request.has("timeout_ms")) {
            Bridge.CFG.timeoutMs = Math.max(1000, request.get("timeout_ms").getAsLong());
        }
        return null; // 直接响应
    }

    private static boolean optBool(JsonObject o, String key, boolean def) {
        return o.has(key) && !o.get(key).isJsonNull() ? o.get(key).getAsBoolean() : def;
    }
}
