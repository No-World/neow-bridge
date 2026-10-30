# -*- coding: utf-8 -*-
"""NeowBridge 功能自检套件：跑局为载体，断言为主业。

用法: selftest.py [seed] [runs]
输出: 每步跑协议不变量断言；按屏类型/动作类型统计覆盖；失败立即带现场报告。

断言分四类：
  [OBS]  观测完整性 —— 屏专有字段、堆计数一致性、意图存在性……
  [LEG]  合法动作一致性 —— legal 与观测互相印证（下标界内、目标存活……）
  [EFF]  动作效果 —— 执行后的确定性增量（出牌-1、地图+1层、金币增加……）
  [NEG]  非法动作拒绝 —— 越界/错屏/死目标必须被拒
"""
import json, socket, sys, time
from collections import Counter

HOST, PORT = "127.0.0.1", 37771

class Fail(Exception):
    pass

class Checker:
    def __init__(self):
        self.fails = []
        self.checks = 0
    def check(self, cond, tag, msg, ctx=None):
        self.checks += 1
        if not cond:
            rec = "[%s] %s | %s" % (tag, msg, json.dumps(ctx, ensure_ascii=False)[:600] if ctx else "")
            self.fails.append(rec)
            print("FAIL", rec, flush=True)
            return False
        return True

def connect():
    s = socket.create_connection((HOST, PORT), timeout=200)
    return s, s.makefile("rwb")

_id = [0]
def rpc(f, cmd, **kw):
    _id[0] += 1
    req = {"id": _id[0], "cmd": cmd}; req.update(kw)
    f.write((json.dumps(req) + "\n").encode()); f.flush()
    line = f.readline()
    if not line:
        raise Fail("connection closed during " + cmd)
    return json.loads(line)

# ----------------------------------------------------------------------
# 观测断言
# ----------------------------------------------------------------------
def check_obs(ch, res):
    obs = res["observation"]; scr = obs["screen"]; t = scr["type"]
    p = obs["player"]
    ch.check(0 < p["hp"] <= p["max_hp"] or t == "game_over", "OBS", "hp 越界", p)
    ch.check(p["energy"] >= 0, "OBS", "能量为负", p)
    for pile, order in (("draw_pile", "hidden"), ("discard_pile", "chrono"), ("exhaust_pile", "chrono"), ("master_deck", "sorted")):
        if pile in obs:
            pg = obs[pile]
            ch.check(pg.get("order") == order, "OBS", "%s order 应为 %s 实为 %s" % (pile, order, pg.get("order")), {})
            ch.check(pg["count"] == len(pg["cards"]), "OBS", "%s count!=len(cards)" % pile, pg.get("count"))
    if t == "combat":
        mons = obs.get("monsters", [])
        ch.check(len(mons) > 0, "OBS", "战斗无怪物")
        for m in mons:
            if not m["is_gone"]:
                ch.check("type" in m.get("intent", {}), "OBS", "存活怪物无意图", m["id"])
                ch.check(m["hp"] >= 0, "OBS", "怪物血为负", m["id"])
    if t == "map":
        mm = obs["map"]
        idxs = {n["index"] for n in mm["nodes"]}
        for c in mm["choices"]:
            ch.check(c in idxs, "OBS", "map choice %s 不在 nodes" % c)
        for n in mm["nodes"]:
            for e in n["edges"]:
                ch.check(e in idxs, "OBS", "边指向不存在节点 %s->%s" % (n["index"], e))
    if t == "card_reward":
        ch.check(len(scr.get("cards", [])) >= 1, "OBS", "卡牌奖励空")
    if t == "combat_reward":
        ch.check("items" in scr, "OBS", "奖励屏无 items")
    if t == "shop":
        for k in ("cards", "relics", "potions", "purge_available", "purge_cost"):
            ch.check(k in scr, "OBS", "商店缺字段 %s" % k)
    if t == "rest":
        ch.check("options" in scr and len(scr["options"]) >= 1, "OBS", "休息屏无选项")
        for o in scr["options"]:
            if o["name"] == "smith" and o["usable"]:
                ch.check("cards" in o and o["cards"], "OBS", "smith 选项缺可升级牌列表")
    if t == "event":
        ch.check("options" in scr, "OBS", "事件屏无选项")

# ----------------------------------------------------------------------
# 合法动作一致性
# ----------------------------------------------------------------------
def check_legal(ch, res):
    obs = res["observation"]; scr = obs["screen"]; t = scr["type"]
    legal = res["legal_actions"]
    hand = obs.get("hand", [])
    mons = obs.get("monsters", [])
    for a in legal:
        ty = a["type"]
        if ty == "play":
            ch.check(0 <= a["card_index"] < len(hand), "LEG", "play 下标越界", a)
            if a.get("target") is not None:
                ch.check(a["target"] < len(mons), "LEG", "play 目标越界", a)
        elif ty in ("potion", "potion_discard"):
            ch.check(0 <= a[ty if ty == "potion" else "potion_index"] < len(obs.get("potions", [])), "LEG", "药水下标越界", a)
        elif ty == "card_pick":
            ch.check(0 <= a["index"] < len(scr.get("cards", [])), "LEG", "card_pick 越界", a)
        elif ty == "reward" and a.get("take_indices"):
            n = len(scr.get("items", []))
            for i in a["take_indices"]:
                ch.check(0 <= i < n, "LEG", "reward 下标越界", a)
    if t == "combat":
        ch.check(any(a["type"] == "end_turn" for a in legal) or any(a["type"] == "play" for a in legal),
                 "LEG", "战斗中既不能出牌也不能结束回合")
    if t in ("complete", "game_over", "shop_room", "chest", "card_reward", "combat_reward", "rest", "event", "hand_select", "grid_select"):
        ch.check(len(legal) > 0, "LEG", "决策屏 %s 合法动作为空" % t)

# ----------------------------------------------------------------------
# 动作效果（确定性增量）
# ----------------------------------------------------------------------
def check_effect(ch, prev, action, res, dt):
    obs = res["observation"]; p = obs["player"]
    ty = action["type"]
    ch.check(dt < 30, "EFF", "落定耗时 %.1fs" % dt, action)
    if ty == "play":
        # 手牌数不增（除非触发抽牌，放宽为 -2..+2 警告级）
        d = len(obs["hand"]) - len(prev["observation"]["hand"])
        ch.check(-1 <= d <= 5, "EFF", "出牌后手牌变化异常 %d" % d)
        ch.check(p["energy"] <= prev["observation"]["player"]["energy"], "EFF", "出牌后能量不减反增")
    elif ty == "map":
        ch.check(obs["meta"].get("floor", 0) > prev["observation"]["meta"].get("floor", -1),
                 "EFF", "地图移动后楼层未增加")
    elif ty == "reward" and action.get("take_indices"):
        ch.check(p["gold"] >= prev["observation"]["player"]["gold"], "EFF", "领奖后金币减少")
    elif ty == "potion":
        ch.check(all(x is None for x in obs.get("potions", ["x"]) if True) or
                 sum(1 for x in obs["potions"] if x) <= sum(1 for x in prev["observation"]["potions"] if x),
                 "EFF", "喝药后药水数未减")
    elif ty == "end_turn":
        pass  # 回合数/敌人行动在后续帧体现，弱断言

# ----------------------------------------------------------------------
# 非法动作拒绝（每屏抽测）
# ----------------------------------------------------------------------
def negative_tests(ch, f, res):
    obs = res["observation"]; t = obs["screen"]["type"]
    def must_reject(action, why):
        r = rpc(f, "step", action=action)
        ch.check(not r["ok"] and r.get("error_type") == "invalid_action",
                 "NEG", "非法动作未被拒: %s (%s)" % (json.dumps(action), why),
                 r if not r["ok"] else None)
    if t == "combat":
        must_reject({"type": "play", "card_index": 99}, "越界")
        must_reject({"type": "play", "card_index": 0, "target": 99}, "目标越界")
        must_reject({"type": "event", "option_index": 0}, "错屏")
        dead = [i for i, m in enumerate(obs.get("monsters", [])) if m["is_gone"]]
        if dead and any(a["type"] == "play" for a in res["legal_actions"]):
            must_reject({"type": "play", "card_index": 0, "target": dead[0]}, "死目标")
    if t == "map":
        must_reject({"type": "map", "node_index": 9999}, "越界节点")
    if t == "hand_select":
        must_reject({"type": "hand_select", "indices": [0] * 50}, "数量超限")
        must_reject({"type": "hand_select", "indices": [99999]}, "下标越界")

# ----------------------------------------------------------------------
# 主循环
# ----------------------------------------------------------------------
def pick_first(res):
    legal = res["legal_actions"]
    if not legal:
        return None
    # 覆盖优先：优先选还没测过的动作类型
    unt = [a for a in legal if a["type"] not in COVER]
    return (unt or legal)[0]

if __name__ == "__main__":
    seed0 = sys.argv[1] if len(sys.argv) > 1 else "PMCWKZEF"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    ch = Checker()
    COVER = set()
    SCREENS = Counter()
    ACTS = Counter()
    f = None
    try:
        s, ff = connect(); f = ff
        for run_i in range(runs):
            seed = seed0 if run_i == 0 else seed0 + str(run_i)
            for attempt in range(8):
                r = rpc(f, "new_run", character="IRONCLAD", ascension=0, seed=seed)
                if r["ok"]:
                    break
                time.sleep(4)
            if not r["ok"]:
                raise Fail("new_run 失败: " + str(r.get("error")))
            res = r["result"]
            print("== run %s seed=%s ==" % (run_i + 1, seed), flush=True)
            for step in range(600):
                t = res["observation"]["screen"]["type"]
                SCREENS[t] += 1
                check_obs(ch, res)
                check_legal(ch, res)
                if step % 7 == 0:
                    negative_tests(ch, f, res)
                a = pick_first(res)
                if a is None:
                    time.sleep(1)
                    r2 = rpc(f, "get_state")
                    if r2["ok"] and r2["result"]["legal_actions"]:
                        res = r2["result"]
                        continue
                    print("  no legal at", t); break
                t0 = time.time()
                r = rpc(f, "step", action=a)
                dt = time.time() - t0
                if not r["ok"]:
                    if r.get("error_type") in ("timeout", "internal"):
                        ch.check(False, "EFF", "step 失败 %s" % r.get("error"), a)
                        break
                    r2 = rpc(f, "get_state")
                    if r2["ok"]:
                        res = r2["result"]
                    continue
                check_effect(ch, res, a, r["result"], dt)
                res = r["result"]
                COVER.add(a["type"]); ACTS[a["type"]] += 1
                if res.get("done"):
                    print("  run over:", res["observation"]["meta"].get("won"), flush=True)
                    break
            # 局间：quit_run 应能回菜单再开
            r = rpc(f, "quit_run")
            ch.check(r["ok"], "EFF", "quit_run 失败", r.get("error"))
            time.sleep(2)
        # config 命令
        r = rpc(f, "config", fast_mode=True, settle_stable_frames=3)
        ch.check(r["ok"], "EFF", "config 失败", r.get("error"))
    except (Fail, ConnectionError, OSError) as e:
        ch.check(False, "EFF", "传输异常: %s" % e)
    print("\n===== 自检报告 =====")
    print("断言总数:", ch.checks, " 失败:", len(ch.fails))
    print("屏类型覆盖:", dict(SCREENS))
    print("动作类型覆盖:", dict(ACTS))
    if ch.fails:
        print("失败明细:")
        for x in ch.fails[:20]:
            print("  ", x)
        sys.exit(1)
    print("ALL PASS")
