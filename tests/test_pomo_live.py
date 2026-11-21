"""Teste ao vivo do Pomodoro numa CÓPIA do servidor (pasta temporária, porta de teste). A fase já nasce rodando com
~6 s no pomodoro.json (o limite de foco é 5 min): confere a restauração, o push e que o pomo.done dispara uma vez só.
Rodar: py -3 tests/test_pomo_live.py"""
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
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)


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


async def run():
    fails = []

    def check(name, cond, extra=""):
        print(("OK   " if cond else "FALHA"), name, extra)
        if not cond:
            fails.append(name)

    phone = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await phone.send(json.dumps({"t": "hello", "token": TOKEN}))
    panel = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await panel.send(json.dumps({"t": "hello", "panel": PANEL}))
    m, _ = await recv_until(phone, lambda m: m.get("t") == "pomo", 5)
    check("celular recebe o estado ao conectar", bool(m) and m["d"]["running"] and 0 < m["d"]["remaining_s"] <= 7 and "server_now" in m["d"], str(m and m["d"]["remaining_s"]))
    m, _ = await recv_until(panel, lambda m: m.get("t") == "pomo", 5)
    check("painel recebe o estado ao conectar", bool(m) and m["d"]["running"])
    t0 = time.time()
    done_phone, seen = await recv_until(phone, lambda m: m.get("kind") == "pomo.done", 12)
    done_panel, _ = await recv_until(panel, lambda m: m.get("kind") == "pomo.done", 3)
    check("pomo.done chega ao celular", bool(done_phone) and done_phone["finished"] == "focus" and done_phone["next"] == "short",
          f"após {time.time() - t0:.1f}s")
    check("pomo.done chega ao painel", bool(done_panel))
    extra, seen2 = await recv_until(phone, lambda m: m.get("kind") == "pomo.done", 4)
    check("pomo.done dispara uma vez só", extra is None)
    st = [x for x in seen + seen2 if x.get("t") == "pomo"]
    check("push do estado no fim da fase (parado, pausa pronta, 1 foco hoje)",
          bool(st) and st[-1]["d"]["phase"] == "short" and not st[-1]["d"]["running"] and st[-1]["d"]["today"] == 1)
    # ações: o outro aparelho recebe o push
    r = await req(phone, "pomo.start", task_id="0123abcd")
    check("pomo.start responde ok", bool(r) and r["ok"] and r["d"]["running"])
    m, _ = await recv_until(panel, lambda m: m.get("t") == "pomo" and m["d"]["running"], 3)
    check("painel recebe o push do start", bool(m) and m["d"]["task_id"] == "0123abcd")
    r = await req(phone, "pomo.pause")
    check("pause", bool(r) and r["ok"] and not r["d"]["running"])
    r = await req(phone, "pomo.config", focus_min=4)
    check("limite recusado", bool(r) and not r["ok"])
    r = await req(phone, "pomo.config", focus_min=30)
    check("config aceita", bool(r) and r["ok"] and r["d"]["config"]["focus_min"] == 30)
    await ws_send(panel, "pomo_reset")
    m, _ = await recv_until(phone, lambda m: m.get("t") == "pomo" and m["d"]["phase"] == "idle", 3)
    check("reset pelo painel chega ao celular", bool(m))
    await phone.close()
    await panel.close()
    return fails


async def ws_send(ws, op):
    await ws.send(json.dumps({"t": "pop", "id": 9, "a": op, "p": {}}))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "panel_token": PANEL,
                                                 "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}),
                                     encoding="utf-8")
    now = time.time()
    (srv / "pomodoro.json").write_text(json.dumps({
        "v": 1, "cfg": {"focus_min": 25, "short_min": 5, "long_min": 15, "long_every": 4, "auto_next": False},
        "state": {"phase": "focus", "running": True, "ends_at": now + 9, "remaining_s": None, "cycle": 0, "task_id": None},
        "history": {}}), encoding="utf-8")
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
        fails = asyncio.run(run())
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        for secret, what in ((TOKEN, "token"), (PANEL, "panel_token"), ("0123abcd", "id de tarefa")):
            ok = secret not in log
            print(("OK   " if ok else "FALHA"), f"log sem {what}")
            if not ok:
                fails.append("log " + what)
        print("\n".join(ln for ln in log.splitlines() if "omodoro" in ln or "Traceback" in ln or "ERROR" in ln))
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
