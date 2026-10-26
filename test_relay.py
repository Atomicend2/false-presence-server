import asyncio, json, subprocess, sys, time
import websockets

async def rx(ws, want, n=20):
    for _ in range(n):
        m = json.loads(await asyncio.wait_for(ws.recv(), 3))
        if m["t"] == want:
            return m
    raise AssertionError("never got " + want)

async def main():
    p = subprocess.Popen([sys.executable, "relay.py"], env={"PORT": "18765", "PATH": "/usr/bin:/bin:/usr/local/bin"})
    await asyncio.sleep(1.0)
    ok = 0
    def check(c, msg):
        nonlocal ok
        print(("PASS " if c else "FAIL ") + msg)
        ok += 0 if c else 1
    try:
        h = await websockets.connect("ws://127.0.0.1:18765")
        await h.send(json.dumps({"t": "create", "name": "Host", "token": "tokH"}))
        j = await rx(h, "joined"); code = j["code"]
        check(len(code) == 5 and j["id"] == 1, "create room gives 5-letter code")
        g = await websockets.connect("ws://127.0.0.1:18765")
        await g.send(json.dumps({"t": "join", "code": code.lower(), "name": "Guest", "token": "tokG"}))
        j2 = await rx(g, "joined")
        check(j2["id"] == 2, "guest joins by code (case-insensitive)")
        await h.send(json.dumps({"t": "pick", "char": "REN"}))
        await g.send(json.dumps({"t": "pick", "char": "REN"}))
        e = await rx(g, "error")
        check("taken" in e["msg"], "same character cannot be picked twice")
        await g.send(json.dumps({"t": "pick", "char": "MIO"}))
        lob = await rx(h, "lobby")
        await g.send(json.dumps({"t": "ready", "v": True}))
        await h.send(json.dumps({"t": "ready", "v": True}))
        await asyncio.sleep(0.3)
        await h.send(json.dumps({"t": "start"}))
        s = await rx(g, "start")
        check({p["char"] for p in s["players"]} == {"REN", "MIO"}, "start broadcasts chosen characters")
        await h.send(json.dumps({"t": "msg", "to": 0, "d": {"k": "pos", "x": 1}}))
        m = await rx(g, "msg")
        check(m["from"] == 1 and m["d"]["x"] == 1, "broadcast message reaches guest with sender id")
        await g.send(json.dumps({"t": "msg", "to": 1, "d": {"k": "interact", "id": 7}}))
        m = await rx(h, "msg")
        check(m["from"] == 2 and m["d"]["id"] == 7, "guest message reaches host")
        bad = await websockets.connect("ws://127.0.0.1:18765")
        await bad.send(json.dumps({"t": "join", "code": "ZZZZZ", "name": "x"}))
        e = await rx(bad, "error")
        check("No room" in e["msg"], "unknown code is rejected")
        # --- dropped guest keeps seat and can rejoin
        await g.close()
        pa = await rx(h, "peer_away")
        check(pa["id"] == 2, "host is told a guest dropped (peer_away), seat kept")
        g2 = await websockets.connect("ws://127.0.0.1:18765")
        await g2.send(json.dumps({"t": "rejoin", "code": code, "token": "tokG"}))
        rj = await rx(g2, "rejoined")
        check(rj["id"] == 2 and rj["started"], "guest rejoins the same seat with the token")
        pb = await rx(h, "peer_back")
        check(pb["id"] == 2, "host is told the guest is back")
        bad2 = await websockets.connect("ws://127.0.0.1:18765")
        await bad2.send(json.dumps({"t": "rejoin", "code": code, "token": "nope"}))
        e = await rx(bad2, "error")
        check(e.get("gone") is True, "wrong token cannot take a seat")
        # --- host drops: room stays, guest told to wait, host comes back
        await h.close()
        ha = await rx(g2, "host_away")
        check(ha["id"] == 1, "guests are told the host dropped, room kept")
        h2 = await websockets.connect("ws://127.0.0.1:18765")
        await h2.send(json.dumps({"t": "rejoin", "code": code, "token": "tokH"}))
        rj = await rx(h2, "rejoined")
        check(rj["id"] == 1, "host rejoins as host")
        hb = await rx(g2, "host_back")
        check(hb["id"] == 1, "guests are told the host is back")
        # --- explicit leave still ends things
        await h2.send(json.dumps({"t": "leave"}))
        hl = await rx(g2, "host_left")
        check(True, "explicit host leave ends the room")
    finally:
        p.terminate()
    print("failures:", ok)
    sys.exit(ok)

asyncio.run(main())
