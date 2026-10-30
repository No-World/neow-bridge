# -*- coding: utf-8 -*-
"""通用深驱动：从任意状态起，按'首选合法动作'策略走到战斗结束，验证各类屏。"""
import json, socket, sys, time

s = socket.create_connection(("127.0.0.1", 37771), timeout=180)
f = s.makefile("rwb")
_id = [0]

def rpc(cmd, **kw):
    _id[0] += 1
    req = {"id": _id[0], "cmd": cmd}; req.update(kw)
    f.write((json.dumps(req) + "\n").encode()); f.flush()
    return json.loads(f.readline())

def step(a):
    return rpc("step", action=a)

res = rpc("get_state")["result"]
if not res["info"]["in_dungeon"]:
    res = rpc("new_run", character="IRONCLAD", ascension=20, seed="PMCWKZEF")["result"]

def pick(res):
    """按优先级选一个动作"""
    legal = res["legal_actions"]; scr = res["observation"]["screen"]; t = scr["type"]
    def by_type(*ts):
        return [a for a in legal if a["type"] in ts]
    if t == "combat":
        plays = [a for a in legal if a["type"] == "play"]
        if plays: return plays[0]
        pots = [a for a in legal if a["type"] == "potion"]
        if pots: return pots[0]
        return {"type": "end_turn"}
    if t == "map":
        m = by_type("map")
        return m[0] if m else {"type": "boss"}
    if t == "event":
        return by_type("event")[0]
    if t == "rest":
        return by_type("rest")[0]
    if t == "chest":
        return {"type": "chest_open"}
    if t == "shop_room":
        return {"type": "shop_enter"}
    if t == "shop":
        return {"type": "cancel"}
    if t == "card_reward":
        picks = by_type("card_pick")
        return picks[0] if picks else {"type": "skip"}
    if t == "combat_reward":
        takes = [a for a in legal if a["type"] == "reward" and a.get("take_indices")]
        if takes:
            best = max(takes, key=lambda a: a["take_indices"][0])
            return best
        return {"type": "reward", "skip": True}
    if t == "boss_relic":
        return by_type("relic")[0]
    if t == "hand_select":
        n = scr.get("num_to_select", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "hand_select", "indices": list(range(min(n, 5)))}
    if t == "grid_select":
        if scr.get("confirm_up"):
            return {"type": "grid_select", "indices": []}
        n = scr.get("num", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "grid_select", "indices": list(range(min(n, 3)))}
    if t in ("complete", "game_over"):
        return {"type": "proceed"}
    return legal[0] if legal else None

hist = []
import time as _t
for i in range(160):
    t = res["observation"]["screen"]["type"]
    a = pick(res)
    if a is None:
        # 过渡态：等待后重取状态
        _t.sleep(1.0)
        r2 = rpc("get_state")
        if r2["ok"]:
            res = r2["result"]
            if res["legal_actions"]:
                continue
        print("no legal action at", t); break
    r = step(a)
    if not r["ok"]:
        print("ERR:", r.get("error"), "at", t, "action", a); break
    res = r["result"]
    obs = res["observation"]; info = res["info"]
    hist.append(t)
    if res["done"]:
        print("RUN OVER won=", obs["meta"].get("won")); break
    if i % 10 == 0 or t != info["screen"]:
        print("step %3d %-12s -> %-12s floor=%s hp=%s" % (
            i, t, info["screen"], obs["meta"].get("floor"), obs["player"]["hp"]))

print("screens seen:", sorted(set(hist)))
print("DRIVE OK (steps=%d)" % len(hist))
