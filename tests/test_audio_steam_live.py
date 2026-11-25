"""Teste ao vivo (cópia do servidor em outra porta): perfis de áudio (salvar, listar, aplicar o MESMO estado, apagar),
steam.get lendo a Steam real (só leitura) e o log sem o nome do perfil. Rodar: py -3 tests/test_audio_steam_live.py"""
import asyncio
import json
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
PORT = 8798
TOKEN = secrets.token_urlsafe(24)
SECRET_NAME = "perfil-secreto-xyz"


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
    return await recv_until(ws, lambda m: m.get("t") == "res" and m.get("id") == 7, 10)


async def run():
    fails = []

    def check(name, cond, extra=""):
        print(("OK   " if cond else "FALHA"), name, extra)
        if not cond:
            fails.append(name)

    ws = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await ws.send(json.dumps({"t": "hello", "token": TOKEN}))
    w = await recv_until(ws, lambda m: m.get("t") == "welcome", 5)
    check("conectou", bool(w))
    r = await req(ws, "steam.get")
    check("steam.get responde", bool(r) and r["ok"] and "available" in r["d"]["d"], str(r)[:200])
    if w and w.get("features", {}).get("mixer"):
        r = await req(ws, "audio.profile_save", name=SECRET_NAME)
        check("salva perfil", bool(r) and r["ok"] and r["d"][0]["name"] == SECRET_NAME, str(r)[:200])
        pid = r["d"][0]["id"] if r and r["ok"] else None
        r = await req(ws, "audio.profile_save", name=SECRET_NAME)
        check("nome repetido recusado", bool(r) and not r["ok"], str(r)[:200])
        r = await req(ws, "audio.profile_apply", id=pid)
        check("aplica o mesmo estado sem pular nada de essencial", bool(r) and r["ok"] and r["d"]["ok"], str(r)[:200])
        r = await req(ws, "audio.profile_delete", id=pid)
        check("apaga", bool(r) and r["ok"] and r["d"] == [], str(r)[:200])
        r = await req(ws, "audio.profile_apply", id="deadbeef")
        check("perfil inexistente dá erro", bool(r) and not r["ok"])
    else:
        print("(sem pycaw: parte de áudio pulada)")
    await ws.close()
    return fails


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json",
                                                            "pomodoro*.json", "deck.json*", "audio_profiles.json*", "weather.json*"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}),
                                     encoding="utf-8")
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
        fails = asyncio.run(run())
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        for secret, what in ((TOKEN, "token"), (SECRET_NAME, "nome do perfil")):
            ok = secret not in log
            print(("OK   " if ok else "FALHA"), f"log sem {what}")
            if not ok:
                fails.append("log " + what)
        print("\n".join(ln for ln in log.splitlines() if "Traceback" in ln or "ERROR" in ln))
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
