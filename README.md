# The False Presence relay server (Vanitas)

A tiny websocket relay. It only passes messages between players, no game logic.

## Put it online (Render, free)
1. Make a NEW GitHub repo (call it e.g. `false-presence-server`) and upload these 5 files to its root.
   Keep it separate from the game repo.
2. render.com -> sign in with GitHub -> New -> Web Service -> pick that repo.
   It reads `render.yaml` (Python, free plan, `python relay.py`). Click Deploy.
3. Your link looks like `https://false-presence-relay.onrender.com`. Send it to Claude.
   The game turns https into wss itself and players never see it.

Free services sleep when idle: the first connection can take up to a minute (the game waits 75 s).
If Render asks for a card, run it on a PC instead: `pip install websockets` then `python relay.py`
and `cloudflared tunnel --url http://localhost:8765`.

Test locally: `python test_relay.py` (all lines should say PASS).

## Website + download button
The server's home page (`/`) is now a sleek landing page for the game. `/health` is the plain "ok rooms=N" check.
To turn on the Download button: Render dashboard -> your service -> Environment -> add `APK_URL` = the direct link to the APK
(for example `https://github.com/Atomicend2/The-false-presence/releases/latest/download/the_false_presence.apk`, which only works
if that repo is public). Save and Render restarts it.
