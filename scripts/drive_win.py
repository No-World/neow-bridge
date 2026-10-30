# -*- coding: utf-8 -*-
"""铸造"必赢路线"：加强策略打胜利对局，轨迹冻结为可重放的黄金用例。
用法: drive_win.py [seed] [ascension] [max_attempts]
产物: runtime/win_<seed>.jsonl —— 完整动作序列（胜利）
"""
import json, socket, sys, time

HOST, PORT = "127.0.0.1", 37771
SEED = sys.argv[1] if len(sys.argv) > 1 else "PMCWKZEF"
ASC = int(sys.argv[2]) if len(sys.argv) > 2 else 0
MAX_TRY = int(sys.argv[3]) if len(sys.argv) > 3 else 8

def _connect():
    global s, f
    s = socket.create_connection((HOST, PORT), timeout=200)
    f = s.makefile("rwb")

_connect()
_id = [0]

def rpc(cmd, **kw):
    # 断线自动重连（游戏重启/端口闪断），最多 6 次
    global s, f
    for attempt in range(6):
        try:
            _id[0] += 1
            req = {"id": _id[0], "cmd": cmd}; req.update(kw)
            f.write((json.dumps(req) + "\n").encode()); f.flush()
            line = f.readline()
            if not line:
                raise ConnectionError("closed")
            return json.loads(line)
        except (ConnectionError, OSError, json.JSONDecodeError) as e:
            log("  [rpc] %s 重连(%s): %s" % (cmd, attempt + 1, e))
            time.sleep(3)
            try:
                _connect()
            except OSError:
                time.sleep(7)
    raise SystemExit("rpc 不可达: " + cmd)

def log(*a):
    print(*a); sys.stdout.flush()

def pick(res):
    legal = res["legal_actions"]; obs = res["observation"]
    scr = obs["screen"]; t = scr["type"]
    p = obs["player"]; hp, mx = p["hp"], p["max_hp"]
    by = lambda *ts: [a for a in legal if a["type"] in ts]

    if t == "combat":
        monsters = obs.get("monsters", [])
        hand = obs.get("hand", [])
        alive = [m for m in monsters if not m["is_gone"]]
        incoming = sum((m["intent"].get("damage", 0) or 0) * (m["intent"].get("count", 1) or 1)
                       for m in alive if m["intent"]["type"].startswith("ATTACK"))
        plays = [a for a in legal if a["type"] == "play"]
        # 1) 致命一击
        for a in plays:
            c = hand[a["card_index"]]
            m = monsters[a["target"]] if a.get("target") is not None else None
            if m and c["type"] == "ATTACK" and c.get("damage", 0) >= m["hp"] - m.get("block", 0):
                return a
        # 2) 攻击意图来袭且格挡不足 → 防御
        if incoming > p["block"]:
            defends = [a for a in plays if hand[a["card_index"]]["type"] == "SKILL"
                       and hand[a["card_index"]].get("block", 0) > 0]
            if defends:
                return defends[0]
        # 3) 残血喝药
        if hp < mx * 0.45:
            pots = [a for a in legal if a["type"] == "potion" and "target" not in a]
            if pots:
                return pots[0]
        # 4) 打最弱敌人
        attacks = [a for a in plays if hand[a["card_index"]]["type"] == "ATTACK"]
        if attacks:
            best = sorted(attacks, key=lambda a: (
                monsters[a["target"]]["hp"] if a.get("target") is not None else 999))
            return best[0]
        if plays:
            return plays[0]
        pots = [a for a in legal if a["type"] == "potion" and "target" not in a]
        if pots and (hp < mx * 0.7 or len(alive) >= 3):
            return pots[0]
        return {"type": "end_turn"}

    if t == "map":
        m = by("map")
        if not m:
            return {"type": "boss"} if by("boss") else None
        nodes = {n["index"]: n["type"] for n in obs["map"]["nodes"]}
        types = [nodes.get(a["node_index"], "?") for a in m]
        # 血线决定偏好：残血找休息处/事件，满血打怪攒钱，永远绕开精英除非满血
        if hp < mx * 0.5 and "REST" in types:
            return m[types.index("REST")]
        if "REST" in types and hp < mx * 0.75 and len([x for x in types if x == "MONSTER"]) == 0:
            return m[types.index("REST")]
        if hp >= mx * 0.85 and "MONSTER" in types:
            return m[types.index("MONSTER")]
        for pref in ("EVENT", "MONSTER", "?", "TREASURE", "SHOP", "ELITE", "REST"):
            if pref in types:
                if pref == "ELITE" and hp < mx * 0.7:
                    continue
                return m[types.index(pref)]
        return m[0]

    if t == "rest":
        opts = scr.get("options", [])
        for want in (("sleep" if hp < mx * 0.72 else "smith"), "smith", "sleep"):
            for a in by("rest"):
                if a.get("option") == want:
                    return a
        return by("rest")[0] if by("rest") else None

    if t == "shop":
        buys = by("shop_buy_card")
        if buys and p["gold"] > 180:
            return buys[0]
        if by("shop_card_removal") and p["gold"] > 200 and len(obs.get("master_deck", {}).get("cards", [])) > 12:
            return by("shop_card_removal")[0]
        return {"type": "cancel"}

    if t == "shop_room": return {"type": "shop_enter"}
    if t == "chest": return {"type": "chest_open"}
    if t == "event": return by("event")[0] if by("event") else None
    if t == "boss_relic": return by("relic")[0]
    if t == "card_reward":
        picks = by("card_pick")
        if picks:
            cards = scr.get("cards", [])
            def score(i):
                c = cards[i]
                return (c["rarity"] == "RARE", c["type"] == "ATTACK", c["rarity"] == "UNCOMMON")
            return max(picks, key=lambda a: score(a["index"]))
        return {"type": "skip"}
    if t == "combat_reward":
        takes = [a for a in legal if a["type"] == "reward" and a.get("take_indices")]
        return takes[0] if takes else {"type": "reward", "skip": True}
    if t == "hand_select":
        n = scr.get("num_to_select", 1); n = 1 if n in (-1, 0) else n
        return {"type": "hand_select", "indices": list(range(min(n, 3)))}
    if t == "grid_select":
        if scr.get("confirm_up"): return {"type": "grid_select", "indices": []}
        n = scr.get("num", 1); n = 1 if n in (-1, 0) else n
        return {"type": "grid_select", "indices": list(range(min(n, 2)))}
    if t in ("complete", "game_over"): return {"type": "proceed"}
    return legal[0] if legal else None

def play_run(seed, asc, traj_path):
    rpc("config", timeout_ms=180000)
    r = rpc("get_state")
    res = r.get("result") if r["ok"] else None
    if res and res["info"]["in_dungeon"]:
        r = rpc("quit_run"); time.sleep(3)  # 直接弃掉残留局（不管它卡在什么屏）
    if res and res["observation"]["screen"]["type"] == "game_over":
        rpc("step", action={"type": "proceed"}); time.sleep(2)
    for _ in range(10):
        r = rpc("new_run", character="IRONCLAD", ascension=asc, seed=seed)
        if r["ok"]: break
        time.sleep(4)
    else:
        return None, None
    res = r["result"]
    traj = open(traj_path, "w", encoding="utf-8")
    steps, last_fl = 0, -1
    t0 = time.time()
    while steps < 2500:
        obs = res["observation"]; info = res["info"]
        if obs["meta"].get("floor") != last_fl:
            last_fl = obs["meta"].get("floor")
            log("  [%4.0fs] 楼层%s hp%s/%s 金%s 牌%s" % (time.time()-t0, last_fl,
                obs["player"]["hp"], obs["player"]["max_hp"], obs["player"]["gold"],
                obs.get("master_deck", {}).get("count")))
        if res.get("done"):
            won = obs["meta"].get("won")
            log("  == 终局 won=%s 楼层%s 步数%s 用时%.0fs" % (won, obs["meta"].get("floor"), steps, time.time()-t0))
            traj.close()
            return won, steps
        a = pick(res)
        if a is None:
            time.sleep(1.2)
            r2 = rpc("get_state")
            if r2["ok"]: res = r2["result"]
            continue
        r = rpc("step", action=a)
        if not r["ok"]:
            if r.get("error_type") in ("timeout", "internal"):
                log("  !!", r.get("error_type"), str(r.get("error"))[:80]); time.sleep(5)
            r2 = rpc("get_state")
            if not r2["ok"]:
                log("  等待桥恢复..."); time.sleep(10)
                r3 = rpc("get_state")
                if not r3["ok"]:
                    log("  桥失联"); traj.close(); return "DEAD", steps
                res = r3["result"]; continue
            res = r2["result"]
            continue
        res = r["result"]
        steps += 1
        traj.write(json.dumps({"i": steps, "action": a}, ensure_ascii=False) + "\n")
        traj.flush()
    traj.close()
    return False, steps

log("=== 铸造必赢路线: %s A%s, 最多 %s 次 ===" % (SEED, ASC, MAX_TRY))
for attempt in range(1, MAX_TRY + 1):
    # 多次尝试用相邻种子（PMCWKZEF, PMCWKZEF0+…），同一 seed 每局地图相同但策略演化会自然不同
    seed = SEED if attempt == 1 else SEED + str(attempt - 1)
    log("-- 第 %s 次尝试: 种子 %s" % (attempt, seed))
    won, steps = play_run(seed, ASC, "runtime/win_%s.jsonl" % seed)
    if won is None:
        log("无法开局，退出"); sys.exit(1)
    if won is True:
        log("★ 胜利！轨迹: runtime/win_%s.jsonl (%s 步)" % (seed, steps))
        with open("runtime/win_seed.txt", "w") as fh:
            fh.write(seed + "\n" + str(steps) + "\n")
        sys.exit(0)
    if won == "DEAD":
        log("桥失联，退出"); sys.exit(2)
log("用尽尝试次数未获胜")
sys.exit(3)
