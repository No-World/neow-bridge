# -*- coding: utf-8 -*-
"""必赢路线回放校验：种子+动作序列重放，逐问"仅凭观测能否推出这步动作"。

用法: replay_test.py <seed> [ascension] [trajectory.jsonl]

两类结论：
  [DIV] 确定性分歧 —— 录制时合法的动作重放时被拒（同种子同序列应完全复现）
  [GAP] 决策信息缺口 —— 观测缺少推出该动作所需的字段（决策充分性失败）
"""
import json, socket, sys, time

HOST, PORT = "127.0.0.1", 37771

class Gap(Exception):
    pass

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
        raise RuntimeError("connection closed")
    return json.loads(line)

def loc(o, *path):
    """安全取嵌套字段；返回 (ok, value)。"""
    cur = o
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return False, None
        cur = cur[p]
    return True, cur

def derive(action, obs, legal):
    """决策充分性：仅凭 obs 能否定位并论证该动作。返回 论证串 或 抛 Gap。"""
    t = obs["screen"]["type"]
    p = obs["player"]
    ty = action["type"]

    def need(cond, what):
        if not cond:
            raise Gap("缺 %s" % what)

    if ty == "play":
        i = action["card_index"]
        need(i < len(obs["hand"]), "hand[%d]（手牌共 %d 张）" % (i, len(obs["hand"])))
        c = obs["hand"][i]
        need("id" in c and "cost_for_turn" in c and "type" in c, "卡牌身份/费用/类型字段")
        need(p["energy"] >= c["cost_for_turn"], "能量 vs 费用判定（energy=%s cost=%s）" % (p["energy"], c["cost_for_turn"]))
        if action.get("target") is not None:
            m = action["target"]
            need(m < len(obs["monsters"]) and not obs["monsters"][m]["is_gone"], "存活目标（含 hp/block/is_gone）")
        return "play %s(%s费,type=%s)%s" % (c["id"], c["cost_for_turn"], c["type"],
                                            "->" + obs["monsters"][action["target"]]["id"] if action.get("target") is not None else "")

    if ty == "end_turn":
        need(t == "combat", "combat 屏")
        # 论证"无可打牌"所需：playable 标志或 energy vs cost
        have_play_flag = all(("playable" in c) for c in obs["hand"]) if obs["hand"] else True
        need(have_play_flag, "手牌 playable 标志（判断是否还有牌可打）")
        return "end_turn（手牌 %s 张，能量 %s）" % (len(obs["hand"]), p["energy"])

    if ty == "potion":
        i = action["potion_index"]
        need(i < len(obs["potions"]) and obs["potions"][i] is not None, "药水栏位内容")
        po = obs["potions"][i]
        need(po.get("can_use") is not None and po.get("target_required") is not None, "can_use/target_required")
        if action.get("target") is not None:
            need(action["target"] < len(obs["monsters"]), "药水目标")
        return "potion %s" % po["id"]

    if ty == "potion_discard":
        i = action["potion_index"]
        need(obs["potions"][i] is not None, "待弃药水")
        return "potion_discard %s" % obs["potions"][i]["id"]

    if ty == "map":
        n = action["node_index"]
        ok, choices = loc(obs, "map", "choices")
        need(ok, "map.choices")
        need(n in choices, "choices 中含目标节点 %s（当前 choices=%s）" % (n, choices))
        ok, nodes = loc(obs, "map", "nodes")
        need(ok, "map.nodes")
        tgt = [x for x in nodes if x["index"] == n]
        need(tgt and "type" in tgt[0], "节点类型")
        need("hp" in p and "max_hp" in p, "血量（路线选点依据）")
        return "map %s(type=%s)" % (n, tgt[0]["type"])

    if ty == "boss":
        ok, bd = loc(obs, "map", "boss_door")
        need(ok and bd, "map.boss_door")
        return "boss"

    if ty == "event":
        i = action["option_index"]
        ok, opts = loc(obs, "screen", "options")
        need(ok and i < len(opts), "事件选项列表")
        need("label" in opts[i] and "disabled" in opts[i], "选项 label/disabled")
        return "event[%d] %s" % (i, opts[i].get("label"))

    if ty == "rest":
        ok, opts = loc(obs, "screen", "options")
        need(ok, "休息选项")
        want = action.get("option")
        m = [o for o in opts if o.get("name") == want]
        need(m, "选项 %s（含 name/usable）" % want)
        if want == "sleep":
            need("heal" in m[0], "sleep 的回血量（决策依据）")
        if want == "smith":
            need("cards" in m[0], "smith 的可升级牌列表（决策依据）")
        return "rest %s" % want

    if ty == "reward":
        ok, items = loc(obs, "screen", "items")
        need(ok, "奖励 items")
        if action.get("skip"):
            need(True, "")
            return "reward skip（items=%s）" % [x["type"] for x in items]
        for i in action.get("take_indices", []):
            need(i < len(items), "奖励下标 %d" % i)
            need("type" in items[i], "奖励类型")
        return "reward take %s" % action.get("take_indices")

    if ty == "card_pick":
        ok, cards = loc(obs, "screen", "cards")
        need(ok and action["index"] < len(cards), "奖励卡列表")
        return "card_pick %s" % cards[action["index"]]["id"]

    if ty in ("card_bowl", "skip", "chest_open", "shop_enter"):
        need(ty != "chest_open" or t == "chest", "宝箱屏")
        return ty

    if ty == "relic":
        ok, rel = loc(obs, "screen", "relics")
        need(ok and action["relic_index"] < len(rel), "Boss遗物列表")
        return "relic[%s] %s" % (action["relic_index"], rel[action["relic_index"]].get("id"))

    if ty in ("shop_buy_card", "shop_buy_relic", "shop_buy_potion"):
        key = {"shop_buy_card": "cards", "shop_buy_relic": "relics", "shop_buy_potion": "potions"}[ty]
        ok, items = loc(obs, "screen", key)
        need(ok and action["index"] < len(items), "商店 %s 列表" % key)
        need("price" in items[action["index"]], "价格（决策依据）")
        return "%s %s(%s金)" % (ty, items[action["index"]].get("id", items[action["index"]].get("card", {}).get("id")), items[action["index"]]["price"])

    if ty == "shop_card_removal":
        ok, av = loc(obs, "screen", "purge_available")
        ok2, cost = loc(obs, "screen", "purge_cost")
        need(ok and ok2, "删卡服务可用性与价格")
        return "removal(cost=%s,gold=%s)" % (cost, p.get("gold"))

    if ty == "proceed":
        need(t in ("complete", "game_over", "shop_room"), "可推进的屏（实际 %s）" % t)
        return "proceed"

    if ty == "cancel":
        need(t in ("map", "shop", "grid_select"), "可后退的屏（实际 %s）" % t)
        return "cancel"

    if ty == "hand_select":
        ok, n = loc(obs, "screen", "num_to_select")
        need(ok, "hand_select 数量约束")
        need(len(action["indices"]) <= (obs["screen"].get("max_to_select", 99)), "选择数量约束")
        return "hand_select %s" % action["indices"]

    if ty in ("grid_select", "scry"):
        scr = obs["screen"]
        need("num" in scr or "any_number" in scr, "grid 数量约束")
        need(len(action.get("indices", action.get("discard_indices", []))) <= scr.get("num", 99) or scr.get("any_number"),
             "选择数量约束")
        return "%s %s" % (ty, action.get("indices", action.get("discard_indices")))

    raise Gap("未知的动作类型 %s" % ty)

def main():
    seed = sys.argv[1] if len(sys.argv) > 1 else None
    asc = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    traj_path = sys.argv[3] if len(sys.argv) > 3 else ("runtime/win_%s.jsonl" % (seed or "X"))
    actions = [json.loads(l)["action"] for l in open(traj_path, encoding="utf-8") if l.strip()]
    print("重放 %s：%d 步，asc=%s" % (seed, len(actions), asc))

    s, f = connect()
    rpc(f, "config", timeout_ms=180000)
    # 如已有局先弃
    r = rpc(f, "get_state")
    if r["ok"] and r["result"]["info"]["in_dungeon"]:
        rpc(f, "quit_run"); time.sleep(3)
    r = rpc(f, "new_run", character="IRONCLAD", ascension=asc, seed=seed)
    if not r["ok"]:
        print("new_run 失败:", r.get("error")); sys.exit(2)

    gaps, divs, ok_steps = [], [], 0
    t0 = time.time()
    for i, a in enumerate(actions):
        # 1) 决策充分性：拿"动作前"的观测做论证
        rg = rpc(f, "get_state")
        if not rg["ok"]:
            print("step %d: get_state 失败 %s" % (i, rg.get("error"))); break
        res = rg["result"]
        legal = res["legal_actions"]
        obs = res["observation"]
        try:
            why = derive(a, obs, legal)
        except Gap as g:
            gaps.append((i, a, str(g)))
            why = "GAP: " + str(g)
        # 2) 确定性：动作此刻必须合法
        if a not in legal and a not in [x for x in legal]:
            eq = any(all(x.get(k) == v for k, v in a.items()) for x in legal)
            if not eq:
                divs.append((i, a, res["info"]["screen"], json.dumps(legal)[:200]))
                print("step %d [DIV] %s 于 %s 合法集=%s" % (i, json.dumps(a), res["info"]["screen"], json.dumps(legal)[:120]))
        # 3) 执行
        r = rpc(f, "step", action=a)
        if not r["ok"]:
            print("step %d 执行失败: %s %s" % (i, r.get("error_type"), r.get("error")))
            divs.append((i, a, r.get("error"), ""))
            break
        ok_steps += 1
        res = r["result"]
        if res.get("done"):
            print("step %d: 对局结束 won=%s（录制时未结束则提前结束=分歧）" % (i, res["observation"]["meta"].get("won")))
            break

    print("\n===== 回放报告 =====")
    print("重放 %d/%d 步，用时 %.0fs" % (ok_steps, len(actions), time.time() - t0))
    print("确定性分歧 [DIV]: %d" % len(divs))
    for d in divs[:10]:
        print("  step %d %s @%s" % (d[0], json.dumps(d[1], ensure_ascii=False)[:80], d[2]))
    print("决策信息缺口 [GAP]: %d" % len(gaps))
    for g in gaps[:15]:
        print("  step %d %s -- %s" % (g[0], json.dumps(g[1], ensure_ascii=False)[:70], g[2]))
    if not gaps and not divs and ok_steps == len(actions):
        print("REPLAY PERFECT：观测信息完整支撑每一步决策，且路线完全复现")

if __name__ == "__main__":
    main()
