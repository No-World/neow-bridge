package com.neow.bridge;

/**
 * 外部通信入口：本地 socket（默认，便于多实例）或 stdio，
 * 收发 docs/protocol.md 定义的统一 gym 协议 JSON。
 *
 * TODO: fork CommunicationMod 后，以其收发循环为底改造。
 */
public final class Server {

    private Server() {
    }

    public static void start() {
        // TODO: 监听 + 协议编解码 + 动作回放
    }
}
