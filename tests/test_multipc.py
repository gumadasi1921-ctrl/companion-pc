"""Vários PCs: unidades do multipc.py + teste ao vivo numa CÓPIA do servidor (porta de teste, mutex de teste).
Confere welcome (pc_id/pc_name/proto), Origin (recusa estranha, aceita a própria e a da lista), CORS só para a lista,
X-Forwarded-For (painel recusado por proxy; IP real no pareamento) e que o log não traz token.
Rodar: py -3 tests/test_multipc.py"""
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

SRC = Path(__file__).resolve().parent.parent / "windows" / "server"
sys.path.insert(0, str(SRC))
import multipc  # noqa: E402

PORT = 8794
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
FAILS = []
APP = "https://casa.exemplo.ts.net"


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        FAILS.append(name)


def units():
    check("pc_id novo é válido", multipc.valid_pc_id(multipc.new_pc_id()))
    check("pc_id inválido recusado", not multipc.valid_pc_id("abc") and not multipc.valid_pc_id(None))
    check("sem Origin passa", multipc.origin_ok(None, "x:1", []))
    check("mesma origem passa", multipc.origin_ok("http://192.168.0.2:8770", "192.168.0.2:8770", []))
    check("origem estranha recusada", not multipc.origin_ok("https://mal.com", "192.168.0.2:8770", [APP]))
    check("origem da lista passa", multipc.origin_ok(APP, "192.168.0.2:8770", [APP]))
    check("Origin null recusada", not multipc.origin_ok("null", "x", [APP]))
    check("CORS só da lista", multipc.cors_headers(APP, "h", [APP]) == {"Access-Control-Allow-Origin": APP, "Vary": "Origin"}
          and multipc.cors_headers("https://mal.com", "h", [APP]) == {} and multipc.cors_headers(None, "h", [APP]) == {})
    check("CORS nunca *", "*" not in multipc.cors_headers(APP, "h", [APP]).values())
    check("XFF só vale vindo do loopback", multipc.client_ip("192.168.0.9", "1.2.3.4") == "192.168.0.9")
    check("XFF do loopback vale (último item)", multipc.client_ip("127.0.0.1", "9.9.9.9, 100.64.0.7") == "100.64.0.7")
    check("XFF inválido ignorado", multipc.client_ip("127.0.0.1", "lixo") == "127.0.0.1")
    check("origens limpas", multipc.clean_origins([" HTTPS://Casa.Exemplo.ts.net/ "]) == [APP])
    for bad in (["https://a.com/caminho"], ["ftp://a.com"], ["a"] * 6, "x", ["https://a b"]):
        try:
            multipc.clean_origins(bad)
            check(f"origem inválida recusada {bad!r}", False)
        except ValueError:
            check(f"origem inválida recusada {bad!r}", True)
    check("link remoto só leva o código", multipc.remote_link(APP, "https://trab.exemplo.ts.net", "123456") == APP + "/#add=https%3A%2F%2Ftrab.exemplo.ts.net~123456")
    check("link remoto sem código/endereço = nada", multipc.remote_link(APP, "", "123456") is None and multipc.remote_link(APP, "https://a.b", None) is None and multipc.remote_link(APP, "https://a.b", "12") is None)
    check("endereço do PC pelo Tailscale: https", multipc.clean_remote_base("https://A.b.ts.net/") == "https://a.b.ts.net" and multipc.clean_remote_base("") == "")
    try:
        multipc.clean_remote_base("http://a.b")
        check("remote_base http recusado", False)
    except ValueError:
        check("remote_base http recusado", True)
    check("nome do PC", multipc.clean_pc_name(" Casa ") == "Casa")
    try:
        multipc.clean_pc_name("x" * 31)
        check("nome longo recusado", False)
    except ValueError:
        check("nome longo recusado", True)


async def hello(origin=None, xff=None, msg=None):
    hdr = {}
    if origin:
        hdr["Origin"] = origin
    if xff:
        hdr["X-Forwarded-For"] = xff
    ws = await websockets.connect(f"ws://127.0.0.1:{PORT}", additional_headers=hdr)
    await ws.send(json.dumps(msg or {"t": "hello", "token": TOKEN}))
    try:
        r = json.loads(await asyncio.wait_for(ws.recv(), 4))
    except Exception as e:  # noqa: BLE001
        r = {"closed": getattr(getattr(e, "rcvd", None), "code", None)}
    await ws.close()
    return r


def http_cors(origin):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/dota/img/item/1?k=0", headers={"Origin": origin} if origin else {})
    try:
        r = urllib.request.urlopen(req, timeout=4)
        return r.headers.get("Access-Control-Allow-Origin")
    except urllib.error.HTTPError as e:
        return e.headers.get("Access-Control-Allow-Origin")


async def live(srv):
    w = await hello()
    check("welcome traz pc_id, proto e pc_name", multipc.valid_pc_id(w.get("pc_id")) and w.get("proto") == multipc.PROTO and "pc_name" in w, str(w.get("proto")))
    cfg = json.loads((srv / "config.json").read_text(encoding="utf-8"))
    check("pc_id gravado no config.json e igual ao welcome", cfg.get("pc_id") == w.get("pc_id"))
    check("origem estranha recusada (4004)", (await hello(origin="https://mal.com")).get("closed") == 4004)
    check("origem da lista aceita", (await hello(origin=APP)).get("t") == "welcome")
    check("mesma origem aceita", (await hello(origin=f"http://127.0.0.1:{PORT}")).get("t") == "welcome")
    check("CORS no HTTP só para a lista", http_cors(APP) == APP and http_cors("https://mal.com") is None and http_cors(None) is None)
    p = await hello(msg={"t": "hello", "panel": PANEL})
    check("painel local (sem proxy) entra", p.get("t") != "welcome" and p.get("closed") is None)
    p = await hello(xff="100.64.0.7", msg={"t": "hello", "panel": PANEL})
    check("painel por proxy (X-Forwarded-For) recusado", p.get("closed") == 4001, str(p)[:60])
    check("token certo por proxy entra", (await hello(xff="100.64.0.7")).get("t") == "welcome")


def main():
    units()
    tmp = Path(tempfile.mkdtemp(prefix="companion-multipc-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "panel_token": PANEL, "allowed_origins": [APP],
        "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}), encoding="utf-8")
    env = dict(os.environ, COMPANION_INSTANCE_SUFFIX="-test-mpc", COMPANION_SEM_JANELA="1")
    pr = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                socket.create_connection(("127.0.0.1", PORT), 0.5).close()
                break
            except OSError:
                time.sleep(0.4)
        else:
            print("servidor de teste não subiu")
            return 2
        asyncio.run(live(srv))
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        for secret, what in ((TOKEN, "token"), (PANEL, "panel_token")):
            check(f"log sem {what}", secret not in log)
    finally:
        pr.kill()
        pr.wait()
        time.sleep(1)
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
