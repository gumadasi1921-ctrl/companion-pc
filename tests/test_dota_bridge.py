"""Ponte do DotaCopilot: unidade (dota.py com relógio e opener falsos) e ao vivo numa CÓPIA do servidor
(pasta temporária, porta de teste) apontada para uma ponte FALSA em outra porta, que serve os JSON de
tests/fixtures/dota-<fase>.json. Sem Dota aberto o servidor consulta a cada 10 s, então leva ~30 s.
Rodar: py -3 tests/test_dota_bridge.py"""
import asyncio
import copy
import http.server
import json
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent
SRC = ROOT.parent / "windows" / "server"
FIX = ROOT / "fixtures"
PORT, BRIDGE_PORT = 8796, 8797
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
PHASES = ("offline", "menu", "draft", "pregame", "game", "postgame")
FIXTURES = {p: json.loads((FIX / f"dota-{p}.json").read_text(encoding="utf-8")) for p in PHASES}
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


# ------------------------------------------------------------------ unidade
def unit():
    sys.path.insert(0, str(SRC))
    import dota  # noqa: E402

    for p, d in FIXTURES.items():
        keys = {"v", "updated_at", "phase", "lobby", "hero", "live", "result"}
        check(f"fixture {p} tem todos os blocos do contrato", set(d) == keys and d["v"] == 1 and d["phase"] == p)

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self, n):
            return self.body[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    now = [100.0]
    answer = {"body": json.dumps(FIXTURES["menu"]).encode(), "err": None}

    def opener(req, timeout):
        check_timeout.append(timeout)
        if answer["err"]:
            raise answer["err"]
        return Resp(answer["body"])

    check_timeout = []
    b = dota.DotaBridge({"bridge": "http://127.0.0.1:1/companion/v1/state"}, clock=lambda: now[0], opener=opener)
    check("consulta na primeira vez", b.due(False) and b.due(True))
    s = b.poll()
    check("repassa o JSON da ponte", s == FIXTURES["menu"])
    check("timeout de 300 ms", check_timeout == [0.3])
    now[0] += 0.5
    check("Dota aberto: espera 1 s", not b.due(True))
    now[0] += 0.5
    check("Dota aberto: 1×/s", b.due(True) and not b.due(False))
    now[0] += 9
    check("Dota fechado: a cada 10 s", b.due(False))
    for err in (urllib.error.URLError("recusado"), TimeoutError(), ConnectionResetError()):
        answer["err"] = err
        s = b.poll()
        check(f"ponte fora do ar ({type(err).__name__}) = offline com motivo",
              s["phase"] == "offline" and s["reason"] == "DotaCopilot fechado" and s["lobby"] is None)
    answer["err"] = None
    for bad in (b"{", b"[]", json.dumps({"v": 2, "phase": "menu"}).encode(), json.dumps({"v": 1, "phase": "x"}).encode()):
        answer["body"] = bad
        check(f"resposta fora do contrato = offline ({bad[:20]!r})", b.poll()["phase"] == "offline")
    far = dota.DotaBridge({"bridge": "http://192.168.0.10:32123/companion/v1/state"}, opener=opener)
    check("só ponte local", far.poll()["phase"] == "offline")
    a, c = copy.deepcopy(FIXTURES["game"]), copy.deepcopy(FIXTURES["game"])
    c["updated_at"] += 5
    check("updated_at sozinho não conta como mudança", dota.same(a, c))
    c["live"]["gold"] += 1
    check("mudança de campo conta", not dota.same(a, c))
    check("estado inicial é diferente de qualquer estado", not dota.same(None, a))
    check("auto_open padrão ligado", dota.DotaBridge().auto_open is True)
    check("auto_open do config.json", dota.DotaBridge({"auto_open": False}).auto_open is False)


# ------------------------------------------------------------------ ponte falsa
CURRENT = {"d": FIXTURES["menu"]}


class Fake(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = json.dumps(CURRENT["d"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


async def recv_until(ws, pred, timeout):
    end = time.time() + timeout
    seen = []
    while time.time() < end:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, end - time.time())))
        except asyncio.TimeoutError:
            break
        seen.append(m)
        if pred(m):
            return m, seen
    return None, seen


async def req(ws, a, **p):
    await ws.send(json.dumps({"t": "req", "id": 7, "a": a, "p": p}))
    m, _ = await recv_until(ws, lambda m: m.get("t") == "res" and m.get("id") == 7, 5)
    return m


async def live(fake):
    is_dota = lambda m: m.get("t") == "dota"  # noqa: E731
    phone = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await phone.send(json.dumps({"t": "hello", "token": TOKEN}))
    m, seen = await recv_until(phone, is_dota, 5)
    welcome = next((x for x in seen if x.get("t") == "welcome"), {})
    check("welcome anuncia a tela Dota", welcome.get("features", {}).get("dota") is True)
    check("celular recebe o estado da ponte ao conectar", bool(m) and m["d"]["phase"] == "menu" and m["auto_open"] is True)
    check("o estado é o JSON da ponte, sem mudanças", bool(m) and m["d"] == FIXTURES["menu"])
    for p in PHASES:
        CURRENT["d"] = FIXTURES[p]
        r = await req(phone, "dota.get")
        check(f"dota.get com a ponte em {p}", bool(r) and r["ok"] and r["d"]["d"] == FIXTURES[p])
    CURRENT["d"] = FIXTURES["menu"]
    await req(phone, "dota.get")
    # só updated_at mudou: não reenvia
    CURRENT["d"] = {**FIXTURES["menu"], "updated_at": FIXTURES["menu"]["updated_at"] + 9}
    m, _ = await recv_until(phone, is_dota, 11)
    check("sem mudança (só updated_at) não envia", m is None)
    CURRENT["d"] = FIXTURES["game"]
    m, _ = await recv_until(phone, is_dota, 11)
    check("mudou para game: envia", bool(m) and m["d"]["phase"] == "game")
    fake.shutdown()
    fake.server_close()
    m, _ = await recv_until(phone, is_dota, 11)
    check("ponte fora do ar: offline com o motivo", bool(m) and m["d"]["phase"] == "offline" and m["d"].get("reason") == "DotaCopilot fechado")
    await phone.close()


def main():
    unit()
    fake = http.server.ThreadingHTTPServer(("127.0.0.1", BRIDGE_PORT), Fake)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json", "games-history.json"))
    (srv / "config.json").write_text(json.dumps({
        "token": TOKEN, "panel_token": PANEL,
        "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""},
        "dota": {"bridge": f"http://127.0.0.1:{BRIDGE_PORT}/companion/v1/state", "auto_open": True}}), encoding="utf-8")
    p = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        asyncio.run(live(fake))
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        lines = [ln for ln in log.splitlines() if "DotaCopilot" in ln]
        check("log: ponte ok e fora do ar, uma vez cada",
              sum("ponte do DotaCopilot ok" in ln for ln in lines) == 1 and sum("fora do ar" in ln for ln in lines) == 1,
              f"{len(lines)} linhas")
        check("log sem conteúdo da ponte", "Invoker" not in log and "Blink" not in log and "dota.get" not in log)
        check("log sem token", TOKEN not in log and PANEL not in log)
        check("log sem erro", "Traceback" not in log and " ERROR " not in log)
        print("\n".join(lines))
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
