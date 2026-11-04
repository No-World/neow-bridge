package com.neow.bridge.patch;

import com.evacipated.cardcrawl.modthespire.lib.SpirePatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePostfixPatch;
import com.evacipated.cardcrawl.modthespire.lib.SpirePrefixPatch;
import com.megacrit.cardcrawl.cards.AbstractCard;
import com.megacrit.cardcrawl.cards.CardGroup;
import com.megacrit.cardcrawl.dungeons.AbstractDungeon;
import com.megacrit.cardcrawl.random.Random;

import java.util.ArrayList;
import java.util.List;

/**
 * 洗牌追踪：hook `CardGroup.shuffle(Random)`，输出
 *   洗牌前顺序 / shuffleRng 的 counter 与 XS128 双字状态 / 本次实际用到的 nextLong / 洗牌后顺序
 *
 * 目的：对拍「同种子下第 N 次洗牌的排列」。引擎侧复算时必须用同一个 nextLong，
 * 而仅凭 counter 无法确认（流可能被别处推进）。这里直接打出真实种子。
 *
 * 开关：环境变量 `NEOW_TRACE_SHUFFLE=1`（与引擎侧同名，方便同一轮对拍同时打开）。
 *
 * ⚠️ 关键：必须在 `__instance.shuffle(rng)` **之前**取 rng.randomLong() 的实际值。
 * 做法是 Prefix 里先快照 counter + 状态，Postfix 里根据状态差反推？——不必要：
 * 直接改成在 Prefix 里读 counter，然后由 Postfix 输出「counter 增量」，
 * 并在 Prefix 里用 getState(0)/getState(1) 记录洗牌前的 XS128 状态。
 * 消费的 nextLong 可由「洗牌后状态」推出，但更简单的是：Prefix 里直接算一次
 * 状态推进（不消费真实流），把那个 long 打出来作为参考种子。
 */
public class ShuffleTrace {

    private static final boolean ON = "1".equals(System.getenv("NEOW_TRACE_SHUFFLE"));

    static {
        System.out.println("[SHUF] ShuffleTrace loaded, enabled=" + ON);
    }

    /** 用 XS128 状态手工推进一次，得到 nextLong()（不触碰真实流）。 */
    private static long peekNextLong(long s0, long s1) {
        long n1 = s0;
        long n0 = s1;
        n1 ^= n1 << 23;
        long newS1 = n1 ^ n0 ^ n1 >>> 17 ^ n0 >>> 26;
        return newS1 + n0;
    }

    private static String ids(List<AbstractCard> group) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < group.size(); i++) {
            if (i > 0) sb.append(",");
            sb.append(group.get(i).cardID);
        }
        return sb.append("]").toString();
    }

    @SpirePatch(clz = CardGroup.class, method = "shuffle", paramtypez = {Random.class})
    public static class ShuffleWithRng {

        private static final ThreadLocal<long[]> SNAP = new ThreadLocal<long[]>();

        @SpirePrefixPatch
        public static void Prefix(CardGroup __instance, Random rng) {
            if (!ON) return;
            try {
                long s0 = rng.random.getState(0);
                long s1 = rng.random.getState(1);
                SNAP.set(new long[]{rng.counter, s0, s1, peekNextLong(s0, s1)});
                System.out.println("[SHUF] pre counter=" + rng.counter
                        + " state0=" + s0 + " state1=" + s1
                        + " nextLong=" + peekNextLong(s0, s1)
                        + " n=" + __instance.size()
                        + " order=" + ids(__instance.group));
            } catch (Throwable t) {
                System.out.println("[SHUF] prefix error: " + t);
            }
        }

        @SpirePostfixPatch
        public static void Postfix(CardGroup __instance, Random rng) {
            if (!ON) return;
            try {
                long[] snap = SNAP.get();
                SNAP.remove();
                long dc = snap == null ? -1 : rng.counter - snap[0];
                System.out.println("[SHUF] post counter=" + rng.counter
                        + " delta=" + dc
                        + " n=" + __instance.size()
                        + " order=" + ids(__instance.group));
            } catch (Throwable t) {
                System.out.println("[SHUF] postfix error: " + t);
            }
        }
    }

    /**
     * `CardGroup.shuffle()`（无参）走 `new java.util.Random(AbstractDungeon.shuffleRng.randomLong())`，
     * 这里只是把 shuffleRng 的当前状态也打出来，便于判断「谁在推进这条流」。
     */
    @SpirePatch(clz = CardGroup.class, method = "shuffle", paramtypez = {})
    public static class ShuffleNoArg {
        @SpirePrefixPatch
        public static void Prefix(CardGroup __instance) {
            if (!ON) return;
            try {
                Random r = AbstractDungeon.shuffleRng;
                long s0 = r.random.getState(0);
                long s1 = r.random.getState(1);
                System.out.println("[SHUF] noarg pre counter=" + r.counter
                        + " state0=" + s0 + " state1=" + s1
                        + " nextLong=" + peekNextLong(s0, s1)
                        + " n=" + __instance.size()
                        + " order=" + ids(__instance.group));
            } catch (Throwable t) {
                System.out.println("[SHUF] noarg error: " + t);
            }
        }
    }
}
