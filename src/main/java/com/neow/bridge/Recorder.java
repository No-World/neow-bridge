package com.neow.bridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.rooms.AbstractRoom;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;

import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

/**
 * 对局记录器：**逐帧**记录规则性状态变化，落盘 jsonl。
 *
 * 设计要点：
 *  - 只记录「规则性字段」（floor/turn/hp/hand/monsters/rng…），跳过动画抖动
 *  - 状态变化就写一条，包含当时的 legal_actions（供事后反推动作）
 *  - **不因 S/L 丢记录**：检测到 floor/turn 回退时写一条 {"type":"sl_marker"} 后继续
 *  - 开关：命令 `record_start` / `record_stop`（不影响正常 step）
 */
public final class Recorder {

    private static final Logger logger = LogManager.getLogger(Recorder.class.getName());

    private static BufferedWriter out;
    private static String path;
    private static String lastHash = "";
    private static int seq = 0;
    private static long startedAt = 0;
    private static String lastFloor = "";
    private static String lastTurn = "";
    private static final ArrayList<String> slMarks = new ArrayList<>();

    private Recorder() {
    }

    public static synchronized boolean active() {
        return out != null;
    }

    public static synchronized JsonObject start(String file) {
        JsonObject r = new JsonObject();
        try {
            if (out != null) {
                r.addProperty("already", true);
                r.addProperty("path", path);
                return r;
            }
            // 路径解析：相对路径以工作目录为基准；若 cwd 已在 runtime/ 下且
            // file 也以 "runtime/" 开头，则去掉该前缀（避免 runtime/runtime/）
            File f = new File(file);
            if (!f.isAbsolute()) {
                String cwd = System.getProperty("user.dir");
                String rel = file.replace('/', File.separatorChar);
                String rtPrefix = "runtime" + File.separator;
                String cwdNorm = cwd.replace('\\', '/');
                if (cwdNorm.endsWith("/runtime") && rel.startsWith(rtPrefix)) {
                    rel = rel.substring(rtPrefix.length());
                }
                f = new File(cwd, rel);
            }
            if (f.getParentFile() != null) {
                //noinspection ResultOfMethodCallIgnored
                f.getParentFile().mkdirs();
            }
            out = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(f), StandardCharsets.UTF_8));
            path = f.getAbsolutePath();
            seq = 0;
            lastHash = "";
            lastFloor = "";
            lastTurn = "";
            slMarks.clear();
            startedAt = System.currentTimeMillis();
            r.addProperty("started", true);
            r.addProperty("path", path);
        } catch (Throwable t) {
            r.addProperty("error", String.valueOf(t));
        }
        return r;
    }

    public static synchronized JsonObject stop() {
        JsonObject r = new JsonObject();
        try {
            if (out == null) {
                r.addProperty("active", false);
                return r;
            }
            out.flush();
            out.close();
            out = null;
            r.addProperty("stopped", true);
            r.addProperty("path", path);
            r.addProperty("records", seq);
            JsonArray marks = new JsonArray();
            for (String m : slMarks) {
                marks.add(m);
            }
            r.add("sl_marks", marks);
        } catch (Throwable t) {
            r.addProperty("error", String.valueOf(t));
        }
        return r;
    }

    /** 每帧调用（Bridge.onPostUpdate 里）。 */
    public static synchronized void frame() {
        if (out == null) {
            return;
        }
        try {
            if (!Screens.inDungeon()) {
                return;
            }
            JsonObject obs = Obs.observation(Bridge.CFG);
            JsonObject info = Screens.info();
            String h = hash(obs, info);
            if (h.equals(lastHash)) {
                return;
            }
            lastHash = h;
            seq++;

            JsonObject rec = new JsonObject();
            rec.addProperty("i", seq);
            rec.addProperty("t", (System.currentTimeMillis() - startedAt) / 1000.0);
            rec.add("state", Obs.snapshotForTrace(obs, info));
            JsonArray legal = new JsonArray();
            try {
                legal = Legal.legalActions();
            } catch (Throwable ignored) {
            }
            rec.add("legal", legal);
            write(rec);

            // S/L 检测：**只标记，不丢记录**
            String floor = String.valueOf(info.has("floor") ? info.get("floor").getAsInt() : -1);
            JsonObject meta = obs.has("meta") ? obs.getAsJsonObject("meta") : new JsonObject();
            String turn = meta.has("turn") && !meta.get("turn").isJsonNull()
                    ? meta.get("turn").getAsString() : "";
            if (!lastFloor.isEmpty() && floor.compareTo(lastFloor) < 0) {
                markSL("floor 回退 " + lastFloor + " -> " + floor);
            } else if (!lastTurn.isEmpty() && turn.compareTo(lastTurn) < 0) {
                markSL("turn 回退 " + lastTurn + " -> " + turn + "（floor " + floor + "）");
            }
            lastFloor = floor;
            if (!turn.isEmpty()) {
                lastTurn = turn;
            }
        } catch (Throwable t) {
            logger.error("recorder frame failed", t);
        }
    }

    private static void markSL(String reason) {
        try {
            JsonObject m = new JsonObject();
            m.addProperty("type", "sl_marker");
            m.addProperty("i", seq);
            m.addProperty("reason", reason);
            write(m);
            slMarks.add("#" + seq + " " + reason);
            logger.warn("S/L 标记 #{}: {}", seq, reason);
        } catch (Throwable ignored) {
        }
    }

    private static void write(JsonObject o) {
        try {
            out.write(o.toString());
            out.write("\n");
            out.flush();
        } catch (Throwable ignored) {
        }
    }

    private static String hash(JsonObject obs, JsonObject info) {
        StringBuilder sb = new StringBuilder();
        sb.append(info).append('|');
        sb.append(obs.has("meta") ? obs.get("meta") : "").append('|');
        sb.append(obs.has("player") ? obs.get("player") : "").append('|');
        sb.append(obs.has("hand") ? obs.get("hand") : "").append('|');
        sb.append(obs.has("monsters") ? obs.get("monsters") : "").append('|');
        sb.append(obs.has("screen") ? obs.get("screen") : "").append('|');
        sb.append(obs.has("draw_pile") ? obs.get("draw_pile") : "").append('|');
        sb.append(obs.has("discard_pile") ? obs.get("discard_pile") : "");
        return sb.toString();
    }
}
