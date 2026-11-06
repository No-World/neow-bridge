# -*- coding: utf-8 -*-
"""按玩家真实对局存档（*.run）驱动一局，落盘 golden trace。

⚠️ 用途硬约束（docs/parity-spec.md §1.5）：产物**只用于对拍验证**，
禁止进训练集 / 行为克隆 / 评估基线。

核心思路：*.run 记录了真玩家的**决策链**（每层路线、选牌、遗物、营火、事件、
Boss 遗物、购物、药水），但**没有战斗内逐回合出牌**。所以：
  · 非战斗屏：严格按存档决策（选牌 / 营火 / 事件 / 商店 / 路线）
  · 战斗内：用启发式策略出牌（引擎确定性 ⇒ 只要决策合理，结果可复现）

用法: drive_runfile.py <path/to/x.run> [--out F]
产物: runtime/run_<seed>.jsonl（与 parity_replay.py 兼容）
"""
import json, os, socket, sys, time

PORT = int(os.environ.get("NEOW_BRIDGE_PORT", "45000"))
RUN = sys.argv[1]
SEED_OVERRIDE = None
for i, a in enumerate(sys.argv):
    if a == "--seed" and i + 1 < len(sys.argv):
        SEED_OVERRIDE = sys.argv[i + 1]

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "neow-sim", "tools"))
from seed_conv import seed_to_str  # noqa: E402

RUNFILE = json.load(open(RUN, encoding="utf-8"))
SEED = SEED_OVERRIDE or seed_to_str(RUNFILE["seed_played"])
ASC = RUNFILE.get("ascension_level") or 0
CHAR = RUNFILE.get("character_chosen") or "IRONCLAD"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "runtime", "run_%s.jsonl" % SEED)

# ── 存档决策索引 ──────────────────────────────────────────────
PATH_PER_FLOOR = RUNFILE.get("path_per_floor") or []
CARD_CHOICES = {}
for c in RUNFILE.get("card_choices") or []:
    CARD_CHOICES.setdefault(int(c["floor"]), []).append(c)
RELIC_OBTAINED = {int(r["floor"]): r["key"] for r in (RUNFILE.get("relics_obtained") or [])}
CAMPFIRE = {int(c["floor"]): c for c in (RUNFILE.get("campfire_choices") or [])}
BOSS_RELICS = RUNFILE.get("boss_relics") or []
EVENT_CHOICES = {int(e["floor"]): e for e in (RUNFILE.get("event_choices") or [])}
PURCHASE_FLOORS = {int(f): None for f in (RUNFILE.get("item_purchase_floors") or [])}
ITEMS_PURCHASED = RUNFILE.get("items_purchased") or []
POTION_USAGE = {int(f) for f in (RUNFILE.get("potions_floor_usage") or [])}


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


def log(*a):
    print(*a, flush=True)


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
                                 "effects": o.get("effects")} for o in (scr.get("options") or [])]}
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


# ── 战斗策略（比 drive_heart 再稳一点：优先保血）────────────
def combat_pick(legal, obs):
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

    # 1) 能击杀就击杀（减少本回合伤害）
    for a in plays:
        c = hand[a["card_index"]]
        if c.get("type") != "ATTACK":
            continue
        tgt = a.get("target")
        if tgt is None:
            continue
        m = monsters[tgt]
        dmg = c.get("damage") or 0
        if dmg >= (m["hp"] - (m.get("block") or 0)):
            return a
    # 2) 能力牌优先（长期收益）
    powers = [a for a in plays if hand[a["card_index"]].get("type") == "POWER"]
    if powers:
        return powers[0]
    # 3) 格挡不足先补格挡（按 incoming 缺口从大到小）
    if incoming > p["block"]:
        defs = [a for a in plays if (hand[a["card_index"]].get("block") or 0) > 0]
        if defs:
            defs.sort(key=lambda a: -hand[a["card_index"]].get("block", 0))
            return defs[0]
    # 4) 攻击：优先打「下回合会打最疼」的，其次血最少
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
    # 5) 药水：残血或 Boss 战
    boss_like = len(alive) == 1 and alive[0]["hp"] > 90
    if hp < mx * 0.3 or (boss_like and hp < mx * 0.5):
        pots = [a for a in legal if a["type"] == "potion"]
        if pots:
            return pots[0]
    return {"type": "end_turn"}


def card_pick_score(c):
    cid = (c.get("id") or "").split("+")[0]
    good_power = {"Barricade", "Feel No Pain", "Demon Form", "Inflame", "Evolve",
                  "Dark Embrace", "Corruption", "Juggernaut", "Metallicize",
                  "Brutality", "Berserk", "Rupture", "Combust", "Fire Breathing"}
    good_atk = {"Fiend Fire", "Bludgeon", "Reaper", "Feed", "Whirlwind", "Heavy Blade",
                "Uppercut", "Carnage", "Immolate", "Reckless Charge", "Pommel Strike",
                "Bash", "Dropkick", "Perfected Strike", "Twin Strike"}
    good_skill = {"Impervious", "Shrug It Off", "Flame Barrier", "True Grit",
                  "Second Wind", "Shockwave", "Disarm", "Entrench", "Battle Trance"}
    if cid in good_power:
        return 120
    if cid in good_atk:
        return 100
    if cid in good_skill:
        return 90
    if cid in ("AscendersBane", "Injury", "Regret", "Pain", "Parasite", "Normality"):
        return -100
    return {"RARE": 70, "UNCOMMON": 55, "COMMON": 35}.get(c.get("rarity"), 30)



# ── 涅奥祝福：存档 neow_bonus → 按钮文本关键词 ──
NEOW_KEYS = {
    "THREE_CARDS": ("obtain a card", "choose a card"),
    "ONE_RANDOM_RARE_CARD": ("obtain a random rare card",),
    "REMOVE_CARD": ("remove a card",),
    "UPGRADE_CARD": ("upgrade a card",),
    "TRANSFORM_CARD": ("transform a card",),
    "THREE_ENEMY_KILL": ("next three combats have 1 hp",),
    "RANDOM_COLORLESS": ("colorless card",),
    "RANDOM_COLORLESS_2": ("colorless card",),
    "ONE_RARE_RELIC": ("random rare relic",),
    "THREE_RARE_CARDS": ("choose a rare card",),
    "TWO_FIFTY_GOLD": ("250 gold",),
    "TRANSFORM_TWO_CARDS": ("transform 2 cards",),
    "TWENTY_PERCENT_HP_BONUS": ("max hp",),
    "BOSS_RELIC": ("boss relic",),
    "REMOVE_TWO": ("remove 2 cards",),
    "THREE_EPIC_CARDS": ("epic",),
}

def neow_pick(scr, bonus):
    """按存档的 neow_bonus 在涅奥选项里找对应按钮（先 Talk 后选祝福）。"""
    opts = scr.get("options") or []
    ev = [a for a in ([{"type": "event", "option_index": i} for i in range(len(opts))])]
    keys = NEOW_KEYS.get(bonus or "", ())
    for i, o in enumerate(opts):
        txt = ((o.get("label") or "") + " " + (o.get("text") or "")).lower()
        if any(k in txt for k in keys):
            return {"type": "event", "option_index": i}
    return {"type": "event", "option_index": 0}


def pick(res, floor):
    legal = res["legal_actions"]
    obs = res["observation"]
    scr = obs["screen"]
    t = scr["type"]
    p = obs["player"]

    def by(*ts):
        return [a for a in legal if a["type"] in ts]

    if t == "combat":
        return combat_pick(legal, obs)

    if t == "map":
        m = by("map")
        if not m:
            return {"type": "boss"} if by("boss") else None
        nodes = {n["index"]: n["type"] for n in obs["map"]["nodes"]}
        # ⚠️ path_per_floor 只记录房间**类型**（M/E/R/$/T/?/B），不含具体列；
        #    同类型多个可选时只能按类型选第一个 —— 这是「路线不完全复刻」的
        #    已知局限（存档不含节点坐标）。若要精确复刻需 bridge 侧记录 node_index。
        want = PATH_PER_FLOOR[floor] if floor < len(PATH_PER_FLOOR) else None
        PREF = {"M": "MONSTER", "E": "ELITE", "R": "REST", "$": "SHOP",
                "T": "TREASURE", "?": "EVENT", "B": None}
        want_t = PREF.get(want)
        if want_t:
            for a in m:
                if nodes.get(a["node_index"]) == want_t:
                    return a
        # 无匹配就按血量走
        frac = p["hp"] / p["max_hp"] if p["max_hp"] else 1
        order = ("REST", "EVENT", "MONSTER", "TREASURE", "SHOP", "ELITE") if frac < 0.5 \
            else ("ELITE", "MONSTER", "EVENT", "TREASURE", "REST", "SHOP")
        for pref in order:
            for a in m:
                if nodes.get(a["node_index"]) == pref:
                    return a
        return m[0]

    if t == "card_reward":
        picks = by("card_pick")
        want = CARD_CHOICES.get(floor)
        if want and picks:
            target = want[0]["picked"]
            cards = scr.get("cards", [])
            if target and target != "SKIP":
                for a in picks:
                    cid = (cards[a["index"]].get("id") or "").split("+")[0]
                    if cid == target.split("+")[0]:
                        return a
                # 存档牌不在候选里（说明前面决策已分叉）→ 用评分
            elif target == "SKIP":
                return {"type": "skip"}
        if picks:
            cards = scr.get("cards", [])
            best = max(picks, key=lambda a: card_pick_score(cards[a["index"]]))
            if card_pick_score(cards[best["index"]]) <= 30:
                return {"type": "skip"}
            return best
        return {"type": "skip"}

    if t == "rest":
        want = CAMPFIRE.get(floor)
        key = (want or {}).get("key")
        if key == "RECALL":
            for a in by("rest"):
                if a.get("option") in ("recall", "RECALL"):
                    return a
        if key == "SMITH":
            for a in by("rest"):
                if a.get("option") == "smith":
                    return a
            return by("rest")[0] if by("rest") else None
        if key == "REST":
            for a in by("rest"):
                if a.get("option") == "sleep":
                    return a
            return by("rest")[0] if by("rest") else None
        # 存档没记录（如无 SMITH 目标）→ 按血量
        want_opt = "sleep" if p["hp"] < p["max_hp"] * 0.65 else "smith"
        for a in by("rest"):
            if a.get("option") == want_opt:
                return a
        return by("rest")[0] if by("rest") else None

    if t == "combat_reward":
        takes = [a for a in legal if a["type"] == "reward" and a.get("take_indices")]
        return takes[0] if takes else {"type": "reward", "skip": True}

    if t == "boss_relic":
        relics = by("relic")
        if not relics:
            return {"type": "skip"}
        # 按存档的 boss_relics[i].picked 名字匹配（3 选 1 的候选里找同名）
        used = getattr(pick, "_boss_i", 0)
        want = None
        if used < len(BOSS_RELICS):
            want = BOSS_RELICS[used].get("picked")
        pick._boss_i = used + 1
        cands = scr.get("relics") or scr.get("items") or []
        if want:
            for a in relics:
                i = a.get("index", a.get("entity", 0))
                if 0 <= i < len(cands):
                    name = cands[i].get("id") or cands[i].get("name") or ""
                    if want.split(" ")[0].lower() in name.lower():
                        return a
        return relics[0]

    if t == "event":
        # 事件内精英战打完后（Dead Adventurer / Mushrooms / Colosseum 等），
        # bridge 只提供 proceed（事件屏但战斗已结束）
        if by("proceed") and not by("event"):
            return by("proceed")[0]
        ev = by("event")
        if not ev:
            return None
        # 涅奥（floor 0）：Talk → 按存档 neow_bonus 选祝福 → Leave 收尾
        if floor == 0:
            opts = scr.get("options") or []
            labels = [((o.get("label") or "") + (o.get("text") or "")).lower() for o in opts]
            if len(opts) == 1:
                # 只有 Talk 或只有 Leave：两者都点它
                return ev[0]
            if any("leave" in l for l in labels) and not any("talk" in l for l in labels):
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(labels) and "leave" in labels[oi]:
                        return a
                return ev[0]
            if any("talk" in l for l in labels):
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(labels) and "talk" in labels[oi]:
                        return a
            return neow_pick(scr, RUNFILE.get("neow_bonus"))
        want = EVENT_CHOICES.get(floor)
        opts = scr.get("options") or []
        if want:
            choice = (want.get("player_choice") or "").strip()
            # ⚠️ 同一事件会有多步决策（Golden Idol 的 Take → 三选一；Dead Adventurer
            #    的 Search → Continue → Leave）。存档只记「最终选择」，故：
            #    第 1 步按存档选，之后各步只做「收尾」（唯一可选项，通常是 Leave）。
            seen = getattr(pick, "_ev_seen", None)
            if seen is None:
                pick._ev_seen = seen = {}
            if seen.get(floor):
                if len(ev) == 1:
                    return ev[0]
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(opts):
                        txt = (opts[oi].get("label") or "").lower()
                        if "leave" in txt or "continue" in txt:
                            return a
                return ev[0]
            seen[floor] = True
            opts = scr.get("options") or []
            # ① 先按 player_choice 文本匹配（大小写/子串）
            if choice:
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(opts):
                        txt = ((opts[oi].get("label") or "") + " "
                               + (opts[oi].get("text") or ""))
                        if choice.lower() in txt.lower() or txt.lower() in choice.lower():
                            return a
            # ①' Dead Adventurer 特例：存档的 "Searched 'N' times" 表示搜索 N 次
        #     （按钮在 Search / Continue 之间轮换，且每次 +25% 遇怪率）。
        #     按 N 次连续点「搜索/继续」，第 N+1 次才 Leave。
        if choice and "search" in choice.lower():
            n = 1
            import re as _re
            m2 = _re.search(r"searched\s*'?(\d+)'?", choice.lower())
            if m2:
                n = int(m2.group(1))
            done = getattr(pick, "_searched", 0)
            if done < n:
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(opts):
                        txt = ((opts[oi].get("label") or "") + " "
                               + (opts[oi].get("text") or "")).lower()
                        if any(w in txt for w in ("search", "continue")):
                            pick._searched = done + 1
                            return a
            # 已搜够 N 次 → Leave
            for a in ev:
                oi = a.get("option_index", 0)
                if 0 <= oi < len(opts):
                    txt = (opts[oi].get("label") or "").lower()
                    if "leave" in txt:
                        return a
        # ② 语义关键词兜底：存档的 player_choice 常是「结果描述」
            #    （如 Dead Adventurer 的 "Searched '3' times"、Scrap Ooze 的 "Success"），
            #    与按钮文本（"Search" / "Reach Inside"）不字面匹配 → 按语义组匹配。
            SEMANTIC = (
                (("search", "sought", "reach", "dig", "grab"), ("search", "reach", "dig", "grab", "inside")),
                (("success", "found", "loot"), ("search", "reach", "dig", "grab", "inside")),
                (("box", "banana", "donut"), ("box", "banana", "donut")),
                (("entered light", "light"), ("enter", "light")),
                (("take damage", "damage"), ("take", "damage", "smash", "break")),
                (("fight", "combat", "battle"), ("fight", "attack", "combat")),
                (("obtain", "take"), ("take", "obtain", "grab")),
                (("leave", "walk away", "ignore"), ("leave", "walk", "ignore")),
                (("pray", "worship"), ("pray", "worship")),
                (("gold", "rich"), ("gold", "rich")),
                (("transform",), ("transform",)),
                (("remove", "purge"), ("remove", "purge")),
                (("upgrade", "smith"), ("upgrade", "smith", "forge")),
                (("heal", "rest"), ("heal", "rest")),
            )
            if choice:
                cl = choice.lower()
                for want_words, btn_words in SEMANTIC:
                    if not any(w in cl for w in want_words):
                        continue
                    for a in ev:
                        oi = a.get("option_index", 0)
                        if not (0 <= oi < len(opts)):
                            continue
                        txt = ((opts[oi].get("label") or "") + " "
                               + (opts[oi].get("text") or "")).lower()
                        if any(b in txt for b in btn_words):
                            return a
            # ③ 文本/语义都匹配不到 → 按**存档记录的副作用**反推
            #    （event_effects.json 提供每个选项的结构化效果：hp / obtain_relic / …）
            dmg = int(want.get("damage_taken") or 0)
            gold = int(want.get("gold_gain") or 0)
            rel = want.get("relics_obtained") or []
            upg = want.get("cards_upgraded") or []
            cards = want.get("cards_obtained") or []
            effects = [o.get("effects") or {} for o in opts]

            def hp_of(e):
                return int(e.get("hp") or 0)

            # 掉血事件：找 hp 最接近 -dmg 的项（最可靠）
            if dmg > 0:
                best, bestd = None, 10 ** 9
                for a in ev:
                    oi = a.get("option_index", 0)
                    if not (0 <= oi < len(effects)):
                        continue
                    h = hp_of(effects[oi])
                    if h < 0:
                        d = abs(h + dmg)
                        if d < bestd:
                            best, bestd = a, d
                if best is not None and bestd <= 2:
                    return best
            # 遗物：找 obtain_relic 匹配的
            for rname in rel:
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(effects):
                        r = effects[oi].get("obtain_relic") or ""
                        if r and rname.split(" ")[0].lower() in r.lower():
                            return a
            # 升级：找 upgrade_card 的
            if upg:
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(effects) and effects[oi].get("upgrade_card"):
                        return a
            # 金币
            if gold > 0:
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(effects) and effects[oi].get("gold"):
                        return a
                # 数字型（如 Searched '3' times）→ 取最后可用项
                for a in ev:
                    oi = a.get("option_index", 0)
                    if 0 <= oi < len(opts):
                        txt = (opts[oi].get("label") or opts[oi].get("text") or "")
                        if any(w in txt.lower() for w in ("search", "sought", "leave", "walk away")):
                            return a
        return ev[0]
    if t == "shop_room":
        # 该层有购买且还没进过店 → 进店；否则直接 proceed 离开房间
        if floor in PURCHASE_FLOORS and not getattr(pick, "_entered_shop", False):
            pick._entered_shop = True
            return {"type": "shop_enter"}
        if by("proceed"):
            return {"type": "proceed"}
        return {"type": "shop_enter"}
    if t == "shop":
        # 存档里该层有购买 → 严格按 items_purchased 的**名字**匹配货架；
        # 匹配不到就什么都不买（绝不兜底乱买，否则金币会被花光、复现失真）。
        # 全部买完 → proceed 离开商店房间（cancel 只是关闭商店屏、会退回 shop_room）。
        bought = getattr(pick, "_bought", set())
        if floor in PURCHASE_FLOORS:
            buys = by("shop_buy_card", "shop_buy_relic", "shop_buy_potion")
            cards = scr.get("cards") or []
            relics = scr.get("relics") or []
            pots = scr.get("potions") or []
            for want in ITEMS_PURCHASED:
                if want in bought:
                    continue
                for a in buys:
                    at = a.get("type")
                    i = a.get("index", 0)
                    src_list = (cards if at == "shop_buy_card"
                                else relics if at == "shop_buy_relic" else pots)
                    if 0 <= i < len(src_list):
                        name = (src_list[i].get("id") or src_list[i].get("name") or "")
                        if want.split("+")[0].lower() in name.lower():
                            bought.add(want)
                            pick._bought = bought
                            return a
        # 该买的都买完了：先 cancel 关闭商店屏（回到 shop_room），
        # 再由 shop_room 分支发 proceed 离开房间。
        # （shop 屏里没有 proceed 动作，直接发会报 invalid_action）
        if by("cancel"):
            return {"type": "cancel"}
        if by("proceed"):
            return {"type": "proceed"}
        return {"type": "cancel"}
    if t == "chest":
        return {"type": "chest_open"}
    if t == "hand_select":
        n = scr.get("num_to_select", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "hand_select", "indices": list(range(min(n, 3)))}
    if t == "grid_select":
        if scr.get("confirm_up"):
            return {"type": "grid_select", "indices": []}
        # 营火 SMITH：存档 campfire_choices[floor].data 指定要升级的牌名
        want = CAMPFIRE.get(floor)
        target = (want or {}).get("data")
        cards = scr.get("cards") or []
        if target and cards:
            tgt = target.split("+")[0]
            for i, c in enumerate(cards):
                cid = (c.get("id") or "").split("+")[0]
                if cid == tgt:
                    return {"type": "grid_select", "indices": [i]}
        # 事件选牌 / 战斗内选牌：按存档的 cards_obtained / cards_upgraded 匹配
        ev = EVENT_CHOICES.get(floor) or {}
        for key in ("cards_obtained", "cards_upgraded"):
            got = ev.get(key) or []
            if got and cards:
                tgt = got[0].split("+")[0]
                for i, c in enumerate(cards):
                    cid = (c.get("id") or "").split("+")[0]
                    if cid == tgt:
                        return {"type": "grid_select", "indices": [i]}
        n = scr.get("num", 1)
        n = 1 if n in (-1, 0) else n
        return {"type": "grid_select", "indices": list(range(min(n, 2)))}
    if t in ("complete", "game_over"):
        return {"type": "proceed"}
    return legal[0] if legal else None


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
        r = rpc("new_run", character=CHAR, ascension=ASC, seed=SEED)
        if r.get("ok"):
            break
        time.sleep(4)
    else:
        log("无法开局")
        return 1
    res = r["result"]
    log("开局 seed=%s asc=%s hp=%s/%s" % (SEED, ASC,
        res["observation"]["player"]["hp"], res["observation"]["player"]["max_hp"]))

    traj = open(OUT, "w", encoding="utf-8")
    traj.write(json.dumps({"i": 0, "action": None, "state": snapshot(res)},
                          ensure_ascii=False) + "\n")
    traj.flush()

    steps, last_floor, t0 = 0, -1, time.time()
    while steps < 3000:
        obs = res["observation"]
        floor = obs.get("meta", {}).get("floor") or 0
        if floor != last_floor:
            last_floor = floor
            log("  [%4.0fs] F%-2s act%s %-13s hp%s/%s 金%s 牌%s" % (
                time.time() - t0, floor, obs["meta"].get("act"),
                (obs.get("screen") or {}).get("type"),
                obs["player"]["hp"], obs["player"]["max_hp"], obs["player"]["gold"],
                obs.get("master_deck", {}).get("count")))
        if res.get("done"):
            log("  == 终局 won=%s floor=%s steps=%s %.0fs" % (
                obs["meta"].get("won"), floor, steps, time.time() - t0))
            break
        a = pick(res, floor)
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
            log("  step %d 失败: %s" % (steps, str(r.get("error"))[:120]))
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
    log("写出 %d 步 -> %s" % (steps, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
