# -*- coding: utf-8 -*-
"""驱动一局打到碎心（A20 铁甲战士），落盘完整轨迹。

比 drive_win.py 强的地方：
  - 战斗：精确格挡计算（逐张 Defend 直到覆盖 incoming），先打完能力/0 费，再打攻击
  - 牌组：按「高质量攻击/能力」打分选卡，避免无限膨胀
  - 药水：留给精英/Boss（残血 <35% 或精英战才喝）
  - 地图：优先精英（攒遗物）当血量充裕；残血找休息

用法: drive_heart.py <seed> [asc] [max_steps]
产物: runtime/heart_<seed>.jsonl（与 record_trace.py 同格式）
"""
import json, os, socket, sys, time

PORT = int(os.environ.get("NEOW_BRIDGE_PORT", "45000"))
SEED = sys.argv[1] if len(sys.argv) > 1 else "HEART01"
ASC = int(sys.argv[2]) if len(sys.argv) > 2 else 20
MAX_STEPS = int(sys.argv[3]) if len(sys.argv) > 3 else 2500
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "runtime", "heart_%s.jsonl" % SEED)

s = None
f = None
_id = [0]


def connect():
    global s, f
    s = socket.create_connection(("127.0.0.1", PORT), timeout=200)
    f = s.makefile("rwb")


def rpc(cmd, **kw):
    global s, f
    for _ in range(6):
        try:
            _id[0] += 1
            req = {"id": _id[0], "cmd": cmd}
            req.update(kw)
            f.write((json.dumps(req) + "\n").encode())
            f.flush()
            line = f.readline()
            if not line:
                raise ConnectionError("closed")
            return json.loads(line)
        except (ConnectionError, OSError, json.JSONDecodeError) as e:
            print("  [rpc] %s 重连: %s" % (cmd, e), flush=True)
            time.sleep(3)
            try:
                connect()
            except OSError:
                time.sleep(7)
    raise RuntimeError("rpc failed: " + cmd)


def log(*a):
    print(*a, flush=True)


# ── 卡牌评分（选牌奖励用）──────────────────────────────────
GOOD_ATTACK = {"Bash", "Uppercut", "Carnage", "Bludgeon", "Reaper", "Feed", "Whirlwind",
               "Heavy Blade", "Sword Boomerang", "Twin Strike", "Pommel Strike",
               "Clothesline", "Iron Wave", "Anger", "Headbutt", "Perfected Strike",
               "Thunderclap", "Cleave", "Immolate", "Fiend Fire", "Reckless Charge"}
GOOD_POWER = {"Inflame", "Demon Form", "Feel No Pain", "Dark Embrace", "Barricade",
              "Juggernaut", "Metallicize", "Rupture", "Berserk", "Brutality",
              "Corruption", "Fire Breathing", "Evolve", "Combust", "Rage",
              "Double Tap", "Limit Break", "Battle Trance"}
GOOD_SKILL = {"Shrug It Off", "True Grit", "Flame Barrier", "Ghostly Armor",
              "Impervious", "Second Wind", "Entrench", "Burning Pact",
              "Bloodletting", "Disarm", "Intimidate", "Shockwave", "Spot Weakness"}
BAD = {"Clash", "Flex", "Warcry", "Havoc", "Sentinel", "Sever Soul", "Wild Strike",
       "Dropkick", "Blood for Blood", "Rampage", "Reckless"}


def card_score(c):
    cid = c["id"].split("+")[0]
    if cid in GOOD_POWER:
        return 100
    if cid in GOOD_ATTACK:
        return 85
    if cid in GOOD_SKILL:
        return 70
    if cid in BAD:
        return 5
    rarity = c.get("rarity", "")
    t = c.get("type", "")
    base = {"RARE": 60, "UNCOMMON": 45, "COMMON": 25}.get(rarity, 20)
    if t == "POWER":
        base += 20
    if t == "ATTACK":
        base += 8
    return base


def pick(res):
    legal = res["legal_actions"]
    obs = res["observation"]
    scr = obs["screen"]
    t = scr["type"]
    p = obs["player"]
    hp, mx = p["hp"], p["max_hp"]

    def by(*ts):
        return [a for a in legal if a["type"] in ts]

    if t == "combat":
        monsters = obs.get("monsters", [])
        hand = obs.get("hand", [])
        alive = [m for m in monsters if not m.get("is_gone")]
        incoming = 0
        for m in alive:
            it = m.get("intent") or {}
            if it.get("type", "").startswith("ATTACK"):
                incoming += (it.get("damage") or 0) * (it.get("count") or 1)
        plays = [a for a in plays_all(legal)]
        # 1) 致命一击
        for a in plays:
            c = hand[a["card_index"]]
            m = monsters[a["target"]] if a.get("target") is not None else None
            if m and c.get("type") == "ATTACK" and (c.get("damage") or 0) >= m["hp"] - (m.get("block") or 0):
                return a
        # 2) 先放能力（POWER 不占格挡时机）
        powers = [a for a in plays if hand[a["card_index"]].get("type") == "POWER"]
        if powers and p["energy"] >= 2:
            return powers[0]
        # 3) 格挡不足则补格挡
        if incoming > p["block"]:
            need = incoming - p["block"]
            defs = sorted(
                [a for a in plays if (hand[a["card_index"]].get("block") or 0) > 0],
                key=lambda a: -hand[a["card_index"]].get("block", 0),
            )
            if defs:
                return defs[0]
        # 4) 攻击（打血最少的）
        atk = [a for a in plays if hand[a["card_index"]].get("type") == "ATTACK"]
        if atk:
            atk.sort(key=lambda a: (
                (monsters[a["target"]]["hp"] if a.get("target") is not None else 999),
                -(hand[a["card_index"]].get("damage") or 0),
            ))
            return atk[0]
        # 5) 其它可打的（技能/能力）
        if plays:
            return plays[0]
        # 6) 药水（危急或精英/Boss）
        boss_like = len(alive) == 1 and alive[0]["hp"] > 100
        if hp < mx * 0.35 or (boss_like and hp < mx * 0.6):
            pots = [a for a in legal if a["type"] == "potion"]
            if pots:
                return pots[0]
        return {"type": "end_turn"}

    if t == "map":
        m = by("map")
        if not m:
            return {"type": "boss"} if by("boss") else None
        nodes = {n["index"]: n["type"] for n in obs["map"]["nodes"]}
        types = [nodes.get(a["node_index"], "?") for a in m]
        frac = hp / mx if mx else 1
        if frac < 0.45 and "REST" in types:
            return m[types.index("REST")]
        if frac > 0.75:
            for pref in ("ELITE", "MONSTER", "EVENT", "TREASURE", "SHOP", "REST"):
                if pref in types:
                    return m[types.index(pref)]
        for pref in ("MONSTER", "EVENT", "REST", "TREASURE", "SHOP", "ELITE"):
            if pref in types:
                return m[types.index(pref)]
        return m[0]

    if t == "rest":
        want = "sleep" if hp < mx * 0.65 else "smith"
        for a in by("rest"):
            if a.get("option") == want:
                return a
        return by("rest")[0] if by("rest") else None

    if t == "card_reward":
        picks = by("card_pick")
        if picks:
            cards = scr.get("cards", [])
            best = max(picks, key=lambda a: card_score(cards[a["index"]]))
            sc = card_score(cards[best["index"]])
            deck_n = obs.get("master_deck", {}).get("count", 0)
            # 牌组太大就只拿高分牌
            if deck_n > 26 and sc < 60:
                return {"type": "skip"}
            return best
        return {"type": "skip"}

    if t == "combat_reward":
        takes = [a for a in legal if a["type"] == "reward" and a.get("take_indices")]
        return takes[0] if takes else {"type": "reward", "skip": True}

    if t == "shop_room":
        return {"type": "shop_enter"}
    if t == "shop":
        if by("shop_card_removal") and p["gold"] >= 200 and len(obs.get("master_deck", {}).get("cards", [])) > 12:
            return by("shop_card_removal")[0]
        return {"type": "cancel"}
    if t == "chest":
        return {"type": "chest_open"}
    if t == "event":
        return by("event")[0] if by("event") else None
    if t == "boss_relic":
        return by("relic")[0] if by("relic") else None
    if t == "hand_select":
        n = scr.get("num_to_select", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "hand_select", "indices": list(range(min(n, 3)))}
    if t == "grid_select":
        if scr.get("confirm_up"):
            return {"type": "grid_select", "indices": []}
        n = scr.get("num", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "grid_select", "indices": list(range(min(n, 2)))}
    if t in ("complete", "game_over"):
        return {"type": "proceed"}
    return legal[0] if legal else None


def plays_all(legal):
    return [a for a in legal if a["type"] == "play"]


def snapshot(res):
    """与 tests/conformance/record_trace.py 的 snapshot 同格式（parity_replay 可读）。"""
    obs = res.get("observation")
    if obs is None:
        return None
    info = res.get("info", {})
    p = obs.get("player", {})
    scr = obs.get("screen") or {}

    def payload():
        t = scr.get("type")
        if t == "card_reward":
            return {"type": t, "cards": [c.get("id") for c in (scr.get("cards") or [])]}
        if t == "combat_reward":
            out = []
            for r in (scr.get("items") or []):
                e = {"type": r.get("type")}
                for k in ("gold",):
                    if r.get(k) is not None:
                        e[k] = r[k]
                for k in ("relic_id", "potion_id"):
                    if r.get(k):
                        e["relic" if k == "relic_id" else "potion"] = r[k]
                if r.get("cards"):
                    e["cards"] = [c.get("id") for c in r["cards"]]
                out.append(e)
            return {"type": t, "items": out}
        return None

    return {
        "screen": info.get("screen"),
        "act": info.get("act"),
        "floor": info.get("floor"),
        "turn": obs.get("meta", {}).get("turn"),
        "meta": {
            "seed": obs.get("meta", {}).get("seed"),
            "seed_code": obs.get("meta", {}).get("seed_code"),
            "ascension": obs.get("meta", {}).get("ascension"),
            "character": obs.get("meta", {}).get("character"),
        },
        "player": {
            "hp": p.get("hp"),
            "max_hp": p.get("max_hp"),
            "block": p.get("block"),
            "energy": p.get("energy"),
            "gold": p.get("gold"),
            "powers": p.get("powers"),
        },
        "hand": [(c.get("id"), c.get("upgraded"), c.get("cost_for_turn"))
                 for c in (obs.get("hand") or [])],
        "monsters": [
            {"id": m.get("id"), "hp": m.get("hp"), "max_hp": m.get("max_hp"),
             "block": m.get("block"), "powers": m.get("powers"), "intent": m.get("intent")}
            for m in (obs.get("monsters") or [])
        ],
        "draw_pile_n": (obs.get("draw_pile") or {}).get("count"),
        "draw_order": (None if (obs.get("draw_pile") or {}).get("order") == "hidden"
                       else [c.get("id") for c in ((obs.get("draw_pile") or {}).get("cards") or [])]),
        "discard_pile_n": (obs.get("discard_pile") or {}).get("count"),
        "discard_order": [c.get("id") for c in ((obs.get("discard_pile") or {}).get("cards") or [])],
        "exhaust_pile_n": (obs.get("exhaust_pile") or {}).get("count"),
        "deck": [(c.get("id"), bool(c.get("upgraded")))
                 for c in ((obs.get("master_deck") or {}).get("cards") or [])],
        "screen_payload": payload(),
        "relics": [(r.get("id"), r.get("counter")) for r in (obs.get("relics") or [])],
        "rng": {k: (v.get("counter") if isinstance(v, dict) else v)
                for k, v in (obs.get("rng") or {}).items()},
    }


def main():
    connect()
    r = rpc("config", cheat_draw_order=True)
    if not r.get("ok"):
        log("config 失败:", r.get("error"))
    r = rpc("get_state")
    res = r.get("result") if r.get("ok") else None
    if res and res.get("info", {}).get("in_dungeon"):
        rpc("quit_run")
        time.sleep(3)
    if res and (res.get("observation", {}).get("screen") or {}).get("type") == "game_over":
        rpc("step", action={"type": "proceed"})
        time.sleep(2)
    for _ in range(10):
        r = rpc("new_run", character="IRONCLAD", ascension=ASC, seed=SEED)
        if r.get("ok"):
            break
        time.sleep(4)
    else:
        log("无法开局")
        return 1
    res = r["result"]

    traj = open(OUT, "w", encoding="utf-8")
    traj.write(json.dumps({"i": 0, "action": None, "state": snapshot(res)}, ensure_ascii=False) + "\n")
    traj.flush()

    steps, last_floor, t0 = 0, -1, time.time()
    while steps < MAX_STEPS:
        obs = res["observation"]
        info = res.get("info", {})
        if obs.get("meta", {}).get("floor") != last_floor:
            last_floor = obs["meta"].get("floor")
            log("  [%4.0fs] F%-2s act%s hp%s/%s 金%s 牌%s" % (
                time.time() - t0, last_floor, obs["meta"].get("act"),
                obs["player"]["hp"], obs["player"]["max_hp"], obs["player"]["gold"],
                obs.get("master_deck", {}).get("count")))
        if res.get("done"):
            log("  == 终局 won=%s floor=%s steps=%s %.0fs" % (
                obs["meta"].get("won"), obs["meta"].get("floor"), steps, time.time() - t0))
            break
        a = pick(res)
        if a is None:
            time.sleep(1)
            r2 = rpc("get_state")
            if r2.get("ok"):
                res = r2["result"]
            continue
        r = rpc("step", action=a)
        steps += 1
        if not r.get("ok"):
            rec = {"i": steps, "action": a, "state": snapshot(res), "error": r.get("error")}
            traj.write(json.dumps(rec, ensure_ascii=False) + "\n")
            traj.flush()
            log("  step %d 失败: %s" % (steps, r.get("error")))
            r2 = rpc("get_state")
            if not r2.get("ok"):
                break
            res = r2["result"]
            continue
        res = r["result"]
        traj.write(json.dumps({"i": steps, "action": a, "state": snapshot(res)}, ensure_ascii=False) + "\n")
        traj.flush()

    traj.close()
    log("写出 %d 步 -> %s" % (steps, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
