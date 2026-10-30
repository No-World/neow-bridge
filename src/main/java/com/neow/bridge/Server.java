package com.neow.bridge;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

/**
 * 传输层：本地 TCP（默认，便于多实例与外部进程管理）或 stdio。
 *
 * 分帧：UTF-8 JSON，一行一条（\n 结尾）。
 * 环境变量：NEOW_BRIDGE_PORT（默认 37771）、NEOW_BRIDGE_BIND（默认 127.0.0.1）、
 *          NEOW_BRIDGE_STDIO=1 启用 stdio 模式。
 */
public final class Server {

    private static final Logger logger = LogManager.getLogger(Server.class.getName());
    private static final Gson GSON = new Gson();

    private Server() {
    }

    public static void start() {
        boolean stdio = "1".equals(System.getenv("NEOW_BRIDGE_STDIO"));
        if (stdio) {
            Thread t = new Thread(Server::stdioLoop, "neow-bridge-stdio");
            t.setDaemon(true);
            t.start();
            return;
        }
        Thread t = new Thread(Server::tcpLoop, "neow-bridge-tcp");
        t.setDaemon(true);
        t.start();
    }

    // ------------------------------------------------------------------
    // TCP
    // ------------------------------------------------------------------

    private static void tcpLoop() {
        int port = intEnv("NEOW_BRIDGE_PORT", 37771);
        String bind = env("NEOW_BRIDGE_BIND", "127.0.0.1");
        ServerSocket server;
        try {
            server = new ServerSocket(port, 8, InetAddress.getByName(bind));
            logger.info("NeowBridge listening on {}:{}", bind, port);
        } catch (IOException e) {
            logger.error("NeowBridge failed to bind " + bind + ":" + port, e);
            return;
        }
        while (true) {
            try {
                Socket client = server.accept();
                Thread t = new Thread(() -> serve(client), "neow-bridge-client");
                t.setDaemon(true);
                t.start();
            } catch (IOException e) {
                logger.error("accept failed", e);
            }
        }
    }

    private static void serve(Socket client) {
        logger.info("client connected: {}", client.getRemoteSocketAddress());
        try {
            client.setTcpNoDelay(true);
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8));
            StringBuilder out = new StringBuilder();
            String line;
            while ((line = in.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) {
                    continue;
                }
                JsonObject request;
                try {
                    request = new JsonParser().parse(line).getAsJsonObject();
                } catch (Exception e) {
                    write(out, client, Commands.error(null, "bad_request", "invalid JSON: " + e.getMessage()));
                    continue;
                }
                long timeoutMs = Bridge.CFG.timeoutMs + 5_000;
                // ping 在忙时代答（服务器线程），保证卡死时也有透视
                String cmd0 = Actions.str(request, "cmd");
                // diag：线程栈转储，服务器线程直接代答（游戏线程卡死时也能用）
                if ("diag".equals(cmd0)) {
                    JsonObject resp = new JsonObject();
                    resp.addProperty("id", request.has("id") ? request.get("id").getAsInt() : 0);
                    resp.addProperty("ok", true);
                    JsonObject r = new JsonObject();
                    r.addProperty("bridge_state", Bridge.diagState());
                    for (java.util.Map.Entry<Thread, StackTraceElement[]> e : Thread.getAllStackTraces().entrySet()) {
                        String tn = e.getKey().getName();
                        if (!(tn.equals("LWJGL Application") || tn.equals("main") || tn.contains("Sound") || tn.contains("Music"))) {
                            continue;
                        }
                        JsonArray fr = new JsonArray();
                        StackTraceElement[] st = e.getValue();
                        for (int k = Math.max(0, st.length - 30); k < st.length; k++) {
                            fr.add(new com.google.gson.JsonPrimitive(st[k].toString()));
                        }
                        r.add("thread[" + tn + "]", fr);
                    }
                    resp.add("result", r);
                    write(out, client, resp);
                    continue;
                }
                if ("ping".equals(cmd0) && Bridge.busy()) {
                    JsonObject resp = new JsonObject();
                    resp.addProperty("id", request.has("id") ? request.get("id").getAsInt() : 0);
                    resp.addProperty("ok", true);
                    JsonObject r = new JsonObject();
                    r.addProperty("busy", true);
                    r.addProperty("protocol_version", Bridge.PROTOCOL_VERSION);
                    r.addProperty("bridge_version", Bridge.BRIDGE_VERSION);
                    r.addProperty("bridge_state", Bridge.diagState());
                    resp.add("result", r);
                    write(out, client, resp);
                    continue;
                }
                JsonObject response = Bridge.submit(request, timeoutMs);
                write(out, client, response);
                if ("kill_game".equals(Actions.str(request, "cmd"))) {
                    return;
                }
            }
        } catch (IOException e) {
            logger.info("client disconnected: {}", e.getMessage());
        } finally {
            try {
                client.close();
            } catch (IOException ignored) {
            }
        }
    }

    private static synchronized void write(StringBuilder sb, Socket client, JsonObject obj) {
        sb.setLength(0);
        sb.append(GSON.toJson(obj)).append('\n');
        try {
            client.getOutputStream().write(sb.toString().getBytes(StandardCharsets.UTF_8));
            client.getOutputStream().flush();
        } catch (IOException e) {
            logger.error("write failed", e);
        }
    }

    // ------------------------------------------------------------------
    // stdio
    // ------------------------------------------------------------------

    private static void stdioLoop() {
        logger.info("NeowBridge stdio mode");
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        PrintStream out = System.out;
        String line;
        try {
            while ((line = in.readLine()) != null) {
                line = line.trim();
                if (line.isEmpty()) {
                    continue;
                }
                JsonObject request;
                try {
                    request = new JsonParser().parse(line).getAsJsonObject();
                } catch (Exception e) {
                    out.println(GSON.toJson(
                            Commands.error(null, "bad_request", "invalid JSON: " + e.getMessage())));
                    out.flush();
                    continue;
                }
                JsonObject response = Bridge.submit(request, Bridge.CFG.timeoutMs + 5_000);
                out.println(GSON.toJson(response));
                out.flush();
            }
        } catch (IOException e) {
            logger.error("stdio loop ended", e);
        }
    }

    // ------------------------------------------------------------------

    private static String env(String key, String def) {
        String v = System.getenv(key);
        return v == null || v.isEmpty() ? def : v;
    }

    private static int intEnv(String key, int def) {
        try {
            return Integer.parseInt(System.getenv(key));
        } catch (Exception e) {
            return def;
        }
    }
}
