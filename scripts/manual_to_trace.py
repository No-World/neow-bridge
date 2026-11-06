# -*- coding: utf-8 -*-
"""把 record_manual.py 的原始录制转成 parity_replay.py 可读的 golden trace。

## 原理

录制器只记录「状态序列 + 每步的 legal_actions」，不知道你点了什么。
转换器对每一对相邻状态 (S_i → S_{i+1})：
  · 在 S_i 的 legal_actions 里逐个试算「发这个动作后应变成什么」
  · 但**不能真的发**（会破坏正在进行的对局）
  → 改用**启发式指纹匹配**：按动作类型推断

匹配规则（按优先级）：
  1. play：看手牌少了哪张（按 id+uuid 序）→ 得到 card_index；看怪物掉血 → target
  2. end_turn：hand 变多 / turn +1
  3. map：floor +1
  4. event：options 变化 → 按 label 匹配
  5. reward / proceed / rest / chest_open / boss / grid_select / hand_select

产物与 record_trace.py 同格式，可直接喂 parity_replay.py。
"""
import json
import sys

# 动作类型常量（与 crates/py/src/lib.rs action_type 一致）
AT = {"play": 0, "end_turn": 1, "map": 4, "event": 5, "rest": 6,
      "reward": 8, "proceed": 14, "chest_open": 12, "boss": 10,
      "grid_select": 16, "hand_select": 17, "potion": 2, "shop_buy_card": 7}


def hand_ids(st):
    return [(h[0], h[1]) for h in (st.get("hand") or [])]


def infer_action(prev, nxt):
    """从 (prev_state, next_state) 反推动作。返回 bridge action dict 或 None。"""
    if prev is None or nxt is None:
        return None
    ps, ns = prev, nxt
    psc = (ps.get("screen") or "").lower()
    nsc = (nxt.get("screen") or "").lower()

    # ── 战斗：手牌少了某张 → play ──
    if psc == "combat" and nsc in ("combat", "combat_reward", "complete"):
        ph, nh = hand_ids(ps), hand_ids(nxt)
        if len(nh) < len(ph):
            # 找出被移除的那张（按 (id, upgraded) 多重集差）
            from collections import Counter
            cp = Counter(ph)
            cn = Counter(nh)
            removed = list((cp - cn).elements())
            if removed:
                target_id = removed[0]
                for idx, h in enumerate(ph):
                    if h == target_id:
                        # 找目标：怪物掉血最多的那个
                        pm = ps.get("monsters") or []
                        nm = nxt.get("monsters") or []
                        tgt = None
                        for k, (a, b) in enumerate(zip(pm, nm)):
                            if (a.get("hp") or 0) > (b.get("hp") or 0):
                                tgt = k
                                break
                        return {"type": "play", "card_index": idx,
                                **({"target": tgt} if tgt is not None else {})}
        # 手牌没少但 turn +1 → end_turn
        if (ps.get("turn") or 0) != (nxt.get("turn") or 0):
            return {"type": "end_turn"}
        # 手牌数相同但内容变了（抽牌/弃牌）→ 也是 end_turn 之后
        if ph != nh:
            return {"type": "end_turn"}

    # ── 地图 ──
    if psc == "map" and nsc in ("combat", "event", "rest", "shop", "shop_room",
                                "chest", "complete", "boss_relic"):
        if (nxt.get("floor") or 0) > (ps.get("floor") or 0):
            return {"type": "map"}   # node_index 事后由 floor 对齐
        if nsc == "boss_relic":
            return {"type": "boss"}

    # ── 事件：options 变化 ──
    if psc == "event" and nsc in ("event", "map", "combat", "grid_select", "complete"):
        po = ((ps.get("screen_payload") or {}).get("options") or [])
        no = ((nxt.get("screen_payload") or {}).get("options") or [])
        plabels = [(o.get("label") or "").lower() for o in po]
        nlabels = [(o.get("label") or "").lower() for o in no]
        # 找「消失了的那一项」= 你点的那个
        from collections import Counter
        gone = list((Counter(plabels) - Counter(nlabels)).elements())
        if gone:
            for idx, lab in enumerate(plabels):
                if lab == gone[0]:
                    return {"type": "event", "option_index": idx}
        if nsc == "grid_select":
            return {"type": "event", "option_index": 0}

    # ── 选牌 ──
    if psc == "card_reward":
        if (nxt.get("meta") or {}).get("floor") is not None:
            po = ((ps.get("screen_payload") or {}).get("cards") or [])
            nd = [d[0] for d in (nxt.get("deck") or [])]
            pd = [d[0] for d in (ps.get("deck") or [])]
            added = [c for c in nd if nd.count(c) > pd.count(c)]
            if added:
                for idx, cid in enumerate(po):
                    if cid and cid.split("+")[0] == added[0].split("+")[0]:
                        return {"type": "card_pick", "index": idx}
            return {"type": "skip"}

    # ── 战斗奖励 ──
    if psc == "combat_reward":
        po = ((ps.get("screen_payload") or {}).get("items") or [])
        no = ((nxt.get("screen_payload") or {}).get("items") or [])
        if len(no) < len(po) or nsc != "combat_reward":
            return {"type": "reward", "take_indices": [0]}
        if len(no) == len(po):
            return {"type": "reward", "skip": True}

    # ── 营火 ──
    if psc == "rest":
        return {"type": "rest", "option_index": 0}

    # ── 宝箱 ──
    if psc == "chest":
        return {"type": "chest_open"}

    # ── 商店 ──
    if psc == "shop_room":
        if nsc == "shop":
            return {"type": "shop_enter"}
        return {"type": "proceed"}
    if psc == "shop":
        ng = (nxt.get("player") or {}).get("gold") or 0
        pg = (ps.get("player") or {}).get("gold") or 0
        if ng < pg:
            return {"type": "shop_buy_card", "index": 0}
        return {"type": "cancel"}

    # ── Boss 遗物 ──
    if psc == "boss_relic":
        return {"type": "relic", "index": 0}

    # ── 选择屏 ──
    if psc == "grid_select":
        return {"type": "grid_select", "indices": [0]}
    if psc == "hand_select":
        return {"type": "hand_select", "indices": [0]}

    # ── 完成屏 ──
    if psc in ("complete", "game_over"):
        return {"type": "proceed"}

    return None


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: manual_to_trace.py <record_manual 产物> [--out F]")
    src = sys.argv[1]
    out = None
    for i, a in enumerate(sys.argv):
        if a == "--out" and i + 1 < len(sys.argv):
            out = sys.argv[i + 1]
    if out is None:
        out = src.replace(".jsonl", "_trace.jsonl")

    rows = [json.loads(x) for x in open(src, encoding="utf-8")]
    if not rows:
        sys.exit("空文件")

    out_rows = []
    unmapped = 0
    for k, r in enumerate(rows):
        prev = r.get("state")
        nxt = rows[k + 1].get("state") if k + 1 < len(rows) else None
        act = infer_action(prev, nxt) if nxt is not None else None
        if act is None and nxt is not None:
            unmapped += 1
        # trace 语义：state_i 是 action_i 执行**之后**的观测
        out_rows.append({"i": r["i"], "action": act, "state": prev})

    with open(out, "w", encoding="utf-8") as fh:
        for rec in out_rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"转换 {len(out_rows)} 步 -> {out}（未推断出动作 {unmapped} 处）")
    meta = (rows[0]["state"] or {}).get("meta") or {}
    print(f"种子 {meta.get('seed_code')} 进阶 {meta.get('ascension')} "
          f"角色 {meta.get('character')}")
    print(f"\n下一步：PYTHONPATH=python python3 ../neow-sim/tools/parity_replay.py {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
