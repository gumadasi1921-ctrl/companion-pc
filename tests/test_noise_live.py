"""Teste ao vivo do Ruído numa CÓPIA do servidor (pasta temporária, porta de teste, volume 0 = sem som audível):
foco → fim do foco (liga e desliga sozinho), ações noise.*, filho de verdade (processo) e filho some se o servidor morre.
Se houver mídia tocando no PC, o foco NÃO liga o ruído (regra aprovada): o teste confere o aviso e o "Ligar mesmo assim".
Rodar: py -3 tests/test_noise_live.py"""
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

import psutil
import websockets

SRC = Path(__file__).resolve().parent.parent / "windows" / "server"
PORT = 8797
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
SERVER_PID = [0]


def players():
    try:
        return [c for c in psutil.Process(SERVER_PID[0]).children(recursive=True) if "noise_player" in " ".join(c.cmdline())]
    except psutil.Error:
        return []


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
    m, _ = await recv_until(ws, lambda m: m.get("t") == "res" and m.get("id") == 7, 15)
    return m


async def run():
    fails = []

    def check(name, cond, extra=""):
        print(("OK   " if cond else "FALHA"), name, extra)
        if not cond:
            fails.append(name)

    ph = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await ph.send(json.dumps({"t": "hello", "token": TOKEN}))
    m, _ = await recv_until(ph, lambda m: m.get("t") == "noise", 8)
    check("celular recebe o estado do ruído ao conectar (desligado)", bool(m) and not m["d"]["on"] and m["d"]["cfg"]["focus_on"] is True, str(m and m["d"]["cfg"]))
    r = await req(ph, "noise.set", volume=101)
    check("volume inválido é recusado", bool(r) and not r["ok"])
    # foco rodando: liga sozinho (ou avisa, se houver mídia tocando)
    await ph.send(json.dumps({"t": "req", "id": 8, "a": "pomo.start", "p": {}}))   # sem req(): ela descartaria o push do ruído
    m, _ = await recv_until(ph, lambda m: m.get("t") == "noise" and (m["d"]["on"] or m["d"]["blocked"]), 15)
    check("foco começou: ruído ligou ou avisou da mídia", bool(m), str(m and {k: m["d"][k] for k in ("on", "blocked", "by")}))
    if m and m["d"]["blocked"]:
        print("     (há mídia tocando no PC: caminho do aviso)")
        check("bloqueado: nenhum filho tocando", not players())
        r = await req(ph, "noise.force")
        check("Ligar mesmo assim liga (by=auto)", bool(r) and r["ok"] and r["d"]["on"] and r["d"]["by"] == "auto")
    elif m:
        check("ligou sozinho (by=auto, marrom)", m["d"]["by"] == "auto" and m["d"]["kind"] == "brown" and m["d"]["name"] == "Ruído marrom")
    check("há um filho tocando", len(players()) == 1)
    # o fim do foco (6 s) desliga
    m, _ = await recv_until(ph, lambda m: m.get("t") == "noise" and not m["d"]["on"], 15)
    check("fim do foco desligou o ruído", bool(m))
    time.sleep(0.5)
    check("nenhum filho depois do foco", not players())
    # manual: liga, troca de tipo, para
    r = await req(ph, "noise.start", kind="pink")
    check("noise.start manual", bool(r) and r["ok"] and r["d"]["on"] and r["d"]["by"] == "manual" and r["d"]["kind"] == "pink")
    r = await req(ph, "noise.set", kind="white")
    check("trocar de tipo tocando troca o som", bool(r) and r["ok"] and r["d"]["kind"] == "white" and r["d"]["on"])
    check("continua 1 filho", len(players()) == 1)
    r = await req(ph, "noise.stop")
    check("noise.stop", bool(r) and r["ok"] and not r["d"]["on"])
    r = await req(ph, "noise.start", kind="brown")
    check("liga de novo", bool(r) and r["ok"] and r["d"]["on"])
    await ph.close()
    return fails


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json", "noise-*.wav"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "panel_token": PANEL, "noise": {"focus_on": True, "kind": "brown", "volume": 0},
                                                 "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}), encoding="utf-8")
    (srv / "pomodoro.json").write_text(json.dumps({
        "v": 1, "cfg": {"focus_min": 25, "short_min": 5, "long_min": 15, "long_every": 4, "auto_next": False},
        "state": {"phase": "focus", "running": False, "ends_at": None, "remaining_s": 8, "cycle": 0, "task_id": None},
        "history": {}}), encoding="utf-8")
    p = subprocess.Popen([sys.executable, "companion_server.py", "--console"], cwd=srv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    SERVER_PID[0] = p.pid
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
        kids = players()
        p.kill()
        p.wait()
        end = time.time() + 8
        while time.time() < end and any(k.is_running() for k in kids):
            time.sleep(0.3)
        gone = not any(k.is_running() for k in kids)
        print(("OK   " if gone else "FALHA"), "o filho some quando o servidor morre")
        if not gone:
            fails.append("órfão")
            for k in kids:
                k.kill()
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        for secret, what in ((TOKEN, "token"), (PANEL, "panel_token")):
            ok = secret not in log
            print(("OK   " if ok else "FALHA"), f"log sem {what}")
            if not ok:
                fails.append("log " + what)
        print("\n".join(ln for ln in log.splitlines() if "uído" in ln or "ruído" in ln or "Traceback" in ln))
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
