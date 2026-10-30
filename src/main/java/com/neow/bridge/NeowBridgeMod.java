package com.neow.bridge;

import basemod.BaseMod;
import basemod.interfaces.PostInitializeSubscriber;
import com.evacipated.cardcrawl.modthespire.lib.SpireInitializer;

/**
 * NeowBridge 模组入口。
 *
 * 开发路线：先 fork CommunicationMod 原样跑通，再逐步替换为统一 gym 协议
 * （docs/protocol.md）与本入口整合。
 */
@SpireInitializer
public class NeowBridgeMod implements PostInitializeSubscriber {

    public static void initialize() {
        new NeowBridgeMod();
    }

    public NeowBridgeMod() {
        BaseMod.subscribe(this);
    }

    @Override
    public void receivePostInitialize() {
        Server.start(); // 游戏就绪后启动外部通信（本地 socket / stdio）
    }
}
