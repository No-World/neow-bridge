package com.neow.bridge;

import java.util.HashMap;
import java.util.Map;

/** 遗物计数器周期表：周期类遗物的满值（counter 到达 period 时触发）。
 *  与 neow-sim encoding.rs 的 RELIC_PERIOD 对齐。
 */
public final class RelicPeriods {
    private static final Map<String, Integer> PERIODS = new HashMap<>();

    static {
        PERIODS.put("Shuriken", 3);
        PERIODS.put("Kunai", 3);
        PERIODS.put("Ornamental Fan", 3);
        PERIODS.put("Pen Nib", 10);
        PERIODS.put("Nunchaku", 10);
        PERIODS.put("Incense Burner", 6);
        PERIODS.put("Sundial", 3);
        PERIODS.put("Happy Flower", 3);
        PERIODS.put("MawBank", 3); // 每 3 个节点 +12 金（简化）
        PERIODS.put("Pocketwatch", 3); // 无伤 3 回合抽 3
    }

    public static int getPeriod(String relicId) {
        return PERIODS.getOrDefault(relicId, 0);
    }

    public static boolean isPeriodic(String relicId) {
        return PERIODS.containsKey(relicId);
    }
}
