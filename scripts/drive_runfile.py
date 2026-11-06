# -*- coding: utf-8 -*-
"""按玩家真实对局存档（*.run）驱动一局，落盘 golden trace。

⚠️ 用途硬约束（docs/parity-spec.md §1.5）：产物**只用于对拍验证**，
禁止进训练集 / 行为克隆 / 评估基线。

## 设计：显式决策状态机（v2）

*.run 记录的是**结果层决策**（每层的路线类型、选牌、营火、事件、遗物、购物），
但同一事件常有**多步**（Golden Idol：Take → 三选一；Dead Adventurer：Search×N → Leave）。
旧实现用启发式猜每步，导致「选了 Outrun 而不是 Smash」这类静默错误。

v2 改为：
  1. 启动时把存档编译成 `PLAN[floor] = [Step, ...]`（每步一个可断言的意图）
  2. 运行时逐 floor 消费 PLAN，每步断言「当前屏/选项」符合预期，不符就**大声报错**
  3. 战斗内仍用启发式（存档无逐回合记录），但战斗前后的资源（HP/金币/maxHP）
     与存档逐层校验

用法: drive_runfile.py <path/to/x.run> [--seed X] [--out F] [--dry-run]
"""
import json
import os
import socket
import sys
import time

PORT = int(os.environ.get("NEOW_BRIDGE_PORT", "45000"))

# ── 参数 ────────────────────────────────────────────────────────
RUN = None
SEED_OVERRIDE = None
OUT_OVERRIDE = None
DRY_RUN = False
_argv = sys.argv[1:]
i = 0
while i < len(_argv):
    a = _argv[i]
    if a == "--seed" and i + 1 < len(_argv):
        SEED_OVERRIDE = _argv[i + 1]; i += 2
    elif a == "--out" and i + 1 < len(_argv):
        OUT_OVERRIDE = _argv[i + 1]; i += 2
    elif a == "--dry-run":
        DRY_RUN = True; i += 1
    elif a.startswith("--"):
        i += 1
    else:
        RUN = a; i += 1

if RUN is None:
    sys.exit("用法: drive_runfile.py <x.run> [--seed X] [--out F] [--dry-run]")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "neow-sim", "tools"))
from seed_conv import seed_to_str  # noqa: E402

RUNFILE = json.load(open(RUN, encoding="utf-8"))
SEED = SEED_OVERRIDE or seed_to_str(RUNFILE["seed_played"])
ASC = RUNFILE.get("ascension_level") or 0
CHAR = RUNFILE.get("character_chosen") or "IRONCLAD"
OUT = OUT_OVERRIDE or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "runtime", "run_%s.jsonl" % SEED)


# ══════════════════════════════════════════════════════════════════
# 1. 存档 → 决策计划（PLAN）
# ══════════════════════════════════════════════════════════════════

# 事件的多步脚本：存档的 player_choice → [(步骤序号, 期望按钮关键词列表)]
# 每个元组的第 1 项是该步的「意图名」，第 2 项是可选按钮的匹配关键词（任一命中即可）
EVENT_SCRIPTS = {
    # ── Act 1 ──
    ("Big Fish", "Box"): [
        ("pick_box", ["box"]),
    ],
    ("Big Fish", "Banana"): [("pick_banana", ["banana"])],
    ("Big Fish", "Donut"): [("pick_donut", ["donut"])],
    # Shining Light：Enter 掉血升级 2 张（之后自动收尾）
    ("Shining Light", "Entered Light"): [
        ("enter", ["enter"]),
    ],
    ("Shining Light", "Ignored Light"): [("leave", ["leave"])],
    # Golden Idol：Take → (Outrun | Smash | Hide)
    ("Golden Idol", "Take Damage"): [
        ("take", ["take"]),
        ("smash", ["smash", "damage"]),
    ],
    ("Golden Idol", "Outrun"): [
        ("take", ["take"]),
        ("outrun", ["outrun"]),
    ],
    ("Golden Idol", "Hide"): [
        ("take", ["take"]),
        ("hide", ["hide"]),
    ],
    # Scrap Ooze：Reach Inside 可能多次（每次掉血/成功）
    ("Scrap Ooze", "Success"): [
        ("reach", ["reach", "inside"]),
    ],
    ("Scrap Ooze", "Leave"): [("leave", ["leave"])],
    # Match and Keep!
    ("Match and Keep!", "2 cards matched"): [
        ("match", ["flip", "match"]),
        ("match", ["flip", "match"]),
    ],
    # Dead Adventurer：Search N 次（每次 +25% 遇怪率）。
    # 「Searched '3' times」= 搜了 3 次；若期间遇怪则进战斗（fight），
    # 打完 proceed；没遇怪则最后 Leave。所以脚本写成「搜 3 次 → 遇怪/离开都接受」。
    ("Dead Adventurer", "Searched '3' times"): [
        ("search", ["search", "continue"]),
        ("search", ["search", "continue"]),
        ("search", ["search", "continue"]),
        ("finish", ["leave", "fight", "continue"]),
    ],
    # ── Act 2/3 ──
    ("The Joust", "Bet on Owner"): [("bet_owner", ["owner"])],
    ("The Joust", "Bet on Snail"): [("bet_snail", ["snail"])],
    ("Accursed Blacksmith", "Forge"): [("forge", ["forge"])],
    ("Accursed Blacksmith", "Rummage"): [("rummage", ["rummage"])],
    ("Vampires(?)", "Share"): [("share", ["share"])],
}

# 通用兜底：未知事件的第 1 步按 player_choice 关键词猜
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
    """把存档编译成 {floor: [Step...]}。Step = dict(kind=..., ...)。"""
    plan = {}

    def add(floor, step):
        plan.setdefault(int(floor), []).append(step)

    # 1) 选牌奖励（card_choices）：每层一次，可能 SKIP
    for c in rf.get("card_choices") or []:
        f = int(c["floor"])
        picked = c["picked"]
        add(f, {
            "kind": "card_reward",
            "pick": None if picked in (None, "SKIP") else picked.split("+")[0],
            "upgraded": "+" in (picked or ""),
            "not_picked": [x.split("+")[0] for x in (c.get("not_picked") or [])],
        })

    # 2) 营火
    for c in rf.get("campfire_choices") or []:
        add(int(c["floor"]), {
            "kind": "campfire",
            "action": {"SMITH": "smith", "REST": "sleep",
                       "RECALL": "recall", "LIFT": "lift",
                       "DIG": "dig", "PURGE": "toke"}.get(c["key"], "sleep"),
            "target": c.get("data"),
        })

    # 3) 事件（多步脚本）
    for e in rf.get("event_choices") or []:
        name = e.get("event_name") or ""
        choice = (e.get("player_choice") or "").strip()
        script = EVENT_SCRIPTS.get((name, choice))
        if script:
            steps = [{"kind": "event", "intent": it, "match": mw} for it, mw in script]
        else:
            steps = [{"kind": "event", "intent": "auto", "match": None,
                      "choice": choice, "record": e}]
        for s in steps:
            add(int(e["floor"]), dict(s, event=name))

    # 4) 购物（items_purchased + item_purchase_floors 一一对应）
    bought = rf.get("items_purchased") or []
    pf = rf.get("item_purchase_floors") or []
    for name, f in zip(bought, pf):
        add(int(f), {"kind": "shop", "buy": name.split("+")[0]})

    # 5) 删牌（items_purged）
    for name, f in zip(rf.get("items_purged") or [],
                       rf.get("items_purged_floors") or []):
        add(int(f), {"kind": "shop", "purge": name.split("+")[0]})

    # 6) Boss 遗物（按 boss_relics 顺序，索引 = 该幕序号）
    for k, b in enumerate(rf.get("boss_relics") or []):
        add(-1, {"kind": "boss_relic", "pick": b.get("picked"), "order": k})

    for f in plan:
        plan[f].sort(key=lambda s: {"event": 0, "card_reward": 1, "campfire": 2,
                                    "shop": 3, "boss_relic": 4}[s["kind"]])
    return plan


PLAN = build_plan(RUNFILE)

# 目标资源曲线（用于逐层校验）
TARGET_GOLD = RUNFILE.get("gold_per_floor") or []
TARGET_HP = RUNFILE.get("current_hp_per_floor") or []
TARGET_MAX = RUNFILE.get("max_hp_per_floor") or []
PATH_PER_FLOOR = RUNFILE.get("path_per_floor") or []


def target_at(floor):
    """gold_per_floor[i] = 第 i+1 层**结束时**的资源。返回该层的目标。"""
    idx = floor - 1
    return {
        "gold": TARGET_GOLD[idx] if idx < len(TARGET_GOLD) else None,
        "hp": TARGET_HP[idx] if idx < len(TARGET_HP) else None,
        "max_hp": TARGET_MAX[idx] if idx < len(TARGET_MAX) else None,
    }


# ══════════════════════════════════════════════════════════════════
# 2. 会话状态（每层消费计划、记录已做过的步骤）
# ══════════════════════════════════════════════════════════════════

class Session:
    def __init__(self):
        self.floor = -1
        self.steps = []          # 当前 floor 剩余的计划步骤
        self.boss_i = 0          # 第几个 Boss 遗物
        self.bought = set()      # 已买过的物品名
        self.entered_shop = False
        self.pending_grid_target = None
        self.warnings = []       # 计划与实际不符的记录
        self.cur_hp = None
        self.cur_gold = None
        self.cur_max = None

    def enter_floor(self, floor):
        if floor != self.floor:
            self.floor = floor
            self.steps = list(PLAN.get(floor, []))
            self.entered_shop = False
            self.pending_grid_target = None
            if floor > 0:
                t = target_at(floor)
                print("  [target] F%-2d 目标 gold=%s hp=%s max=%s" % (
                    floor, t["gold"], t["hp"], t["max_hp"]), flush=True)

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


# ══════════════════════════════════════════════════════════════════
# 3. RPC
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
            _id[0] += 1
            req = {"id": _id[0], "cmd": cmd}
            req.update(kw)
            _f.write((json.dumps(req) + "\n").encode())
            _f.flush()
            line = _f.readline()
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


def snapshot(res):
    obs = res.get("observation")
    if obs is None:
        return None
    info = res.get("info", {})
    p = obs.get("player", {})
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
        if t == "boss_relic":
            return {"type": t,
                    "relics": [r.get("id") for r in (scr.get("relics") or [])]}
        return None

    return {
        "screen": info.get("screen"), "act": info.get("act"),
        "floor": info.get("floor"), "turn": obs.get("meta", {}).get("turn"),
        "meta": {
            "seed": obs.get("meta", {}).get("seed"),
            "seed_code": obs.get("meta", {}).get("seed_code"),
            "ascension": obs.get("meta", {}).get("ascension"),
            "character": obs.get("meta", {}).get("character"),
        },
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


# ══════════════════════════════════════════════════════════════════
# 4. 战斗启发式（存档无逐回合记录，只能自算）
# ══════════════════════════════════════════════════════════════════

def combat_action(legal, obs):
    p = obs["player"]
    hp, mx = p["hp"], p["max_hp"]
    monsters = obs.get("monsters", [])
    hand = obs.get("hand", [])
    alive = [m for m in monsters if not m.get("is_gone")]
    incoming = 0
    for m in alive:
        it = m.get("intent") or {}
        if (it.get("type") or "").startswith("ATTACK"):
            incoming += (it.get("damage") or 0) * (it.get("count") or 1)
    plays = [a for a in legal if a["type"] == "play"]

    # 1) 致命一击
    for a in plays:
        tgt = a.get("target")
        if tgt is None:
            continue
        c = hand[a["card_index"]]
        if c.get("type") != "ATTACK":
            continue
        m = monsters[tgt]
        if (c.get("damage") or 0) >= (m["hp"] - (m.get("block") or 0)):
            return a
    # 2) 能力牌
    powers = [a for a in plays if hand[a["card_index"]].get("type") == "POWER"]
    if powers:
        return powers[0]
    # 3) 补格挡
    if incoming > p["block"]:
        defs = [a for a in plays if (hand[a["card_index"]].get("block") or 0) > 0]
        if defs:
            defs.sort(key=lambda a: -hand[a["card_index"]].get("block", 0))
            return defs[0]
    # 4) 攻击（威胁最大的优先）
    atk = [a for a in plays if hand[a["card_index"]].get("type") == "ATTACK"]
    if atk:
        def key(a):
            t = a.get("target")
            if t is None:
                return (999, 0)
            m = monsters[t]
            it = m.get("intent") or {}
            threat = (it.get("damage") or 0) * (it.get("count") or 1)
            return (-threat, m["hp"])
        atk.sort(key=key)
        return atk[0]
    if plays:
        return plays[0]
    # 5) 药水
    boss_like = len(alive) == 1 and alive[0]["hp"] > 90
    if hp < mx * 0.3 or (boss_like and hp < mx * 0.5):
        pots = [a for a in legal if a["type"] == "potion"]
        if pots:
            return pots[0]
    return {"type": "end_turn"}


# ══════════════════════════════════════════════════════════════════
# 5. 决策分发（显式状态机）
# ══════════════════════════════════════════════════════════════════

def option_texts(scr):
    return [((o.get("label") or "") + " " + (o.get("text") or "")).lower()
            for o in (scr.get("options") or [])]


def find_option(legal, texts, keywords):
    """按关键词找 event 选项（label+text 任一命中）。返回 action 或 None。"""
    for a in legal:
        if a["type"] != "event":
            continue
        oi = a.get("option_index", 0)
        if 0 <= oi < len(texts) and any(k in texts[oi] for k in keywords):
            return a
    return None


def decide(sess, res):
    """返回 (action, 说明)。action=None 表示等一帧。"""
    legal = res["legal_actions"]
    obs = res["observation"]
    scr = obs["screen"]
    t = scr["type"]
    p = obs["player"]

    def by(*ts):
        return [a for a in legal if a["type"] in ts]

    # ── 战斗 ──
    if t == "combat":
        return combat_action(legal, obs), "combat"

    # ── 涅奥 ──
    if t == "event" and (sess.floor or 0) == 0:
        return neow_action(legal, scr), "neow"

    # ── 地图 ──
    if t == "map":
        m = by("map")
        if not m:
            if by("boss"):
                return by("boss")[0], "boss-door"
            return None, "map-empty"
        nodes = {n["index"]: n["type"] for n in obs["map"]["nodes"]}
        want = PATH_PER_FLOOR[sess.floor] if sess.floor < len(PATH_PER_FLOOR) else None
        PREF = {"M": "MONSTER", "E": "ELITE", "R": "REST", "$": "SHOP",
                "T": "TREASURE", "?": "EVENT", "B": None}
        want_t = PREF.get(want)
        if want_t:
            for a in m:
                if nodes.get(a["node_index"]) == want_t:
                    return a, "map->" + want_t
            sess.warn("F%d 存档要 %s，但当前无此类型可选（实际 %s）" % (
                sess.floor, want_t, sorted({nodes.get(a["node_index"]) for a in m})))
        frac = p["hp"] / p["max_hp"] if p["max_hp"] else 1
        order = ("REST", "EVENT", "MONSTER", "TREASURE", "SHOP", "ELITE") if frac < 0.5 \
            else ("ELITE", "MONSTER", "EVENT", "TREASURE", "REST", "SHOP")
        for pref in order:
            for a in m:
                if nodes.get(a["node_index"]) == pref:
                    return a, "map-fallback->" + pref
        return m[0], "map-first"

    # ── 选牌奖励 ──
    if t == "card_reward":
        picks = by("card_pick")
        k, step = sess.peek("card_reward")
        cards = scr.get("cards") or []
        if step and picks:
            want = step["pick"]
            if want is None:
                sess.take(k)
                return {"type": "skip"}, "card:skip(存档 SKIP)"
            for a in picks:
                cid = (cards[a["index"]].get("id") or "").split("+")[0]
                if cid == want:
                    sess.take(k)
                    return a, "card:" + want
            # 存档要的牌不在候选 → 记警告，按评分选
            sess.warn("F%d 存档选 %s，候选 %s 里没有" % (
                sess.floor, want, [c.get("id") for c in cards]))
            sess.take(k)
        if picks:
            best = max(picks, key=lambda a: _card_score(cards[a["index"]]))
            return best, "card:fallback"
        return {"type": "skip"}, "card:none"

    # ── 营火 ──
    if t == "rest":
        k, step = sess.peek("campfire")
        want = step["action"] if step else None
        if want:
            for a in by("rest"):
                if a.get("option") == want:
                    # ⚠️ pop 之前把「升级目标」留给后面的 grid_select 屏：
                    #    曾直接 pop → grid_select 时计划里已无 target →
                    #    兜底选 indices[0]（手牌第一张 = 打击），存档要的
                    #    Reckless Charge / Barricade / Shockwave 全没升上。
                    sess.pending_grid_target = step.get("target")
                    sess.take(k)
                    return a, "campfire:" + want
            sess.warn("F%d 营火要 %s，实际 %s" % (
                sess.floor, want, [a.get("option") for a in by("rest")]))
            sess.take(k)
        frac = p["hp"] / p["max_hp"] if p["max_hp"] else 1
        want2 = "sleep" if frac < 0.65 else "smith"
        for a in by("rest"):
            if a.get("option") == want2:
                return a, "campfire:fallback-" + want2
        return (by("rest")[0], "campfire:first") if by("rest") else (None, "rest-none")

    # ── 事件 ──
    if t == "event":
        # 事件内精英战已结束：只有 proceed
        if by("proceed") and not by("event"):
            return by("proceed")[0], "event-done:proceed"
        ev = by("event")
        if not ev:
            return None, "event-empty"
        texts = option_texts(scr)
        k, step = sess.peek("event")
        if step:
            print("    [ev] F%d 计划剩余=%s 当前步骤=%s" % (
                sess.floor, [s.get("intent") for s in sess.steps if s["kind"]=="event"],
                step.get("intent")), flush=True)
            if step.get("match"):
                a = find_option(ev, texts, step["match"])
                if a:
                    sess.take(k)
                    sess.retry = 0
                    return a, "event:" + step["intent"]
                # ⚠️ 按钮还没刷新（上一步点击后事件要 1~2 帧才换屏）：
                #    先等几帧再判失败，不要立刻放弃（实测 Golden Idol 的
                #    Take → Outrun/Smash/Hide 需要一帧，立刻判失败会重复点 Take）
                sess.retry = getattr(sess, "retry", 0) + 1
                if sess.retry <= 6:
                    return None, "event:wait-refresh"
                sess.warn("F%d 事件 %s 找不到按钮 %s（实际 %s）" % (
                    sess.floor, step.get("event"), step["match"],
                    [t2[:24] for t2 in texts]))
                sess.retry = 0
                sess.take(k)
            else:
                # 通用：按 choice 关键词
                choice = (step.get("choice") or "").lower()
                for intent, kws in GENERIC_KEYWORDS.items():
                    if any(w in choice for w in kws):
                        a = find_option(ev, texts, kws)
                        if a:
                            sess.take(k)
                            return a, "event:" + intent
                sess.take(k)
        # 计划用尽：选 Leave
        for a in ev:
            oi = a.get("option_index", 0)
            if 0 <= oi < len(texts) and ("leave" in texts[oi] or "continue" in texts[oi]):
                return a, "event:leave"
        return ev[0], "event:first"

    # ── 战斗奖励 ──
    if t == "combat_reward":
        takes = [a for a in legal if a["type"] == "reward" and a.get("take_indices")]
        return (takes[0], "reward:take") if takes else ({"type": "reward", "skip": True}, "reward:skip")

    # ── 商店 ──
    if t == "shop_room":
        # ⚠️ 只进店一次：买完/买不到后必须 proceed 离开，否则会在
        #    shop_room → shop_enter → shop → cancel 之间死循环
        #    （实测 F12 空转 2900 步）。
        if not getattr(sess, "entered_shop", False):
            want_shop = any(s["kind"] == "shop" for s in sess.steps)
            if want_shop and by("shop_enter"):
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
            if s["kind"] != "shop":
                continue
            want = s.get("buy")
            if not want:
                continue
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
                        sess.bought.add(want)
                        return a, "shop:buy-" + want
        # 该层所有 shop 步骤都试过 → 放弃未买到的（记 WARN），然后 cancel 关店
        for k2, s in enumerate(list(sess.steps)):
            if s["kind"] == "shop" and s.get("buy"):
                sess.warn("F%d 商店买不到 %s（货架无同名或金币不足 %s）" % (
                    sess.floor, s["buy"], p["gold"]))
                sess.take(k2)
        if by("cancel"):
            return by("cancel")[0], "shop:cancel"
        return (by("proceed")[0], "shop:proceed") if by("proceed") else (None, "shop-none")

    # ── Boss 遗物 ──
    if t == "boss_relic":
        relics = by("relic")
        want = None
        if sess.boss_i < len(RUNFILE.get("boss_relics") or []):
            want = RUNFILE["boss_relics"][sess.boss_i].get("picked")
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

    # ── 其它屏 ──
    if t == "chest":
        return {"type": "chest_open"}, "chest"
    if t == "grid_select":
        if scr.get("confirm_up"):
            return {"type": "grid_select", "indices": []}, "grid:confirm"
        cards = scr.get("cards") or []
        # 营火 SMITH 目标（campfire 分支已把 target 存到 pending_grid_target）
        tgt = getattr(sess, "pending_grid_target", None)
        if tgt and cards:
            base = tgt.split("+")[0]
            for idx, c in enumerate(cards):
                if (c.get("id") or "").split("+")[0] == base:
                    sess.pending_grid_target = None
                    return {"type": "grid_select", "indices": [idx]}, "grid:" + base
            sess.warn("F%d 营火升级目标 %s 不在候选 %s" % (
                sess.floor, tgt, [c.get("id") for c in cards]))
            sess.pending_grid_target = None
        # 事件选牌目标
        for s in sess.steps:
            tgt2 = (s.get("record") or {}).get("cards_upgraded") or \
                   (s.get("record") or {}).get("cards_obtained") or []
            if tgt2 and cards:
                base = tgt2[0].split("+")[0]
                for idx, c in enumerate(cards):
                    if (c.get("id") or "").split("+")[0] == base:
                        return {"type": "grid_select", "indices": [idx]}, "grid:" + base
        # 兜底：按价值选（**绝不**默认选打击/防御——那会让整局牌组走向失真）
        idx, why = pick_upgrade_target(cards, exclude_basic=False)
        n = scr.get("num", 1)
        n = 1 if n in (-1, 0) else n
        n = max(1, min(n, 2))
        if idx is None:
            return {"type": "grid_select", "indices": list(range(n))}, "grid:none"
        picks = [idx] + [i for i in range(len(cards)) if i != idx][:n - 1]
        return {"type": "grid_select", "indices": picks}, "grid:value-" + str(why)
    if t == "hand_select":
        n = scr.get("num_to_select", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "hand_select", "indices": list(range(min(n, 3)))}, "hand:first"
    if t in ("complete", "game_over"):
        return {"type": "proceed"}, "proceed"
    return (legal[0], "first-legal") if legal else (None, "no-legal")


UPGRADE_PRIORITY = {
    "Barricade": 200, "Corruption": 190, "Feel No Pain": 180, "Demon Form": 175,
    "Dark Embrace": 170, "Evolve": 165, "Fiend Fire": 160, "Impervious": 155,
    "Bludgeon": 150, "Reaper": 145, "Feed": 140, "Offering": 135,
    "Whirlwind": 130, "Heavy Blade": 125, "Uppercut": 120, "Carnage": 115,
    "Immolate": 110, "Shockwave": 105, "Bash": 100, "Inflame": 95,
    "Metallicize": 90, "Pommel Strike": 85, "Twin Strike": 80,
    "Sever Soul": 75, "Clothesline": 70, "Rampage": 65, "Headbutt": 60,
    "Dropkick": 55, "Perfected Strike": 50,
}
# 基础牌：除非别无选择，绝不作为升级目标
BASIC_CARDS = {"Strike_R", "Strike_G", "Strike_B", "Defend_R", "Defend_G",
               "Defend_B", "Zap", "Dualcast", "Bash"}


def pick_upgrade_target(cards, exclude_basic=True):
    """选升级目标：优先计划牌 → 高价值牌 → 排除基础牌。

    返回 (index, reason)。**绝不**在还有非基础牌时选打击/防御。
    """
    def cid(c):
        return (c.get("id") or "").split("+")[0]

    cands = [(i, c) for i, c in enumerate(cards)
             if not (exclude_basic and cid(c) in BASIC_CARDS and len(cards) > 1)]
    if not cands:
        cands = list(enumerate(cards))
    if not cands:
        return None, "no-cards"
    best = max(cands, key=lambda t: (UPGRADE_PRIORITY.get(cid(t[1]), 1),
                                     -t[0]))
    return best[0], cid(best[1])


def _card_score(c):
    cid = (c.get("id") or "").split("+")[0]
    good_power = {"Barricade", "Feel No Pain", "Demon Form", "Inflame", "Evolve",
                  "Dark Embrace", "Corruption", "Juggernaut", "Metallicize",
                  "Brutality", "Berserk", "Rupture", "Combust", "Fire Breathing"}
    good_atk = {"Fiend Fire", "Bludgeon", "Reaper", "Feed", "Whirlwind", "Heavy Blade",
                "Uppercut", "Carnage", "Immolate", "Reckless Charge", "Pommel Strike",
                "Bash", "Dropkick", "Perfected Strike", "Twin Strike"}
    if cid in good_power:
        return 120
    if cid in good_atk:
        return 100
    if cid in ("AscendersBane", "Injury", "Regret", "Pain", "Parasite"):
        return -100
    return {"RARE": 70, "UNCOMMON": 55, "COMMON": 35}.get(c.get("rarity"), 30)


def neow_action(legal, scr):
    opts = scr.get("options") or []
    texts = [((o.get("label") or "") + " " + (o.get("text") or "")).lower() for o in opts]
    bonus = RUNFILE.get("neow_bonus") or ""
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
    if any("leave" in t2 for t2 in texts) and not any("talk" in t2 for t2 in texts):
        for a in legal:
            if a["type"] != "event":
                continue
            oi = a.get("option_index", 0)
            if 0 <= oi < len(texts) and "leave" in texts[oi]:
                return a
    if any("talk" in t2 for t2 in texts):
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


# ══════════════════════════════════════════════════════════════════
# 6. 主循环
# ══════════════════════════════════════════════════════════════════

def main():
    print("[plan] seed=%s asc=%s char=%s" % (SEED, ASC, CHAR), flush=True)
    for f in sorted(k for k in PLAN if k > 0):
        print("  F%-2d: %s" % (f, "; ".join(
            s["kind"] + (":" + (s.get("pick") or s.get("intent") or s.get("action")
                                or s.get("buy") or s.get("pick") or "")
                         if (s.get("pick") or s.get("intent") or s.get("action")
                             or s.get("buy")) else "")
            for s in PLAN[f])), flush=True)

    if DRY_RUN:
        print("[dry-run] 计划已生成，退出", flush=True)
        return 0

    connect()
    r = rpc("config", cheat_draw_order=True)
    if not r.get("ok"):
        print("config 失败:", r.get("error"), flush=True)
    r = rpc("get_state")
    res = r.get("result") if r.get("ok") else None
    if res and res.get("info", {}).get("in_dungeon"):
        rpc("quit_run")
        time.sleep(3)
    if res and (res.get("observation", {}).get("screen") or {}).get("type") == "game_over":
        rpc("step", action={"type": "proceed"})
        time.sleep(2)
    for _ in range(10):
        r = rpc("new_run", character=CHAR, ascension=ASC, seed=SEED)
        if r.get("ok"):
            break
        time.sleep(4)
    else:
        print("无法开局", flush=True)
        return 1
    res = r["result"]

    traj = open(OUT, "w", encoding="utf-8")
    traj.write(json.dumps({"i": 0, "action": None, "state": snapshot(res)},
                          ensure_ascii=False) + "\n")
    traj.flush()

    sess = Session()
    steps, last_floor, t0 = 0, -1, time.time()
    while steps < 3000:
        obs = res["observation"]
        floor = obs.get("meta", {}).get("floor") or 0
        sess.enter_floor(floor)
        if floor != last_floor:
            last_floor = floor
            p = obs.get("player", {})
            sess.cur_hp, sess.cur_gold = p.get("hp"), p.get("gold")
            sess.cur_max = p.get("max_hp")
            print("  [%4.0fs] F%-2s act%s %-14s hp%s/%s 金%s 牌%s" % (
                time.time() - t0, floor, obs["meta"].get("act"),
                (obs.get("screen") or {}).get("type"),
                p.get("hp"), p.get("max_hp"), p.get("gold"),
                obs.get("master_deck", {}).get("count")), flush=True)
        if res.get("done"):
            print("  == 终局 won=%s floor=%s steps=%s %.0fs" % (
                obs["meta"].get("won"), floor, steps, time.time() - t0), flush=True)
            break
        a, why = decide(sess, res)
        if a is None:
            time.sleep(0.8)
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
            print("  step %d 失败(%s): %s" % (steps, why, str(r.get("error"))[:110]), flush=True)
            r2 = rpc("get_state")
            if not r2.get("ok"):
                break
            res = r2["result"]
            continue
        res = r["result"]
        traj.write(json.dumps({"i": steps, "action": a, "state": snapshot(res)},
                              ensure_ascii=False) + "\n")
        traj.flush()

    traj.close()
    print("写出 %d 步 -> %s" % (steps, OUT), flush=True)
    if sess.warnings:
        print("\n=== 计划与实际不符 %d 处 ===" % len(sess.warnings), flush=True)
        for w in sess.warnings[:20]:
            print("  - " + w, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
