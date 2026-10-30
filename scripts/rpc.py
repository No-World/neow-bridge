# -*- coding: utf-8 -*-
"""手动单发调试 CLI：一次一条命令，打印紧凑摘要。
用法:
  rpc.py ping
  rpc.py get_state
  rpc.py new_run IRONCLAD 20 PMCWKZEF
  rpc.py step '{"type":"event","option_index":0}'
  rpc.py raw          # 完整 JSON
"""
import json, socket, sys

HOST, PORT = "127.0.0.1", 37771

def call(req):
    s = socket.create_connection((HOST, PORT), timeout=120)
    f = s.makefile("rwb")
    f.write((json.dumps(req) + "\n").encode()); f.flush()
    line = f.readline()
    s.close()
    return json.loads(line)

def summary(r):
    if not r.get("ok"):
        return "ERR[%s] %s" % (r.get("error_type"), r.get("error"))
    res = r.get("result", {})
    if "observation" not in res:
        return json.dumps(res, ensure_ascii=False)
    obs = res["observation"]; info = res["info"]
    out = []
    m = obs["meta"]
    out.append("screen=%s act=%s floor=%s turn=%s" % (info["screen"], m.get("act"), m.get("floor"), m.get("turn")))
    p = obs["player"]
    out.append("hp=%s/%s en=%s gold=%s block=%s" % (p["hp"], p["max_hp"], p["energy"], p["gold"], p["block"]))
    hand = [(c["id"][:14] + ("+" if c["upgraded"] else ""), c["cost_for_turn"]) for c in obs.get("hand", [])]
    out.append("hand=" + " ".join("%s(%s)" % c for c in hand))
    if obs.get("monsters"):
        out.append("monsters=" + " ".join("%s:%s(+%sb)%s[i:%s%s]" % (
            x["id"][:12], x["hp"], x.get("block", 0), "*" if x["is_gone"] else "",
            x["intent"]["type"][:4],
            ("d" + str(x["intent"].get("damage", ""))) if x["intent"].get("damage") else "") for x in obs["monsters"]))
    scr = obs["screen"]; st = scr["type"]
    if st == "event":
        out.append("event=%s opts=%s" % (scr.get("id"), [o["label"] for o in scr.get("options", [])]))
    elif st == "map":
        mm = obs["map"]
        out.append("map choices=%s current=%s" % (mm.get("choices"), mm.get("current")))
    elif st == "combat_reward":
        out.append("rewards=%s" % [(i["type"], i.get("gold", "")) for i in scr.get("items", [])])
    elif st == "card_reward":
        out.append("cards=%s" % [c["id"] for c in scr.get("cards", [])])
    elif st in ("hand_select", "grid_select"):
        out.append("%s %s" % (st, {k: v for k, v in scr.items() if k in ("num", "num_to_select", "any_number", "selection_reason", "pile", "can_pick_zero")}))
    elif st == "rest":
        out.append("rest opts=%s" % [(o["name"], o["usable"]) for o in scr.get("options", [])])
    out.append("legal=" + json.dumps(res["legal_actions"], ensure_ascii=False)[:300])
    def pcnt(k):
        v = obs.get(k)
        return v.get("count", 0) if isinstance(v, dict) else (len(v) if isinstance(v, list) else 0)
    out.append("piles draw=%s discard=%s exhaust=%s deck=%s" % (
        pcnt("draw_pile"), pcnt("discard_pile"), pcnt("exhaust_pile"), pcnt("master_deck")))
    dpile = obs.get("discard_pile")
    if isinstance(dpile, dict) and dpile.get("cards"):
        out.append("discard(" + dpile.get("order", "?") + ")= " + " ".join(
            c["id"] + ("+" if c["upgraded"] else "") for c in dpile["cards"]))
    out.append("done=%s reward=%s" % (res.get("done"), res.get("reward")))
    return "\n  ".join(out)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    _id = 1
    if cmd == "ping": req = {"id": _id, "cmd": "ping"}
    elif cmd == "get_state": req = {"id": _id, "cmd": "get_state"}
    elif cmd == "new_run":
        req = {"id": _id, "cmd": "new_run", "character": sys.argv[2] if len(sys.argv) > 2 else "IRONCLAD",
               "ascension": int(sys.argv[3]) if len(sys.argv) > 3 else 20}
        if len(sys.argv) > 4: req["seed"] = sys.argv[4]
    elif cmd == "step": req = {"id": _id, "cmd": "step", "action": json.loads(sys.argv[2])}
    elif cmd == "raw": cmd = sys.argv[2]; req = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {"id": _id, "cmd": cmd}
    else: req = {"id": _id, "cmd": cmd}
    r = call(req)
    if cmd == "raw" or "--raw" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
    else:
        print(summary(r))
