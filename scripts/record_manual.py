# -*- coding: utf-8 -*-
"""手动对局录制器：你在游戏里自己打，它在后台把每一步**状态 + 合法动作**记下来。

## 为什么要它

`*.run` 只有结果层决策（选牌/事件/遗物），**没有战斗内逐回合出牌**。
而 bridge 的 step 是「发动作」，玩家手动点击时 bridge 不知道你点了什么。

本录制器用**状态轮询 + 差分**反推动作：
  · 每 ~0.4s 读一次 get_state
  · 状态（规则性字段）变化就记一条：完整快照 + 当时的 legal_actions
  · 事后 `manual_to_trace.py` 用「下一状态」为目标，在 legal_actions 里
    搜出能达成的那个动作 → 产出 parity_replay.py 可读的 golden trace

## 用法

  1. 先启动游戏（scripts/launch.sh），进到主菜单
  2. 运行：python3 scripts/record_manual.py --out traces/manual_heart.jsonl
  3. 在游戏窗口里正常玩（新开一局、选种子/进阶、打到碎心）
  4. 打完（或想停）按 Ctrl+C，脚本落盘并打印摘要

⚠️ 素材用途硬约束（docs/parity-spec.md §1.5）：
   本录制器产出的是**对拍素材**（验证引擎与真游戏一致），
   **不是**训练数据。禁止用于行为克隆 / 监督学习 / 评估基线。
"""
import argparse
import hashlib
import json
import os
import socket
import sys
import time

PORT = int(os.environ.get("NEOW_BRIDGE_PORT", "45000"))


class Rpc:
    def __init__(self):
        self.s = None
        self.f = None

    def connect(self):
        self.s = socket.create_connection(("127.0.0.1", PORT), timeout=30)
        self.f = self.s.makefile("rwb")

    def call(self, cmd, **kw):
        for _ in range(3):
            try:
                if self.f is None:
                    self.connect()
                req = {"id": 1, "cmd": cmd}
                req.update(kw)
                self.f.write((json.dumps(req) + "\n").encode())
                self.f.flush()
                line = self.f.readline()
                if not line:
                    raise ConnectionError("closed")
                return json.loads(line)
            except (ConnectionError, OSError, json.JSONDecodeError):
                time.sleep(0.5)
                try:
                    self.connect()
                except OSError:
                    time.sleep(1)
        return {"ok": False, "error": "rpc failed"}


def rule_state(obs, info):
    """只取「规则性」字段（排除动画/UI 抖动），用于检测真实状态变化。"""
    p = obs.get("player") or {}
    scr = obs.get("screen") or {}
    return {
        "screen": scr.get("type"),
        "floor": info.get("floor"),
        "act": info.get("act"),
        "turn": (obs.get("meta") or {}).get("turn"),
        "hp": p.get("hp"),
        "max_hp": p.get("max_hp"),
        "block": p.get("block"),
        "energy": p.get("energy"),
        "gold": p.get("gold"),
        "powers": p.get("powers"),
        "hand": [(c.get("id"), c.get("upgraded"), c.get("cost_for_turn"))
                 for c in (obs.get("hand") or [])],
        "monsters": [(m.get("id"), m.get("hp"), m.get("block"), m.get("is_gone"))
                     for m in (obs.get("monsters") or [])],
        "draw_n": (obs.get("draw_pile") or {}).get("count"),
        "discard_n": (obs.get("discard_pile") or {}).get("count"),
        "exhaust_n": (obs.get("exhaust_pile") or {}).get("count"),
        "deck_n": (obs.get("master_deck") or {}).get("count"),
        "relics": [(r.get("id"), r.get("counter")) for r in (obs.get("relics") or [])],
        "options": [o.get("label") for o in (scr.get("options") or [])],
        "rewards": [r.get("type") for r in (scr.get("items") or [])],
        "rng": obs.get("rng"),
    }


def snapshot(obs, info):
    """与 tests/conformance/record_trace.py 同格式（parity_replay.py 可读）。"""
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
        "meta": {
            "seed": (obs.get("meta") or {}).get("seed"),
            "seed_code": (obs.get("meta") or {}).get("seed_code"),
            "ascension": (obs.get("meta") or {}).get("ascension"),
            "character": (obs.get("meta") or {}).get("character"),
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="traces/manual_run.jsonl")
    ap.add_argument("--poll", type=float, default=0.35, help="轮询间隔（秒）")
    ap.add_argument("--cheat-draw-order", action="store_true", default=True,
                    help="打开抽牌堆真实顺序（对拍需要）")
    args = ap.parse_args()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    rpc = Rpc()
    r = rpc.call("ping")
    if not r.get("ok"):
        sys.exit("连不上 bridge：先运行 scripts/launch.sh")
    print("bridge:", r["result"].get("bridge_version"), "screen:", r["result"].get("screen"))
    if args.cheat_draw_order:
        rpc.call("config", cheat_draw_order=True)

    fh = open(out_path, "w", encoding="utf-8")
    n = 0
    last_hash = None
    t0 = time.time()
    started = False

    print("\n开始录制。请在游戏窗口里正常玩（新开一局 → 打到碎心）。")
    print("状态变化会自动记录；按 Ctrl+C 结束。\n")

    try:
        while True:
            r = rpc.call("get_state")
            if not r.get("ok"):
                time.sleep(args.poll)
                continue
            res = r["result"]
            obs = res.get("observation")
            info = res.get("info", {})
            if obs is None:
                time.sleep(args.poll)
                continue

            # 未开局（主菜单/角色选择）时不记录
            if not info.get("in_dungeon"):
                if started:
                    print("  [离开对局] 结束录制")
                    break
                time.sleep(args.poll)
                continue
            started = True

            rs = rule_state(obs, info)
            h = hashlib.md5(json.dumps(rs, sort_keys=True, ensure_ascii=False)
                            .encode()).hexdigest()
            if h != last_hash:
                last_hash = h
                rec = {
                    "i": n,
                    "t": round(time.time() - t0, 2),
                    "action": None,          # 由 manual_to_trace.py 事后推断
                    "state": snapshot(obs, info),
                    "legal": [(a.get("type"), a.get("entity"), a.get("target"),
                               a.get("card_index"), a.get("option_index"), a.get("index"))
                              for a in (res.get("legal_actions") or [])],
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                n += 1
                p = obs.get("player") or {}
                print("  [%5.1fs] #%-4d F%-2s %-14s hp%s/%s 金%s 手%d 怪%s" % (
                    time.time() - t0, n, info.get("floor"),
                    (obs.get("screen") or {}).get("type"),
                    p.get("hp"), p.get("max_hp"), p.get("gold"),
                    len(obs.get("hand") or []),
                    len([m for m in (obs.get("monsters") or []) if not m.get("is_gone")])),
                    flush=True)
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\n[Ctrl+C] 停止录制")
    finally:
        fh.close()

    # 摘要
    print("\n=== 录制摘要 ===")
    print("  步数:", n)
    print("  产物:", out_path)
    if n:
        rows = [json.loads(x) for x in open(out_path, encoding="utf-8")]
        meta = (rows[0]["state"] or {}).get("meta") or {}
        print("  种子:", meta.get("seed_code"), "| 进阶:", meta.get("ascension"),
              "| 角色:", meta.get("character"))
        floors = sorted({(r["state"] or {}).get("floor") for r in rows
                         if (r["state"] or {}).get("floor") is not None})
        print("  覆盖楼层:", floors[0] if floors else "-", "→", floors[-1] if floors else "-")
    print("\n下一步：python3 scripts/manual_to_trace.py %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
