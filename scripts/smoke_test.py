# -*- coding: utf-8 -*-
"""NeowBridge 一次性冒烟测试：ping -> new_run -> 数步 step（不入协议层）"""
import json, socket, sys

HOST, PORT = "127.0.0.1", 37771
s = socket.create_connection((HOST, PORT), timeout=120)
f = s.makefile("rwb")

def rpc(id_, cmd, **kw):
    req = {"id": id_, "cmd": cmd}; req.update(kw)
    f.write((json.dumps(req) + "\n").encode()); f.flush()
    line = f.readline()
    if not line:
        print("!! connection closed"); sys.exit(1)
    return json.loads(line)

r = rpc(1, "ping"); print("PING:", json.dumps(r["result"], ensure_ascii=False))
assert r["ok"]

r = rpc(2, "new_run", character="IRONCLAD", ascension=20, seed="PMCWKZEF")
print("NEW_RUN ok=", r["ok"])
if not r["ok"]:
    print(json.dumps(r, ensure_ascii=False)[:500]); sys.exit(1)
res = r["result"]; obs = res["observation"]; info = res["info"]
print("screen=%s act=%s floor=%s asc=%s seed=%s" % (
    info["screen"], obs["meta"].get("act"), obs["meta"].get("floor"),
    obs["meta"].get("ascension"), obs["meta"].get("seed")))
print("legal[0:6]:", json.dumps(res["legal_actions"][:6], ensure_ascii=False))
scr = obs["screen"]
print("screen.type=%s" % scr["type"])
if scr["type"] == "event":
    print("event=%s options=%s" % (scr.get("id"), [o["label"] for o in scr.get("options", [])][:6]))
    # 选第一个可用选项
    r3 = rpc(3, "step", action={"type": "event", "option_index": 0})
    print("STEP event ok=", r3["ok"], "screen=", r3["result"]["info"]["screen"] if r3["ok"] else r3.get("error"))
r4 = rpc(4, "get_state")
res = r4["result"]; obs = res["observation"]
print("STATE screen=%s hand=%s deck=%s relics=%s gold=%s" % (
    res["info"]["screen"], len(obs.get("hand", [])), len(obs.get("master_deck", [])),
    [x["id"] for x in obs.get("relics", [])][:4], obs["player"]["gold"]))
print("SMOKE OK")
