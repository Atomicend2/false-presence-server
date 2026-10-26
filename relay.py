#!/usr/bin/env python3
"""THE FALSE PRESENCE relay server.

Keeps rooms (5-letter codes) and forwards messages between the host and the guests
of a room. It contains NO game logic. Free to run (see SERVER_SETUP.md).

  pip install -r requirements.txt
  python relay.py            # listens on $PORT (default 8765)
"""
import asyncio, json, os, random, string, time
from websockets.asyncio.server import serve
from websockets.http11 import Response
from websockets.datastructures import Headers

MAX_PLAYERS = 4
MAX_ROOMS = 300
MAX_FRAME = 8192
ROOM_IDLE_SECONDS = 60 * 60
PLAYER_GRACE = 15 * 60     # a dropped player keeps their seat this long
HOST_GRACE = 30 * 60       # a dropped host (or an empty started room) is kept this long
CHARS = [c for c in string.ascii_uppercase if c not in "IOQ"]
CHARACTERS = {"REN", "MIO", "KENJI", "YUZU"}

REAP_EVERY = int(os.environ.get("REAP_EVERY", "60"))
rooms: dict[str, "Room"] = {}


class Room:
    def __init__(self, code):
        self.code = code
        self.players = {}      # id -> dict(name, char, ready, ws)
        self.host_id = 1
        self.started = False
        self.touched = time.time()

    def public(self):
        return [
            {"id": i, "name": p["name"], "char": p["char"], "ready": p["ready"], "host": i == self.host_id, "away": p["ws"] is None}
            for i, p in sorted(self.players.items())
        ]

    async def broadcast(self, obj, skip=None):
        data = json.dumps(obj, separators=(",", ":"))
        for i, p in list(self.players.items()):
            if i != skip and p["ws"] is not None:
                try:
                    await p["ws"].send(data)
                except Exception:
                    pass

    async def lobby(self):
        await self.broadcast({"t": "lobby", "players": self.public(), "started": self.started})


def new_code():
    for _ in range(50):
        c = "".join(random.choice(CHARS) for _ in range(5))
        if c not in rooms:
            return c
    return None


async def send(ws, obj):
    await ws.send(json.dumps(obj, separators=(",", ":")))


async def handler(ws):
    room = None
    my_id = 0
    left_clean = False
    try:
        async for raw in ws:
            if isinstance(raw, bytes) or len(raw) > MAX_FRAME:
                continue
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            t = m.get("t")
            if t == "create" and room is None:
                if len(rooms) >= MAX_ROOMS:
                    await send(ws, {"t": "error", "msg": "Server is full, try again in a minute."})
                    continue
                code = new_code()
                room = Room(code)
                rooms[code] = room
                my_id = 1
                room.players[1] = {"name": str(m.get("name", "Host"))[:16], "char": "", "ready": False, "ws": ws, "token": str(m.get("token", ""))[:40], "away_since": 0}
                await send(ws, {"t": "joined", "code": code, "id": 1, "host_id": 1})
                await room.lobby()
            elif t == "join" and room is None:
                code = str(m.get("code", "")).upper().strip()
                r = rooms.get(code)
                if r is None:
                    await send(ws, {"t": "error", "msg": "No room with that code."})
                    continue
                if r.started:
                    await send(ws, {"t": "error", "msg": "That game already started."})
                    continue
                if len(r.players) >= MAX_PLAYERS:
                    await send(ws, {"t": "error", "msg": "Room is full (4 players)."})
                    continue
                room = r
                my_id = min(i for i in range(2, 2 + MAX_PLAYERS) if i not in r.players)
                r.players[my_id] = {"name": str(m.get("name", "Player"))[:16], "char": "", "ready": False, "ws": ws, "token": str(m.get("token", ""))[:40], "away_since": 0}
                await send(ws, {"t": "joined", "code": code, "id": my_id, "host_id": r.host_id})
                await r.lobby()
            elif t == "rejoin" and room is None:
                r = rooms.get(str(m.get("code", "")).upper().strip())
                tok = str(m.get("token", ""))[:40]
                seat = None
                if r is not None and tok:
                    for i, p in r.players.items():
                        if p.get("token") == tok:
                            seat = i
                if r is None or seat is None:
                    await send(ws, {"t": "error", "msg": "That room is gone (or your seat was given up).", "gone": True})
                    continue
                old = r.players[seat]["ws"]
                r.players[seat]["ws"] = ws
                r.players[seat]["away_since"] = 0
                room = r
                my_id = seat
                room.touched = time.time()
                if old is not None and old is not ws:
                    try:
                        await old.close()
                    except Exception:
                        pass
                await send(ws, {"t": "rejoined", "code": r.code, "id": seat, "host_id": r.host_id, "players": r.public(), "started": r.started})
                await r.broadcast({"t": "host_back" if seat == r.host_id else "peer_back", "id": seat}, skip=seat)
                await r.lobby()
            elif room is None:
                continue
            else:
                room.touched = time.time()
                me = room.players.get(my_id)
                if me is None:
                    continue
                if t == "pick" and not room.started:
                    ch = str(m.get("char", "")).upper()
                    if ch in CHARACTERS and all(p["char"] != ch for i, p in room.players.items() if i != my_id):
                        me["char"] = ch
                        me["ready"] = False
                        await room.lobby()
                    else:
                        await send(ws, {"t": "error", "msg": "That character is taken."})
                elif t == "ready" and not room.started:
                    me["ready"] = bool(m.get("v")) and me["char"] != ""
                    await room.lobby()
                elif t == "start" and my_id == room.host_id and not room.started:
                    ps = room.players.values()
                    if all(p["char"] != "" for p in ps) and all(p["ready"] for i, p in room.players.items() if i != room.host_id):
                        room.started = True
                        await room.broadcast({"t": "start", "players": room.public()})
                    else:
                        await send(ws, {"t": "error", "msg": "Everyone must pick a character and be ready."})
                elif t == "msg":
                    out = {"t": "msg", "from": my_id, "d": m.get("d")}
                    to = int(m.get("to", 0))
                    if to == 0:
                        await room.broadcast(out, skip=my_id)
                    elif to in room.players:
                        try:
                            await room.players[to]["ws"].send(json.dumps(out, separators=(",", ":")))
                        except Exception:
                            pass
                elif t == "leave":
                    left_clean = True
                    break
    except Exception:
        pass
    finally:
        seat_p = room.players.get(my_id) if room is not None else None
        if seat_p is not None and seat_p["ws"] is not ws:
            pass   # this seat was already taken over by a rejoin
        elif room is not None and room.started and seat_p is not None and not left_clean:
            # dropped mid-game: keep the seat so they can come back with the room code
            seat_p["ws"] = None
            seat_p["away_since"] = time.time()
            room.touched = time.time()
            await room.broadcast({"t": "host_away" if my_id == room.host_id else "peer_away", "id": my_id})
        elif room is not None and my_id in room.players:
            del room.players[my_id]
            if my_id == room.host_id or not room.players:
                await room.broadcast({"t": "host_left"})
                rooms.pop(room.code, None)
            else:
                await room.broadcast({"t": "peer_left", "id": my_id})
                await room.lobby()


async def health(connection, request):
    # plain HTTP hit (health check / uptime ping): answer 200 so free hosts keep us alive
    if request.headers.get("Upgrade", "").lower() != "websocket":
        body = ("ok rooms=%d\n" % len(rooms)).encode()
        return Response(200, "OK", Headers([("Content-Type", "text/plain"), ("Content-Length", str(len(body)))]), body)
    return None


async def reaper():
    while True:
        await asyncio.sleep(REAP_EVERY)
        now = time.time()
        for code in [c for c, r in rooms.items() if now - r.touched > ROOM_IDLE_SECONDS]:
            r = rooms.pop(code)
            await r.broadcast({"t": "host_left"})
        for code, r in list(rooms.items()):
            away = {i: p for i, p in r.players.items() if p["ws"] is None}
            host_gone = r.host_id in away and now - away[r.host_id]["away_since"] > HOST_GRACE
            all_gone = len(away) == len(r.players) and all(now - p["away_since"] > HOST_GRACE for p in away.values())
            if host_gone or all_gone:
                rooms.pop(code, None)
                await r.broadcast({"t": "host_left"})
                continue
            for i, p in away.items():
                if i != r.host_id and now - p["away_since"] > PLAYER_GRACE:
                    del r.players[i]
                    await r.broadcast({"t": "peer_left", "id": i})


async def main():
    port = int(os.environ.get("PORT", "8765"))
    async with serve(handler, "0.0.0.0", port, process_request=health, max_size=MAX_FRAME, ping_interval=20, ping_timeout=30):
        print("relay listening on", port, flush=True)
        await reaper()


if __name__ == "__main__":
    asyncio.run(main())
