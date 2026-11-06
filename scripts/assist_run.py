# -*- coding: utf-8 -*-
"""辅助对局：**局外自动 + 局内真人**，全程录制。

## 分工

  · 非战斗屏（地图 / 选牌 / 营火 / 事件 / 商店 / 奖励 / Boss 遗物）
      → 按 `*.run` 存档的决策链**自动**发动作（复用 drive_runfile 的 PLAN）
  · 战斗屏
      → **不发任何动作**，只轮询 + 打印战场信息，等你在游戏窗口里自己打
  · 全程把状态变化写进 trace（真人的战斗动作靠状态差分反推）

这样真人只需负责「打牌」，其它全靠存档决策 → 能稳定拿到一条完整碎心轨迹。

## 用法

  1. scripts/launch.sh 启动游戏（到主菜单即可）
  2. python3 scripts/assist_run.py <x.run> --out traces/manual_heart.jsonl
  3. 游戏窗口里正常操作战斗；非战斗屏会自动推进
  4. 打完 Ctrl+C，然后跑 manual_to_trace.py 转成 parity 轨迹

⚠️ 素材用途硬约束（docs/parity-spec.md §1.5）：只用于对拍验证，
   禁止进训练集 / 行为克隆 / 评估基线。
"""
import argparse
import hashlib
import json
import os
import socket
import sys
import time

PORT = int(os.environ.get("NEOW_BRIDGE_PORT", "45000"))

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)),
                                "neow-sim", "tools"))
from seed_conv import seed_to_str  # noqa: E402


# ══════════════════════════════════════════════════════════════════
# 存档 → 决策计划（与 drive_runfile.py 同一套规则）
# ══════════════════════════════════════════════════════════════════

EVENT_SCRIPTS = {
    ("Big Fish", "Box"): [("pick_box", ["box"])],
    ("Big Fish", "Banana"): [("pick_banana", ["banana"])],
    ("Big Fish", "Donut"): [("pick_donut", ["donut"])],
    ("Shining Light", "Entered Light"): [("enter", ["enter"])],
    ("Shining Light", "Ignored Light"): [("leave", ["leave"])],
    ("Golden Idol", "Take Damage"): [("take", ["take"]), ("smash", ["smash", "damage"])],
    ("Golden Idol", "Outrun"): [("take", ["take"]), ("outrun", ["outrun"])],
    ("Golden Idol", "Hide"): [("take", ["take"]), ("hide", ["hide"])],
    ("Scrap Ooze", "Success"): [("reach", ["reach", "inside"])],
    ("Scrap Ooze", "Leave"): [("leave", ["leave"])],
    ("Match and Keep!", "2 cards matched"): [("match", ["flip", "match"]),
                                            ("match", ["flip", "match"])],
    ("Dead Adventurer", "Searched '3' times"): [
        ("search", ["search", "continue"]),
        ("search", ["search", "continue"]),
        ("search", ["search", "continue"]),
        ("finish", ["leave", "fight", "continue"]),
    ],
    ("The Joust", "Bet on Owner"): [("bet_owner", ["owner"])],
    ("The Joust", "Bet on Snail"): [("bet_snail", ["snail"])],
    ("Accursed Blacksmith", "Forge"): [("forge", ["forge"])],
    ("Accursed Blacksmith", "Rummage"): [("rummage", ["rummage"])],
}

GENERIC_KEYWORDS = {
    "take": ["take", "grab", "obtain"],
    "leave": ["leave", "walk", "ignore"],
    "enter": ["enter", "search", "reach", "inside", "dig"],
    "fight": ["fight", "attack", "combat"],
    "pray": ["pray", "worship"],
    "gold": ["gold"],
    "upgrade": ["upgrade", "smith", "forge"],
    "remove": ["remove", "purge"],
    "transform": ["transform"],
    "heal": ["heal", "rest"],
}


def build_plan(rf):
    plan = {}

    def add(floor, step):
        plan.setdefault(int(floor), []).append(step)

    for c in rf.get("card_choices") or []:
        picked = c["picked"]
        add(int(c["floor"]), {"kind": "card_reward",
                              "pick": None if picked in (None, "SKIP") else picked.split("+")[0]})
    for c in rf.get("campfire_choices") or []:
        add(int(c["floor"]), {"kind": "campfire",
                              "action": {"SMITH": "smith", "REST": "sleep",
                                         "RECALL": "recall", "LIFT": "lift",
                                         "DIG": "dig", "PURGE": "toke"}.get(c["key"], "sleep"),
                              "target": c.get("data")})
    for e in rf.get("event_choices") or []:
        name = e.get("event_name") or ""
        choice = (e.get("player_choice") or "").strip()
        script = EVENT_SCRIPTS.get((name, choice))
        if script:
            steps = [{"kind": "event", "intent": it, "match": mw, "event": name}
                     for it, mw in script]
        else:
            steps = [{"kind": "event", "intent": "auto", "match": None,
                      "choice": choice, "record": e, "event": name}]
        for s in steps:
            add(int(e["floor"]), s)
    for name, f in zip(rf.get("items_purchased") or [],
                       rf.get("item_purchase_floors") or []):
        add(int(f), {"kind": "shop", "buy": name.split("+")[0]})
    for name, f in zip(rf.get("items_purged") or [],
                       rf.get("items_purged_floors") or []):
        add(int(f), {"kind": "shop", "purge": name.split("+")[0]})

    for f in plan:
        plan[f].sort(key=lambda s: {"event": 0, "card_reward": 1, "campfire": 2,
                                    "shop": 3}[s["kind"]])
    return plan


# ══════════════════════════════════════════════════════════════════
# 会话状态
# ══════════════════════════════════════════════════════════════════

class Session:
    def __init__(self, plan, runfile):
        self.plan = plan
        self.rf = runfile
        self.floor = -1
        self.steps = []
        self.entered_shop = False
        self.pending_grid_target = None
        self.boss_i = 0
        self.warnings = []
        self.retry = 0

    def enter_floor(self, floor):
        if floor != self.floor:
            self.floor = floor
            self.steps = list(self.plan.get(floor, []))
            self.entered_shop = False
            self.pending_grid_target = None
            self.retry = 0

    def peek(self, kind):
        for k, s in enumerate(self.steps):
            if s["kind"] == kind:
                return k, s
        return None, None

    def take(self, k):
        return self.steps.pop(k)

    def warn(self, msg):
        self.warnings.append(msg)
        print("  [WARN] " + msg, flush=True)


def option_texts(scr):
    return [((o.get("label") or "") + " " + (o.get("text") or "")).lower()
            for o in (scr.get("options") or [])]


def find_option(legal, texts, keywords):
    for a in legal:
        if a["type"] != "event":
            continue
        oi = a.get("option_index", 0)
        if 0 <= oi < len(texts) and any(k in texts[oi] for k in keywords):
            return a
    return None


def _card_score(c):
    cid = (c.get("id") or "").split("+")[0]
    gp = {"Barricade", "Feel No Pain", "Demon Form", "Inflame", "Evolve",
          "Dark Embrace", "Corruption", "Juggernaut", "Metallicize",
          "Brutality", "Berserk", "Rupture", "Combust", "Fire Breathing"}
    ga = {"Fiend Fire", "Bludgeon", "Reaper", "Feed", "Whirlwind", "Heavy Blade",
          "Uppercut", "Carnage", "Immolate", "Reckless Charge", "Pommel Strike",
          "Bash", "Dropkick", "Perfected Strike", "Twin Strike"}
    if cid in gp:
        return 120
    if cid in ga:
        return 100
    if cid in ("AscendersBane", "Injury", "Regret", "Pain", "Parasite"):
        return -100
    return {"RARE": 70, "UNCOMMON": 55, "COMMON": 35}.get(c.get("rarity"), 30)


def neow_action(legal, scr, runfile):
    opts = scr.get("options") or []
    texts = [((o.get("label") or "") + " " + (o.get("text") or "")).lower() for o in opts]
    bonus = runfile.get("neow_bonus") or ""
    NEOW_KEYS = {
        "THREE_CARDS": ["obtain a card", "choose a card"],
        "ONE_RANDOM_RARE_CARD": ["random rare card"],
        "REMOVE_CARD": ["remove a card"],
        "UPGRADE_CARD": ["upgrade a card"],
        "TRANSFORM_CARD": ["transform a card"],
        "THREE_ENEMY_KILL": ["next three combats have 1 hp", "1 hp"],
        "RANDOM_COLORLESS": ["colorless card"],
        "ONE_RARE_RELIC": ["random rare relic"],
        "THREE_RARE_CARDS": ["choose a rare card"],
        "TWO_FIFTY_GOLD": ["250 gold"],
        "TRANSFORM_TWO_CARDS": ["transform 2 cards"],
        "TWENTY_PERCENT_HP_BONUS": ["max hp"],
        "BOSS_RELIC": ["boss relic"],
        "REMOVE_TWO": ["remove 2 cards"],
    }
    if len(opts) == 1:
        return {"type": "event", "option_index": 0}
    if any("leave" in t for t in texts) and not any("talk" in t for t in texts):
        for a in legal:
            if a["type"] != "event":
                continue
            oi = a.get("option_index", 0)
            if 0 <= oi < len(texts) and "leave" in texts[oi]:
                return a
    if any("talk" in t for t in texts):
        for a in legal:
            if a["type"] != "event":
                continue
            oi = a.get("option_index", 0)
            if 0 <= oi < len(texts) and "talk" in texts[oi]:
                return a
    for k in NEOW_KEYS.get(bonus, []):
        for a in legal:
            if a["type"] != "event":
                continue
            oi = a.get("option_index", 0)
            if 0 <= oi < len(texts) and k in texts[oi]:
                return a
    return {"type": "event", "option_index": 0}


def decide(sess, res):
    """非战斗屏的自动决策。战斗屏返回 (None, "manual")。"""
    legal = res["legal_actions"]
    obs = res["observation"]
    scr = obs["screen"]
    t = scr["type"]
    p = obs["player"]

    def by(*ts):
        return [a for a in legal if a["type"] in ts]

    if t == "combat":
        return None, "manual"

    if t == "event" and (sess.floor or 0) == 0:
        return neow_action(legal, scr, sess.rf), "neow"

    if t == "map":
        m = by("map")
        if not m:
            if by("boss"):
                return by("boss")[0], "boss-door"
            return None, "map-empty"
        nodes = {n["index"]: n["type"] for n in obs["map"]["nodes"]}
        want = (sess.rf.get("path_per_floor") or [])
        want = want[sess.floor] if sess.floor < len(want) else None
        PREF = {"M": "MONSTER", "E": "ELITE", "R": "REST", "$": "SHOP",
                "T": "TREASURE", "?": "EVENT", "B": None}
        want_t = PREF.get(want)
        if want_t:
            for a in m:
                if nodes.get(a["node_index"]) == want_t:
                    return a, "map->" + want_t
            sess.warn("F%d 存档要 %s，当前只有 %s" % (
                sess.floor, want_t, sorted({nodes.get(a["node_index"]) for a in m})))
        frac = p["hp"] / p["max_hp"] if p["max_hp"] else 1
        order = ("REST", "EVENT", "MONSTER", "TREASURE", "SHOP", "ELITE") if frac < 0.5 \
            else ("ELITE", "MONSTER", "EVENT", "TREASURE", "REST", "SHOP")
        for pref in order:
            for a in m:
                if nodes.get(a["node_index"]) == pref:
                    return a, "map-fallback->" + pref
        return m[0], "map-first"

    if t == "card_reward":
        picks = by("card_pick")
        k, step = sess.peek("card_reward")
        cards = scr.get("cards") or []
        if step and picks:
            want = step["pick"]
            if want is None:
                sess.take(k)
                return {"type": "skip"}, "card:skip"
            for a in picks:
                cid = (cards[a["index"]].get("id") or "").split("+")[0]
                if cid == want:
                    sess.take(k)
                    return a, "card:" + want
            sess.warn("F%d 存档选 %s，候选 %s" % (
                sess.floor, want, [c.get("id") for c in cards]))
            sess.take(k)
        if picks:
            return max(picks, key=lambda a: _card_score(cards[a["index"]]), ), "card:fallback"
        return {"type": "skip"}, "card:none"

    if t == "rest":
        k, step = sess.peek("campfire")
        want = step["action"] if step else None
        if want:
            for a in by("rest"):
                if a.get("option") == want:
                    sess.pending_grid_target = step.get("target")
                    sess.take(k)
                    return a, "campfire:" + want
            sess.take(k)
        frac = p["hp"] / p["max_hp"] if p["max_hp"] else 1
        want2 = "sleep" if frac < 0.65 else "smith"
        for a in by("rest"):
            if a.get("option") == want2:
                return a, "campfire:fallback-" + want2
        return (by("rest")[0], "campfire:first") if by("rest") else (None, "rest-none")

    if t == "event":
        if by("proceed") and not by("event"):
            return by("proceed")[0], "event-done:proceed"
        ev = by("event")
        if not ev:
            return None, "event-empty"
        texts = option_texts(scr)
        k, step = sess.peek("event")
        if step and step.get("match"):
            a = find_option(ev, texts, step["match"])
            if a:
                sess.take(k)
                sess.retry = 0
                return a, "event:" + step["intent"]
            sess.retry += 1
            if sess.retry <= 6:
                return None, "event:wait-refresh"
            sess.warn("F%d 事件 %s 找不到按钮 %s（实际 %s）" % (
                sess.floor, step.get("event"), step["match"], [x[:20] for x in texts]))
            sess.retry = 0
            sess.take(k)
        elif step:
            choice = (step.get("choice") or "").lower()
            for intent, kws in GENERIC_KEYWORDS.items():
                if any(w in choice for w in kws):
                    a = find_option(ev, texts, kws)
                    if a:
                        sess.take(k)
                        return a, "event:" + intent
            sess.take(k)
        for a in ev:
            oi = a.get("option_index", 0)
            if 0 <= oi < len(texts) and ("leave" in texts[oi] or "continue" in texts[oi]):
                return a, "event:leave"
        return ev[0], "event:first"

    if t == "combat_reward":
        takes = [a for a in legal if a["type"] == "reward" and a.get("take_indices")]
        return ((takes[0], "reward:take") if takes
                else ({"type": "reward", "skip": True}, "reward:skip"))

    if t == "shop_room":
        if not sess.entered_shop:
            if any(s["kind"] == "shop" for s in sess.steps) and by("shop_enter"):
                sess.entered_shop = True
                return by("shop_enter")[0], "shop:enter"
        if by("proceed"):
            return by("proceed")[0], "shop:proceed"
        return (by("shop_enter")[0], "shop:enter") if by("shop_enter") else (None, "shop-none")

    if t == "shop":
        cards = scr.get("cards") or []
        relics = scr.get("relics") or []
        pots = scr.get("potions") or []
        for k2, s in enumerate(sess.steps):
            if s["kind"] != "shop" or not s.get("buy"):
                continue
            want = s["buy"]
            for a in legal:
                at = a.get("type")
                if at not in ("shop_buy_card", "shop_buy_relic", "shop_buy_potion"):
                    continue
                idx = a.get("index", 0)
                lst = (cards if at == "shop_buy_card"
                       else relics if at == "shop_buy_relic" else pots)
                if 0 <= idx < len(lst):
                    name = (lst[idx].get("id") or "").split("+")[0]
                    if want.lower() in name.lower():
                        sess.take(k2)
                        return a, "shop:buy-" + want
        for k2, s in enumerate(list(sess.steps)):
            if s["kind"] == "shop" and s.get("buy"):
                sess.warn("F%d 商店买不到 %s（gold=%s）" % (sess.floor if hasattr(sess, 'floor') else '?', s["buy"], p["gold"]))
                sess.take(k2)
        if by("cancel"):
            return by("cancel")[0], "shop:cancel"
        return (by("proceed")[0], "shop:proceed") if by("proceed") else (None, "shop-none")

    if t == "boss_relic":
        relics = by("relic")
        want = None
        bl = sess.rf.get("boss_relics") or []
        if sess.boss_i < len(bl):
            want = bl[sess.boss_i].get("picked")
        sess.boss_i += 1
        cands = scr.get("relics") or []
        if want and relics:
            for a in relics:
                idx = a.get("index", a.get("entity", 0))
                if 0 <= idx < len(cands):
                    name = (cands[idx].get("id") or "")
                    if want.split(" ")[0].lower() in name.lower():
                        return a, "boss_relic:" + want
            sess.warn("F%d Boss 遗物要 %s，候选 %s" % (sess.floor, want, cands))
        return (relics[0], "boss_relic:first") if relics else ({"type": "skip"}, "boss_relic:skip")

    if t == "chest":
        return {"type": "chest_open"}, "chest"

    if t == "grid_select":
        if scr.get("confirm_up"):
            return {"type": "grid_select", "indices": []}, "grid:confirm"
        cards = scr.get("cards") or []
        tgt = sess.pending_grid_target
        if tgt and cards:
            base = tgt.split("+")[0]
            for idx, c in enumerate(cards):
                if (c.get("id") or "").split("+")[0] == base:
                    sess.pending_grid_target = None
                    return {"type": "grid_select", "indices": [idx]}, "grid:" + base
            sess.warn("F%d 营火升级目标 %s 不在候选 %s" % (
                sess.floor, tgt, [c.get("id") for c in cards]))
            sess.pending_grid_target = None
        n = scr.get("num", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "grid_select", "indices": list(range(min(n, 2)))}, "grid:first"

    if t == "hand_select":
        n = scr.get("num_to_select", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "hand_select", "indices": list(range(min(n, 3)))}, "hand:first"

    if t in ("complete", "game_over"):
        return {"type": "proceed"}, "proceed"

    return (legal[0], "first-legal") if legal else (None, "no-legal")


# ══════════════════════════════════════════════════════════════════
# RPC + 录制
# ══════════════════════════════════════════════════════════════════

_s = None
_f = None
_id = [0]


def connect():
    global _s, _f
    _s = socket.create_connection(("127.0.0.1", PORT), timeout=200)
    _f = _s.makefile("rwb")


def rpc(cmd, **kw):
    global _s, _f
    for _ in range(6):
        try:
            if _f is None:
                connect()
            _id[0] += 1
            req = {"id": _id[0], "cmd": cmd}
            req.update(kw)
            _f.write((json.dumps(req) + "\n").encode())
            _f.flush()
            line = _f.readline()
            if not line:
                raise ConnectionError("closed")
            return json.loads(line)
        except (ConnectionError, OSError, json.JSONDecodeError):
            time.sleep(1.5)
            try:
                connect()
            except OSError:
                time.sleep(2)
    return {"ok": False, "error": "rpc failed"}


def snapshot(obs, info):
    p = obs.get("player") or {}
    scr = obs.get("screen") or {}

    def payload():
        t = scr.get("type")
        if t == "event":
            return {"type": t, "id": scr.get("id"),
                    "options": [{"label": o.get("label"), "text": o.get("text"),
                                 "effects": o.get("effects")}
                                for o in (scr.get("options") or [])]}
        if t == "card_reward":
            return {"type": t, "cards": [c.get("id") for c in (scr.get("cards") or [])]}
        if t == "combat_reward":
            out = []
            for r in (scr.get("items") or []):
                e = {"type": r.get("type")}
                if r.get("gold") is not None:
                    e["gold"] = r["gold"]
                if r.get("relic_id"):
                    e["relic"] = r["relic_id"]
                if r.get("potion_id"):
                    e["potion"] = r["potion_id"]
                if r.get("cards"):
                    e["cards"] = [c.get("id") for c in r["cards"]]
                out.append(e)
            return {"type": t, "items": out}
        return None

    return {
        "screen": info.get("screen"), "act": info.get("act"),
        "floor": info.get("floor"),
        "turn": (obs.get("meta") or {}).get("turn"),
        "meta": {"seed": (obs.get("meta") or {}).get("seed"),
                 "seed_code": (obs.get("meta") or {}).get("seed_code"),
                 "ascension": (obs.get("meta") or {}).get("ascension"),
                 "character": (obs.get("meta") or {}).get("character")},
        "player": {k: p.get(k) for k in ("hp", "max_hp", "block", "energy", "gold", "powers")},
        "hand": [(c.get("id"), c.get("upgraded"), c.get("cost_for_turn"))
                 for c in (obs.get("hand") or [])],
        "monsters": [{"id": m.get("id"), "hp": m.get("hp"), "max_hp": m.get("max_hp"),
                      "block": m.get("block"), "powers": m.get("powers"),
                      "intent": m.get("intent")} for m in (obs.get("monsters") or [])],
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


class ContinuityMonitor:
    """S/L 检测：录制的每一步都必须单调前进（floor/turn 不倒退、怪物不回血）。"""

    def __init__(self):
        self.floor = -1
        self.turn = -1
        self.mon_hp = {}
        self.reason = ""

    def check(self, floor, turn, obs, info):
        scr = (obs.get("screen") or {}).get("type")
        p = obs.get("player") or {}

        # floor 只能前进或不变
        if floor < self.floor:
            self.reason = "floor 回退 %s -> %s" % (self.floor, floor)
            return False
        # 同一 floor 内 turn 只能前进或不变
        if floor == self.floor and turn is not None and self.turn is not None:
            if turn < self.turn:
                self.reason = "turn 回退 %s -> %s（floor %s）" % (self.turn, turn, floor)
                return False
        # 同一 turn 内，已出现的怪物血量不能回升（受击只能降；回血类怪物除外）
        if scr == "combat" and floor == self.floor and turn == self.turn:
            for k, m in enumerate(obs.get("monsters") or []):
                if m.get("is_gone"):
                    continue
                hp = m.get("hp") or 0
                prev = self.mon_hp.get(k)
                if prev is not None and hp > prev:
                    # Regenerate/Regen 等回血是合法的，用「大幅回升」判定（>5）
                    if hp - prev > 5:
                        self.reason = "怪物 %s 血量回升 %s -> %s（S/L 迹象）" % (
                            m.get("id"), prev, hp)
                        return False

        # 更新基线（注意：先记录，再更新 floor）
        self.floor = floor
        self.turn = turn if turn is not None else self.turn
        if scr == "combat":
            self.mon_hp = {k: (m.get("hp") or 0)
                           for k, m in enumerate(obs.get("monsters") or [])
                           if not m.get("is_gone")}
        else:
            self.mon_hp = {}
        return True


def rule_hash(obs, info):
    p = obs.get("player") or {}
    scr = obs.get("screen") or {}
    key = [info.get("floor"), info.get("act"), scr.get("type"),
           (obs.get("meta") or {}).get("turn"), p.get("hp"), p.get("block"),
           p.get("energy"), p.get("gold"),
           [(c.get("id"), c.get("cost_for_turn")) for c in (obs.get("hand") or [])],
           [(m.get("id"), m.get("hp"), m.get("block")) for m in (obs.get("monsters") or [])],
           (obs.get("draw_pile") or {}).get("count"),
           (obs.get("discard_pile") or {}).get("count"),
           [o.get("label") for o in (scr.get("options") or [])]]
    return hashlib.md5(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def print_combat(obs, info):
    p = obs.get("player") or {}
    hand = obs.get("hand") or []
    mons = [m for m in (obs.get("monsters") or []) if not m.get("is_gone")]
    print("\n  ═══════ 战斗（请你操作）═══════", flush=True)
    print("  F%s turn%s  hp %s/%s  block %s  energy %s  gold %s" % (
        info.get("floor"), (obs.get("meta") or {}).get("turn"),
        p.get("hp"), p.get("max_hp"), p.get("block"), p.get("energy"),
        p.get("gold")), flush=True)
    print("  手牌:", flush=True)
    for i, c in enumerate(hand):
        print("    [%d] %-22s cost=%s%s" % (
            i, c.get("id"), c.get("cost_for_turn"),
            "  " + str(c.get("damage")) + "dmg" if c.get("damage") else ""), flush=True)
    print("  怪物:", flush=True)
    for m in mons:
        it = m.get("intent") or {}
        print("    %-22s hp %s/%s block %s  意图 %s %s" % (
            m.get("id"), m.get("hp"), m.get("max_hp"), m.get("block"),
            it.get("type"), it.get("damage") or ""), flush=True)
    print("  抽牌堆 %s / 弃牌堆 %s / 消耗 %s" % (
        (obs.get("draw_pile") or {}).get("count"),
        (obs.get("discard_pile") or {}).get("count"),
        (obs.get("exhaust_pile") or {}).get("count"), ), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="*.run 存档路径")
    ap.add_argument("--out", default=None)
    ap.add_argument("--poll", type=float, default=0.4)
    args = ap.parse_args()

    rf = json.load(open(args.run, encoding="utf-8"))
    seed = seed_to_str(rf["seed_played"])
    asc = rf.get("ascension_level") or 0
    char = rf.get("character_chosen") or "IRONCLAD"
    out = args.out or os.path.join(os.path.dirname(HERE), "runtime",
                                   "assist_%s.jsonl" % seed)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    plan = build_plan(rf)
    print(f"[assist] 种子 {seed} 进阶 {asc} 角色 {char}", flush=True)
    print(f"[assist] 计划覆盖 {len(plan)} 层；战斗屏交给你，其余自动。", flush=True)

    connect()
    r = rpc("config", cheat_draw_order=True)
    if not r.get("ok"):
        print("config 失败:", r.get("error"), flush=True)
    r = rpc("get_state")
    res = r.get("result") if r.get("ok") else None
    if res and res.get("info", {}).get("in_dungeon"):
        rpc("quit_run")
        time.sleep(2)
    if res and (res.get("observation", {}).get("screen") or {}).get("type") == "game_over":
        rpc("step", action={"type": "proceed"})
        time.sleep(2)
    for _ in range(10):
        r = rpc("new_run", character=char, ascension=asc, seed=seed)
        if r.get("ok"):
            break
        time.sleep(3)
    else:
        print("无法开局", flush=True)
        return 1
    res = r["result"]

    fh = open(out, "w", encoding="utf-8")
    sess = Session(plan, rf)
    n, last_hash, t0 = 0, None, time.time()
    monitor = ContinuityMonitor()
    in_combat = False

    print("\n开局成功。战斗屏请你操作；其它屏我会自动推进。", flush=True)
    try:
        while True:
            obs = res.get("observation")
            info = res.get("info", {})
            if obs is None:
                time.sleep(args.poll)
                r = rpc("get_state")
                if r.get("ok"):
                    res = r["result"]
                continue
            if res.get("done"):
                print("\n  == 终局 won=%s floor=%s" % (
                    (obs.get("meta") or {}).get("won"), info.get("floor")), flush=True)
                break

            floor = (obs.get("meta") or {}).get("floor") or 0
            turn = (obs.get("meta") or {}).get("turn")

            # ══════ S/L 硬约束（项目产品原则 #1）══════
            # 一次连续对局，绝不通过存档/读档重试。录制期间若检测到「回退」迹象
            # （floor 变小、turn 变小、hp 无故回满、同一 turn 的怪物血量回升），
            # 说明发生了 S/L → **整段录制作废**，不能当作 golden trace。
            if not monitor.check(floor, turn, obs, info):
                print("\n  ✗✗ 检测到 S/L（回退）——录制作废：%s" % monitor.reason, flush=True)
                fh.close()
                bad = out + ".SL_VIOLATION"
                os.rename(out, bad)
                print("  已改名为 %s（不要用于对拍）" % bad, flush=True)
                return 2
            sess.enter_floor(floor)

            # 录制：状态变化就记
            h = rule_hash(obs, info)
            if h != last_hash:
                last_hash = h
                fh.write(json.dumps({"i": n, "t": round(time.time() - t0, 1),
                                     "action": None,
                                     "state": snapshot(obs, info),
                                     "legal": [(a.get("type"), a.get("entity"),
                                                a.get("target"), a.get("card_index"),
                                                a.get("option_index"), a.get("index"))
                                               for a in (res.get("legal_actions") or [])]
                                     }, ensure_ascii=False) + "\n")
                fh.flush()
                n += 1
                scr_t = (obs.get("screen") or {}).get("type")
                if scr_t != "combat":
                    p = obs.get("player") or {}
                    print("  [%5.0fs] #%-4d F%-2s %-14s hp%s/%s 金%s" % (
                        time.time() - t0, n, floor, scr_t, p.get("hp"),
                        p.get("max_hp"), p.get("gold")), flush=True)

            # 战斗屏：不动作，等真人
            if (obs.get("screen") or {}).get("type") == "combat":
                if not in_combat:
                    in_combat = True
                    print_combat(obs, info)
                time.sleep(args.poll)
                r = rpc("get_state")
                if r.get("ok"):
                    res = r["result"]
                continue
            if in_combat:
                in_combat = False
                print("  [战斗结束]", flush=True)

            # 非战斗屏：自动决策
            a, why = decide(sess, res)
            if a is None:
                time.sleep(args.poll)
                r = rpc("get_state")
                if r.get("ok"):
                    res = r["result"]
                continue
            r = rpc("step", action=a)
            if not r.get("ok"):
                print("  step 失败(%s): %s" % (why, str(r.get("error"))[:100]), flush=True)
                time.sleep(1)
            r2 = rpc("get_state")
            if r2.get("ok"):
                res = r2["result"]
    except KeyboardInterrupt:
        print("\n[Ctrl+C] 停止", flush=True)
    finally:
        fh.close()

    print(f"\n=== 录制 {n} 步 -> {out} ===")
    if sess.warnings:
        print(f"计划与实际不符 {len(sess.warnings)} 处：")
        for w in sess.warnings[:12]:
            print("  - " + w)
    print(f"\n下一步：python3 scripts/manual_to_trace.py {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
