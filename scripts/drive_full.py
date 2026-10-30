# -*- coding: utf-8 -*-
"""单种子整局压测：从涅奥打到终局（死亡/胜利），逐层报告 + 轨迹落盘。
用法: drive_full.py [seed]     （默认 PMCWKZEF，铁甲战士 A20）
产物: runtime/full_run_trajectory.jsonl（每步 obs摘要+动作）
"""
import json, socket, sys, time

HOST, PORT = "127.0.0.1", 37771
SEED = sys.argv[1] if len(sys.argv) > 1 else "PMCWKZEF"
TRAJ = "runtime/full_run_trajectory.jsonl"

s = socket.create_connection((HOST, PORT), timeout=180)
f = s.makefile("rwb")
_id = [0]
traj = open(TRAJ, "w", encoding="utf-8")

def rpc(cmd, **kw):
    _id[0] += 1
    req = {"id": _id[0], "cmd": cmd}; req.update(kw)
    f.write((json.dumps(req) + "\n").encode()); f.flush()
    return json.loads(f.readline())

def log(*a):
    print(*a); sys.stdout.flush()

def summarize(res):
    obs = res["observation"]
    return {
        "screen": res["info"]["screen"], "act": obs["meta"].get("act"),
        "floor": obs["meta"].get("floor"), "turn": obs["meta"].get("turn"),
        "hp": obs["player"]["hp"], "max_hp": obs["player"]["max_hp"],
        "gold": obs["player"]["gold"], "hand": len(obs.get("hand", [])),
        "deck": obs.get("master_deck", {}).get("count", 0),
        "relics": [r["id"] for r in obs.get("relics", [])],
        "potions": [p and p["id"] for p in obs.get("potions", [])],
    }

def pick(res):
    legal = res["legal_actions"]; scr = res["observation"]["screen"]; t = scr["type"]
    def by(*ts): return [a for a in legal if a["type"] in ts]
    if t == "combat":
        # 简单策略：优先能击杀的攻击，否则第一张可打；没牌打就结束回合
        obs = res["observation"]
        monsters = obs.get("monsters", [])
        plays = [a for a in legal if a["type"] == "play"]
        kills = []
        for a in plays:
            m = monsters[a["target"]] if a.get("target") is not None else None
            c = obs["hand"][a["card_index"]]
            if m and c["type"] == "ATTACK" and c.get("damage", 0) >= m["hp"] - m.get("block", 0) and not m["is_gone"]:
                kills.append(a)
        if kills: return kills[0]
        if plays: return plays[0]
        pots = [a for a in legal if a["type"] == "potion" and "target" not in a]
        if pots: return pots[0]
        return {"type": "end_turn"}
    if t == "map":
        m = by("map")
        # 优先怪物房间（稳定打钱打经验策略，也覆盖战斗路径）
        nodes = {n["index"]: n["type"] for n in res["observation"]["map"]["nodes"]}
        mons = [a for a in m if nodes.get(a["node_index"]) == "MONSTER"]
        return (mons or m or [None])[0] if m else ({"type": "boss"} if by("boss") else None)
    if t == "event": return by("event")[0] if by("event") else None
    if t == "rest": return by("rest")[0] if by("rest") else None
    if t == "chest": return {"type": "chest_open"}
    if t == "shop_room": return {"type": "shop_enter"}
    if t == "shop": return {"type": "cancel"}
    if t == "card_reward":
        p = by("card_pick"); return p[0] if p else {"type": "skip"}
    if t == "combat_reward":
        takes = [a for a in legal if a["type"] == "reward" and a.get("take_indices")]
        if takes:
            # 优先非金币（多覆盖卡牌路径），金币次之
            non_gold = [a for a in takes if a["take_indices"][0] != 0 or True]
            return takes[0]
        return {"type": "reward", "skip": True}
    if t == "boss_relic": return by("relic")[0]
    if t == "hand_select":
        n = scr.get("num_to_select", 1); n = 1 if n in (-1, 0) else n
        return {"type": "hand_select", "indices": list(range(min(n, 3)))}
    if t == "grid_select":
        if scr.get("confirm_up"): return {"type": "grid_select", "indices": []}
        n = scr.get("num", 1); n = 1 if n in (-1, 0) else n
        return {"type": "grid_select", "indices": list(range(min(n, 2)))}
    if t in ("complete", "game_over"): return {"type": "proceed"}
    return legal[0] if legal else None

# 开局
r = rpc("get_state")
res = r.get("result") if r["ok"] else None
if res and res["info"]["in_dungeon"]:
    log("继续已有对局 ...")
else:
    if res and res["observation"]["screen"]["type"] == "game_over":
        rpc("step", action={"type": "proceed"})
    for attempt in range(10):
        r = rpc("new_run", character="IRONCLAD", ascension=20, seed=SEED)
        if r["ok"]: break
        log("等待主菜单...", r.get("error")); time.sleep(5)
    else:
        log("无法开局"); sys.exit(1)
    res = r["result"]

last_floor, last_act, steps, errors = -1, -1, 0, 0
screens_seen = set()
t0 = time.time()
while steps < 2000:
    info = res["info"]; obs = res["observation"]
    t = obs["screen"]["type"]; screens_seen.add(t)
    if obs["meta"].get("floor") != last_floor or obs["meta"].get("act") != last_act:
        last_floor, last_act = obs["meta"].get("floor"), obs["meta"].get("act")
        log("[%5.0fs] 第%s幕 楼层%s | hp %s/%s 金%s 牌组%s 遗物%s" % (
            time.time() - t0, last_act, last_floor, obs["player"]["hp"], obs["player"]["max_hp"],
            obs["player"]["gold"], obs.get("master_deck", {}).get("count"), len(obs.get("relics", []))))
    if res.get("done"):
        m = obs["meta"]
        log("=== 对局结束 === 胜=%s 分数=%s 楼层=%s 总步数=%s 用时%.0fs" % (
            m.get("won"), obs["screen"].get("score"), m.get("floor"), steps, time.time() - t0))
        break
    a = pick(res)
    if a is None:
        r2 = rpc("get_state")
        if r2["ok"] and r2["result"]["legal_actions"]:
            res = r2["result"]; continue
        log("[%5.0fs] 无合法动作于 %s，等待..." % (time.time() - t0, t)); time.sleep(1.5)
        r3 = rpc("get_state")
        if r3["ok"]: res = r3["result"]
        continue
    r = rpc("step", action=a)
    if not r["ok"]:
        errors += 1
        et = r.get("error_type", "")
        if et in ("timeout", "internal"):
            log("!! %s: %s @%s act=%s" % (et, r.get("error"), a, t)); time.sleep(2)
            r2 = rpc("get_state")
            if not r2["ok"]:
                log("桥失去响应，终止"); break
            res = r2["result"]; continue
        if et == "busy":
            time.sleep(2); continue
        # invalid_action：屏幕在合法集生成与执行之间变化 → 重取状态
        r2 = rpc("get_state")
        if r2["ok"]: res = r2["result"]
        else: time.sleep(1)
        continue
    res = r["result"]
    steps += 1
    traj.write(json.dumps({"i": steps, "action": a, **summarize(res)}, ensure_ascii=False) + "\n")
    traj.flush()

log("步数=%s 错误=%s 屏类=%s" % (steps, errors, sorted(screens_seen)))
traj.close()
