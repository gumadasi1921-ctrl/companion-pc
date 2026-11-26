"""Backup do estado do app, ao vivo numa CÓPIA do servidor (porta de teste): devbk.save/info/get pelo WebSocket,
token errado recusado, aparelho errado, estado vazio recusado, ops do painel devbk_list/devbk_delete e log sem dados.
Rodar: py -3 tests/test_devbackup_live.py"""
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
from pathlib import Path

import websockets

SRC = Path(__file__).resolve().parent.parent / "windows" / "server"
PORT = 8795
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
FAILS = []
A, B = "a1" * 16, "b2" * 16


def check(name, cond, extra=""):
    print(("ok   " if cond else "FALHA ") + name + (f" ({extra})" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


async def conn(msg):
    ws = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await ws.send(json.dumps(msg))
    return ws


async def req(ws, a, p, kind="req"):
    rid = secrets.randbelow(10**6)
    await ws.send(json.dumps({"t": kind, "a" if kind == "req" else "a": a, "p": p, "id": rid}))
    for _ in range(80):
        m = json.loads(await asyncio.wait_for(ws.recv(), 6))
        if m.get("t") == "res" and m.get("id") == rid:
            return m
    return {}


async def live(srv):
    ws = await conn({"t": "hello", "token": TOKEN})
    w = json.loads(await ws.recv())
    check("welcome anuncia devbk", w.get("features", {}).get("devbk") is True)
    r = await req(ws, "devbk.save", {"id": A, "name": "iPhone", "data": {"theme": "modern", "home": {"v": 3}, "token": "NAO"}})
    check("save ok", r.get("ok") and r["d"]["saved"], str(r))
    r = await req(ws, "devbk.save", {"id": A, "name": "iPhone", "data": {}})
    check("estado vazio recusado", not r.get("ok"))
    r = await req(ws, "devbk.save", {"id": "curto", "name": "x", "data": {"theme": "x"}})
    check("id inválido recusado", not r.get("ok"))
    r = await req(ws, "devbk.get", {"id": A})
    check("get devolve só chaves da lista", r.get("ok") and r["d"]["data"] == {"theme": "modern", "home": {"v": 3}}, str(r)[:120])
    r = await req(ws, "devbk.get", {"id": B})
    check("aparelho errado não lê o de outro", not r.get("ok"))
    await req(ws, "devbk.save", {"id": B, "name": "iPad", "data": {"theme": "fallout"}})
    r = await req(ws, "devbk.info", {"id": B})
    d = r.get("d") or {}
    check("info: próprias versões + outros por nome/ref", len(d.get("own", [])) == 1 and [o["name"] for o in d.get("others", [])] == ["iPhone"], str(r)[:160])
    check("info não revela o id de outro aparelho", A not in json.dumps(r))
    r2 = await req(ws, "devbk.get", {"ref": d["others"][0]["ref"]})
    check("copiar de outro aparelho por ref", r2.get("ok") and r2["d"]["data"]["theme"] == "modern")
    await ws.close()
    bad = await conn({"t": "hello", "token": "errado"})
    try:
        await asyncio.wait_for(bad.recv(), 3)
        check("token errado recusado", False)
    except websockets.ConnectionClosed:
        check("token errado recusado", True)
    p = await conn({"t": "hello", "panel": PANEL})
    await asyncio.sleep(0.5)
    r = await req(p, "devbk_list", {}, kind="pop")
    names = sorted(x["name"] for x in (r.get("d") or []))
    check("painel lista os dois", names == ["iPad", "iPhone"], str(r)[:160])
    ref = next(x["ref"] for x in r["d"] if x["name"] == "iPhone")
    r = await req(p, "devbk_delete", {"ref": ref}, kind="pop")
    check("painel apaga", r.get("ok") and r["d"] == 1, str(r)[:120])
    r = await req(p, "devbk_list", {}, kind="pop")
    check("sobrou um", len(r.get("d") or []) == 1)
    await p.close()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-devbk-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json", "device-backups"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "panel_token": PANEL,
        "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}), encoding="utf-8")
    env = dict(os.environ, COMPANION_INSTANCE_SUFFIX="-test-devbk", COMPANION_SEM_JANELA="1")
    pr = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(90):
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
        check("log sem token, nome nem valores", TOKEN not in log and "iPhone" not in log and "modern" not in log and "fallout" not in log)
    finally:
        pr.kill()
        pr.wait()
        time.sleep(1)
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
