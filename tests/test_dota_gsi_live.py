"""Dota sem o Copilot, ao vivo numa CÓPIA do servidor (pasta temporária, portas de teste): OpenDota FALSO (fixtures od-*.json),
GSI por POST de verdade no listener 127.0.0.1 e as ops do painel (Steam ID, instalar integração). Ponte do Copilot = porta fechada.
Rodar: py -3 tests/test_dota_gsi_live.py"""
import asyncio
import http.server
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent
SRC = ROOT.parent / "windows" / "server"
FIX = ROOT / "fixtures"
PORT, GSI_PORT, OD_PORT = 8811, 8812, 8813
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
fails = []
OD_CALLS = []
EXTRA = []   # partida que o OpenDota 'passa a ter' depois do pós-jogo


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


def fx(n):
    return json.loads((FIX / f"{n}.json").read_text(encoding="utf-8"))


ROUTES = {"/players/1": "od-player", "/players/1/recentMatches": "od-recent", "/players/1/heroes": "od-heroes", "/players/1/wl": "od-wl",
          "/constants/heroes": "od-constants-heroes", "/constants/items": "od-constants-items"}


class FakeOd(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        OD_CALLS.append(self.path)
        name = ROUTES.get(self.path) or ("od-match" if self.path.startswith("/matches/") else None)
        data = fx(name) if name else {}
        if self.path.endswith("recentMatches") and EXTRA:
            data = EXTRA + data
        body = json.dumps(data).encode()
        self.send_response(200 if name else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        OD_CALLS.append("POST " + self.path)
        self.send_response(405)
        self.end_headers()

    def log_message(self, *a):
        pass


async def recv_until(ws, pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, end - time.time())))
        except asyncio.TimeoutError:
            break
        if pred(m):
            return m
    return None


def post_gsi(name, token):
    d = fx(name)
    d["auth"]["token"] = token
    req = urllib.request.Request(f"http://127.0.0.1:{GSI_PORT}/", data=json.dumps(d).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def panel_call(ws, op, **p):
    await ws.send(json.dumps({"t": "pop", "a": op, "p": p, "id": 99}))
    m = await recv_until(ws, lambda m: m.get("t") == "res" and m.get("id") == 99, 8)
    return m


async def live(srv, dota_root):
    isd = lambda m: m.get("t") == "dota"  # noqa: E731
    phone = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await phone.send(json.dumps({"t": "hello", "token": TOKEN}))
    m = await recv_until(phone, isd, 8)
    check("sem Copilot e sem GSI: tela sem motivo de 'Copilot'", bool(m) and m["d"]["phase"] in ("offline", "menu") and m["d"].get("reason") is None, str(m and m["d"]["phase"]))
    m = await recv_until(phone, lambda m: isd(m) and m["d"]["lobby"] is not None, 10)
    check("lobby vem do OpenDota (medalha, 5 partidas, mais jogados)", bool(m) and m["d"]["lobby"]["mmr"]["medal"] == "Lenda 3" and len(m["d"]["lobby"]["recent"]) == 5
          and m["d"]["lobby"]["top_heroes"][0]["hero"] == "Invoker" and m["d"]["lobby"]["source"] == "opendota", str(m and m["d"]["lobby"] and list(m["d"]["lobby"])))
    check("chamadas ao OpenDota: só GET conhecidos", all(c in ROUTES or c.startswith("/matches/") for c in OD_CALLS), str(OD_CALLS))
    check("img_key e auto_open acompanham", bool(m) and len(m["img_key"]) == 24)
    # GSI
    check("POST com token errado = 403", post_gsi("gsi-game", "errado") == 403)
    cfg = json.loads((srv / "config.json").read_text(encoding="utf-8"))
    token = cfg["dota"]["gsi_token"]
    check("token da GSI foi gerado e gravado no config", len(token) >= 24)
    check("POST válido = 200", post_gsi("gsi-game", token) == 200)
    m = await recv_until(phone, lambda m: isd(m) and m["d"]["phase"] == "game", 6)
    check("celular recebe a fase game pela GSI", bool(m) and m["d"]["companion"]["source"] == "gsi")
    L = m["d"]["live"]
    check("herói com nome do OpenDota e live da GSI", m["d"]["hero"]["hero"] == "Invoker" and L["gold"] == 900 and L["gpm"] == 410 and L["clock_s"] == 400)
    check("itens com nome e id do catálogo, neutro no slot 9", [(x["item"], x["item_id"], x["slot"]) for x in L["items_detail"]][:2] == [("Tango", 44, 0), ("Blink Dagger", 1, 2)] and L["items_detail"][-1]["slot"] == 9)
    check("sem fonte: build/timers/próximo item = null", m["d"]["hero"]["build"] is None and L["timers"] is None and L["next_item"] is None)
    check("hero.mine do OpenDota", m["d"]["hero"]["mine"]["matches"] == 40)
    check("lobby do OpenDota continua", m["d"]["lobby"] is not None)
    check("GSI: draft", post_gsi("gsi-draft", token) == 200 and (await recv_until(phone, lambda m: isd(m) and m["d"]["phase"] == "draft", 5)) is not None)
    check("GSI: pós-jogo com resultado e oficial do OpenDota", post_gsi("gsi-postgame", token) == 200)
    m = await recv_until(phone, lambda m: isd(m) and m["d"]["phase"] == "postgame", 8)
    check("resultado: vitória pela GSI", bool(m) and m["d"]["result"]["win"] is True and m["d"]["result"]["match_id"] == 8000000001)
    check("OpenDota ainda não tem a partida: sem 'oficial' (nada inventado)", "official" not in m["d"]["result"])
    EXTRA.append({"match_id": 8000000001, "player_slot": 0, "radiant_win": True, "hero_id": 74, "start_time": 1795200000, "duration": 2100,
                  "kills": 8, "deaths": 4, "assists": 11, "gold_per_min": 600, "xp_per_min": 700, "hero_damage": 31000, "last_hits": 250})
    m = await recv_until(phone, lambda m: isd(m) and (m["d"].get("result") or {}).get("official"), 45)
    check("oficial aparece quando a partida entra no OpenDota (até 30 s)", bool(m) and m["d"]["result"]["official"]["gpm"] == 600 and m["d"]["result"]["official"]["source"] == "opendota")
    # painel
    pan = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await pan.send(json.dumps({"t": "hello", "panel": PANEL}))
    await recv_until(pan, lambda m: m.get("t") == "panel_ok", 5)
    r = await panel_call(pan, "dota_status")
    d = (r or {}).get("d") or {}
    check("painel: status sem token", bool(r) and token not in json.dumps(r) and d.get("steam_id") == "1", json.dumps(r)[:200] if r else "sem resposta")
    check("painel: GSI ouvindo e payloads contados", d["gsi"]["listening"] is True and d["gsi"]["count"] >= 3 and d["gsi"]["installed"] is False)
    r = await panel_call(pan, "dota_set_steam", steam_id="abc")
    check("painel: Steam ID inválido recusado", bool(r) and not r.get("ok", True))
    r = await panel_call(pan, "dota_gsi_install", folder=str(dota_root / "x-nao-existe"))
    check("painel: pasta que não é do Dota recusada", bool(r) and not r.get("ok", True))
    r = await panel_call(pan, "dota_gsi_install", folder=str(dota_root))
    f = dota_root / "game" / "dota" / "cfg" / "gamestate_integration" / "gamestate_integration_companion.cfg"
    check("painel: instala o .cfg com porta e token certos", f.exists() and f"127.0.0.1:{GSI_PORT}" in f.read_text(encoding="utf-8") and token in f.read_text(encoding="utf-8"))
    r = await panel_call(pan, "dota_status")
    d = (r or {}).get("d") or {}
    check("painel: status instalado", d["gsi"]["installed"] is True and token not in json.dumps(r))
    await pan.close()
    await phone.close()


def main():
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", OD_PORT), FakeOd)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json", "games-history.json", "dota-od.json"))
    dota_root = tmp / "steam" / "steamapps" / "common" / "dota 2 beta"
    (dota_root / "game" / "dota" / "cfg").mkdir(parents=True)
    (srv / "config.json").write_text(json.dumps({
        "token": TOKEN, "panel_token": PANEL,
        "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""},
        "dota": {"bridge": "http://127.0.0.1:9/companion/v1/state", "auto_open": True, "steam_id": "1"}}), encoding="utf-8")
    env = {**os.environ, "COMPANION_OD_BASE": f"http://127.0.0.1:{OD_PORT}", "COMPANION_GSI_PORT": str(GSI_PORT)}
    p = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                socket.create_connection(("127.0.0.1", PORT), 0.5).close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            print("servidor de teste não subiu")
            return 2
        asyncio.run(live(srv, dota_root))
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        cfg = json.loads((srv / "config.json").read_text(encoding="utf-8"))
        check("log sem token da GSI, sem Steam ID e sem nomes", cfg["dota"]["gsi_token"] not in log and "Invoker" not in log and "Jogador Falso" not in log and TOKEN not in log)
        bad = [ln for ln in log.splitlines() if "ERROR" in ln or "Traceback" in ln]
        check("log sem erro", not bad, " | ".join(bad)[:400])
        check("cache do OpenDota em disco", (srv / "dota-od.json").exists())
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        fake.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
