"""Teste ao vivo (cópia do servidor em outra porta): exportação concluída, link /dl/, config do painel, deck migrado e
estado do modo foco no Pomodoro. Não abre o Explorer nem a tela do Windows. Rodar: py -3 tests/test_notices_live.py"""
import asyncio
import json
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
PORT = 8797
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
SECRET_NAME = "segredo-cliente-xyz"


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


async def req(ws, a, **p):
    await ws.send(json.dumps({"t": "req", "id": 7, "a": a, "p": p}))
    return await recv_until(ws, lambda m: m.get("t") == "res" and m.get("id") == 7, 8)


async def pop(ws, op, **p):
    await ws.send(json.dumps({"t": "pop", "id": 9, "a": op, "p": p}))
    return await recv_until(ws, lambda m: m.get("t") == "res" and m.get("id") == 9, 8)


async def run(exp):
    fails = []

    def check(name, cond, extra=""):
        print(("OK   " if cond else "FALHA"), name, extra)
        if not cond:
            fails.append(name)

    phone = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await phone.send(json.dumps({"t": "hello", "token": TOKEN}))
    panel = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await panel.send(json.dumps({"t": "hello", "panel": PANEL}))
    await recv_until(panel, lambda m: m.get("t") == "panel_ok", 5)
    (exp / "antigo.mp4").write_bytes(b"x" * 100)   # existia antes de vigiar
    r = await pop(panel, "config_save", changes={"export_watch": {"folders": [str(exp)], "on": True}, "misplaced_min": 5})
    check("painel grava pastas de exportação e minutos", bool(r) and r["ok"], str(r))
    r = await pop(panel, "config_save", changes={"misplaced_min": 7})
    check("minutos inválidos recusados", bool(r) and not r["ok"])
    r = await pop(panel, "config_save", changes={"export_watch": {"folders": ["relativa"], "on": True}})
    check("pasta relativa recusada", bool(r) and not r["ok"])
    r = await pop(panel, "config_save", changes={"export_watch": {"folders": [str(exp)] * 2, "on": True}})
    check("pasta repetida recusada", bool(r) and not r["ok"])
    r = await pop(panel, "config_get")
    check("config_get traz export_watch e misplaced_min", bool(r) and r["ok"] and r["d"]["export_watch"]["folders"] == [str(exp)] and r["d"]["misplaced_min"] == 5)
    r = await req(phone, "export.link", id="nao-existe")
    check("link de id desconhecido falha", bool(r) and not r["ok"])
    r = await req(phone, "deck.get")
    pages = r["d"]["deck"]["pages"] if r and r.get("ok") else []
    check("deck antigo ganhou a página Illustrator", [p["name"] for p in pages] == ["Meu", "Illustrator"], str([p["name"] for p in pages]))
    r = await req(phone, "pomo.start")
    check("estado do Pomodoro traz modo foco ligado e 0 seguradas", bool(r) and r["ok"] and r["d"]["focus"] == {"on": True, "held": 0}, str(r and r["d"].get("focus")))
    await asyncio.sleep(4)   # o vigia primeiro "aprende" a pasta
    big = exp / f"{SECRET_NAME}.mp4"
    t0 = time.time()
    with open(big, "wb") as f:
        for _ in range(4):
            f.write(b"0" * 500_000)
            f.flush()
            await asyncio.sleep(1)
    ev = await recv_until(phone, lambda m: m.get("kind") == "export", 25)
    check("aviso de exportação chega ao celular", bool(ev) and ev["name"] == big.name and ev["size"] == 2_000_000, f"{time.time() - t0:.1f}s desde o início")
    check("só depois de ficar estável (>= 5 s depois do último byte)", bool(ev) and time.time() - t0 >= 4 + 5)
    extra = await recv_until(phone, lambda m: m.get("kind") == "export", 8)
    check("um aviso só por arquivo (e o antigo não avisa)", extra is None)
    if ev:
        r = await req(phone, "export.link", id=ev["id"])
        ok = bool(r) and r["ok"] and r["d"]["url"].startswith("/dl/")
        check("export.link devolve link /dl/", ok)
        if ok:
            data = urllib.request.urlopen(f"http://127.0.0.1:{PORT}{r['d']['url']}", timeout=5).read()
            check("download pelo link bate com o arquivo", len(data) == 2_000_000)
    await phone.close()
    await panel.close()
    return fails


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv, exp = tmp / "server", tmp / "exports"
    exp.mkdir()
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json", "deck.json*"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "panel_token": PANEL,
                                                 "downloads_dir": str(tmp / "dl"),
                                                 "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}),
                                     encoding="utf-8")
    (tmp / "dl").mkdir()
    (srv / "deck.json").write_text(json.dumps({"pages": [{"name": "Meu", "buttons": [{"label": "A", "type": "keys", "value": "ctrl+a"}]}]}), encoding="utf-8")
    p = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        fails = asyncio.run(run(exp))
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        for secret, what in ((TOKEN, "token"), (PANEL, "panel_token"), (SECRET_NAME, "nome do arquivo exportado")):
            ok = secret not in log
            print(("OK   " if ok else "FALHA"), f"log sem {what}")
            if not ok:
                fails.append("log " + what)
        print("\n".join(ln for ln in log.splitlines() if "xporta" in ln or "Traceback" in ln or "ERROR" in ln or "deck" in ln))
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
