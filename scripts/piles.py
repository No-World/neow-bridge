# -*- coding: utf-8 -*-
"""单发：打印牌堆去向（调试用）"""
import json, socket
s = socket.create_connection(("127.0.0.1", 37771), timeout=60)
f = s.makefile("rwb")
f.write((json.dumps({"id": 1, "cmd": "get_state"}) + "\n").encode()); f.flush()
r = json.loads(f.readline())
obs = r["result"]["observation"]
print("hand:", [c["id"] for c in obs["hand"]])
print("discard:", [c["id"] for c in obs["discard_pile"]])
print("draw_n:", obs["draw_pile"]["count"], "deck_n:", len(obs["master_deck"]))
print("exhaust:", [c["id"] for c in obs["exhaust_pile"]])
ci = obs.get("card_in_use")
print("card_in_use:", ci and ci["id"])
print("monsters:", [[m["id"], m["hp"], m["block"], m["intent"]["type"]] for m in obs["monsters"]])
print("energy(real totalCount)?:", obs["player"]["energy"])
