package com.neow.bridge;

import com.google.gson.JsonObject;
import com.megacrit.cardcrawl.actions.GameActionManager;
import com.megacrit.cardcrawl.core.Settings;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.rooms.RestRoom;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.util.ArrayDeque;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Bridge 中枢：外部请求 ↔ 游戏线程的桥接与执行状态机。
 *
 * 外部线程调用 submit() 提交命令并阻塞等待结果（带超时）；
 * 游戏线程每帧（PreUpdate）消费任务、每帧（PostUpdate）检查落定；
 * 落定后在游戏线程构建完整 gym 响应（读游戏状态必须在该线程）。
 *
 * 状态机：IDLE → RUNNING(逐帧任务) → SETTLING(等待落定) → 响应 → IDLE
 */
public final class Bridge {

    public static final String PROTOCOL_VERSION = "0.4";
    public static final String BRIDGE_VERSION = "0.3.0";

    private static final Logger logger = LogManager.getLogger(Bridge.class.getName());

    /** 心跳：每帧 +1，供服务器线程判断钩子链是否存活。 */
    public static volatile long heartbeat = 0;
    public static volatile long heartbeatLastNanos = 0;

    /** 运行配置（可经 config 命令修改）。 */
    public static final class Config {
        public volatile boolean fastMode = true;          // 持续按住 FAST_MODE（去动画提速）
        public volatile int settleStableFrames = 3;       // 指纹连续不变帧数
        public volatile long timeoutMs = 60_000;          // 单命令超时
        public volatile boolean cheatDrawOrder = false;   // 对拍研究用：抽牌堆暴露真实顺序
    }

    private static final class Pending {
        final JsonObject request;
        final CompletableFuture<JsonObject> future = new CompletableFuture<>();
        final long deadline = System.currentTimeMillis() + CFG.timeoutMs;
        final String terminalScreen; // 期望到达的终态屏（如 new_run→首个决策点），null 表示任意稳定态

        Pending(JsonObject request, String terminalScreen) {
            this.request = request;
            this.terminalScreen = terminalScreen;
        }
    }

    public static final Config CFG = new Config();

    private enum State { IDLE, RUNNING, SETTLING }

    private static final AtomicReference<Pending> PENDING = new AtomicReference<>(null);
    private static State state = State.IDLE;
    private static ArrayDeque<Runnable> tasks = new ArrayDeque<>();
    private static long lastFingerprint = 0L;
    private static int stableFrames = 0;
    private static int framesSinceTask = 0;

    private Bridge() {
    }


    /** 安全的类短名：绝不走 getSimpleName（其 enclosing 查询会与惰性类加载在 JVM 内部死锁）。 */
    static String safeSimpleName(Class<?> c) {
        String n = c.getName();
        int dot = n.lastIndexOf('.');
        if (dot >= 0) n = n.substring(dot + 1);
        int d = n.lastIndexOf('$');
        if (d >= 0) n = n.substring(d + 1);
        return n.isEmpty() ? "anonymous" : n;
    }

    public static boolean busy() {
        return PENDING.get() != null;
    }

    /** 诊断透视：服务器线程可直接读（不依赖游戏线程）。 */
    public static String diagState() {
        Pending p = PENDING.get();
        return "{\"state\":\"" + state + "\",\"tasks\":" + tasks.size()
                + ",\"pending\":\"" + (p != null ? Actions.str(p.request, "cmd") : null)
                + "\",\"deadline_ms_left\":" + (p != null ? Math.max(0, p.deadline - System.currentTimeMillis()) : -1)
                + ",\"frames_since_task\":" + framesSinceTask + "\"}";
    }

    /** 外部线程：提交请求并等待结果。 */
    public static JsonObject submit(JsonObject request, long timeoutMs) {
        String cmd = Actions.str(request, "cmd");
        String terminal = terminalScreenFor(cmd);
        Pending p = new Pending(request, terminal);
        if (!PENDING.compareAndSet(null, p)) {
            return Commands.error(request, "busy", "another command is in flight");
        }
        try {
            return p.future.get(Math.max(timeoutMs, p.deadline - System.currentTimeMillis()),
                    TimeUnit.MILLISECONDS);
        } catch (java.util.concurrent.TimeoutException e) {
            PENDING.compareAndSet(p, null);
            return Commands.error(request, "timeout", "command timed out: " + cmd);
        } catch (Exception e) {
            PENDING.compareAndSet(p, null);
            return Commands.error(request, "internal", String.valueOf(e));
        }
    }

    private static String terminalScreenFor(String cmd) {
        if (Commands.NEW_RUN.equals(cmd)) {
            return "any-dungeon";
        }
        if (Commands.QUIT_RUN.equals(cmd)) {
            return "menu";
        }
        return null;
    }

    // ------------------------------------------------------------------
    // 游戏线程回调
    // ------------------------------------------------------------------

    /** PreUpdate：编译并逐帧执行任务。 */
    public static void onPreUpdate() {
        heartbeat++;
        heartbeatLastNanos = System.nanoTime();
        if (CFG.fastMode) {
            Settings.FAST_MODE = true;
        }
        Pending p = PENDING.get();
        if (p == null) {
            return;
        }
        if (state == State.IDLE) {
            try {
                JsonObject request = p.request;
                ArrayDeque<Runnable> compiled = Commands.compile(request);
                if (compiled != null) { // null = 无需逐帧任务（如 get_state）
                    tasks = compiled;
                    state = tasks.isEmpty() ? State.SETTLING : State.RUNNING;
                } else {
                    finishInGameThread(p);
                }
            } catch (Actions.InvalidActionException e) {
                fail(p, "invalid_action", e.getMessage());
            } catch (Exception e) {
                logger.error("command compile failed", e);
                fail(p, "internal", String.valueOf(e));
            }
            return;
        }
        if (state == State.RUNNING) {
            Runnable r = tasks.poll();
            try {
                if (r != null) {
                    r.run();
                    framesSinceTask = 0;
                }
            } catch (Exception e) {
                logger.error("task failed", e);
                fail(p, "internal", String.valueOf(e));
                return;
            }
            if (tasks.isEmpty()) {
                state = State.SETTLING;
                resetSettle();
            }
        }
    }

    /** PostUpdate：落定检查与响应构建。绝不让异常漏进渲染循环。 */
    public static void onPostUpdate() {
        Pending p = PENDING.get();
        if (p == null || state != State.SETTLING) {
            return;
        }
        try {
            settleFrame(p);
        } catch (Throwable t) {
            logger.error("settle frame failed", t);
            resetSettle();
        }
    }

    /** 落定超时的现场快照。 */
    private static String diagnostics() {
        StringBuilder sb = new StringBuilder("{");
        try {
            sb.append("\"screen\":\"").append(AbstractDungeon.screen).append("\"");
            sb.append(",\"screenUp\":").append(AbstractDungeon.isScreenUp);
            sb.append(",\"fading\":").append(AbstractDungeon.isFadingOut).append('/').append(AbstractDungeon.isFadingIn);
            if (AbstractDungeon.isPlayerInDungeon()) {
                com.megacrit.cardcrawl.rooms.AbstractRoom room = AbstractDungeon.getCurrRoom();
                sb.append(",\"room\":\"").append(Bridge.safeSimpleName(room.getClass())).append("\"");
                sb.append(",\"phase\":\"").append(room.phase).append("\"");
                GameActionManager am = AbstractDungeon.actionManager;
                sb.append(",\"amPhase\":\"").append(am.phase).append("\"");
                sb.append(",\"acts\":").append(am.actions.size())
                        .append('/').append(am.preTurnActions.size())
                        .append('/').append(am.cardQueue.size())
                        .append('/').append(am.monsterQueue.size())
                        .append(",\"cur\":").append(am.currentAction != null);
                if (room instanceof RestRoom) {
                    sb.append(",\"rest.sel\":").append(((RestRoom) room).campfireUI.somethingSelected);
                    sb.append(",\"rest.hidden\":").append(com.megacrit.cardcrawl.rooms.CampfireUI.hidden);
                }
                if (AbstractDungeon.screen == AbstractDungeon.CurrentScreen.GRID) {
                    sb.append(",\"grid.sel\":").append(AbstractDungeon.gridSelectScreen.selectedCards.size())
                            .append(",\"grid.confirm\":").append(AbstractDungeon.gridSelectScreen.confirmScreenUp)
                            .append(",\"grid.target\":").append(AbstractDungeon.gridSelectScreen.targetGroup != null
                                    ? AbstractDungeon.gridSelectScreen.targetGroup.size() : -1);
                }
                sb.append(",\"endTurnBtn\":").append(AbstractDungeon.overlayMenu.endTurnButton.enabled);
            }
        } catch (Throwable t) {
            sb.append(",\"err\":\"").append(t).append("\"");
        }
        return sb.append('}').toString();
    }

    private static void settleFrame(Pending p) {
        if (System.currentTimeMillis() > p.deadline) {
            logger.error("SETTLE-TIMEOUT diag: " + diagnostics());
            fail(p, "timeout", "settle timeout on screen: " + Screens.current());
            return;
        }
        framesSinceTask++;
        if (framesSinceTask < 2) {
            return; // 动作后至少让出 2 帧
        }
        if (!Stability.quiescent()) {
            resetSettle();
            return;
        }
        long fp = Stability.fingerprint();
        if (fp != lastFingerprint) {
            lastFingerprint = fp;
            stableFrames = 1;
            return;
        }
        stableFrames++;
        if (stableFrames < CFG.settleStableFrames) {
            return;
        }
        // 终态约束（new_run 等待进入地牢 / quit_run 等待回到菜单）
        if (p.terminalScreen != null) {
            String cur = Screens.current();
            if ("any-dungeon".equals(p.terminalScreen) && Screens.inDungeon()
                    && !Screens.TRANSIT.equals(cur)) {
                // ok
            } else if ("menu".equals(p.terminalScreen) && Screens.MENU.equals(cur)) {
                // ok
            } else {
                if (Screens.TRANSIT.equals(cur)) {
                    resetSettle();
                    return;
                }
                if ("any-dungeon".equals(p.terminalScreen) && !Screens.inDungeon()) {
                    return; // 还在过场（菜单→地牢）
                }
                if ("menu".equals(p.terminalScreen) && !Screens.MENU.equals(cur)) {
                    return; // 还在返回菜单途中
                }
            }
        }
        finishInGameThread(p);
    }

    private static void finishInGameThread(Pending p) {
        PENDING.compareAndSet(p, null); // 先释放，再 complete（顺序保证下一条命令可入队）
        JsonObject resp;
        try {
            resp = Commands.respond(p.request);
        } catch (Exception e) {
            logger.error("respond build failed", e);
            resp = Commands.error(p.request, "internal", String.valueOf(e));
        }
        reset();
        p.future.complete(resp);
    }

    private static void fail(Pending p, String type, String msg) {
        PENDING.compareAndSet(p, null);
        reset();
        p.future.complete(Commands.error(p.request, type, msg));
    }

    private static void reset() {
        state = State.IDLE;
        tasks = new ArrayDeque<>();
        resetSettle();
    }

    private static void resetSettle() {
        lastFingerprint = Stability.fingerprint();
        stableFrames = 0;
    }
}
