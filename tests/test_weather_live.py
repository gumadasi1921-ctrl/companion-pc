"""Teste ao vivo da previsão numa CÓPIA do servidor (pasta temporária, porta de teste), com a Open-Meteo de verdade:
busca "São Paulo", escolhe, confere a previsão (24 h / 7 dias), o push, o limite do refresh, o config.json e o log.
Se a rede bloquear a Open-Meteo, o teste diz "bloqueado pela rede" (sem tentar contornar) e sai com código 3.
Rodar: py -3 tests/test_weather_live.py"""
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
PORT = 8796
TOKEN = secrets.token_urlsafe(24)


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


async def req(ws, a, rid=7, timeout=15, **p):
    await ws.send(json.dumps({"t": "req", "id": rid, "a": a, "p": p}))
    m, _ = await recv_until(ws, lambda m: m.get("t") == "res" and m.get("id") == rid, timeout)
    return m


async def run(srv):
    fails = []

    def check(name, cond, extra=""):
        print(("OK   " if cond else "FALHA"), name, extra)
        if not cond:
            fails.append(name)

    a = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await a.send(json.dumps({"t": "hello", "token": TOKEN}))
    b = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await b.send(json.dumps({"t": "hello", "token": TOKEN}))
    m, _ = await recv_until(a, lambda m: m.get("t") == "weather", 5)
    check("ao conectar o aparelho recebe o estado (sem cidade)", bool(m) and m["d"] == {"place": None}, str(m and m["d"]))
    r = await req(a, "weather.search", q="São Paulo")
    if not r or not r.get("ok"):
        print("bloqueado pela rede:", r and r.get("e"))
        return None
    res = r["d"]["results"]
    check("busca devolve até 5 resultados", 1 <= len(res) <= 5 and res[0]["name"] == "São Paulo" and res[0].get("country") == "Brasil", str(res[:1]))
    r = await req(a, "weather.set_place", place={"name": "x", "lat": 999, "lon": 0})
    check("cidade inválida recusada", bool(r) and not r["ok"])
    r = await req(a, "weather.set_place", place=res[0])
    check("escolher a cidade responde com a previsão", bool(r) and r["ok"] and len(r["d"]["d"].get("hourly", [])) == 24 and len(r["d"]["d"].get("daily", [])) == 7, str(r and r.get("e")))
    st = r["d"]["d"]
    check("estado: agora, atualizado em, não desatualizado", "temp" in st["current"] and st["stale"] is False and st["updated_at"] > time.time() - 60)
    m, _ = await recv_until(b, lambda m: m.get("t") == "weather" and m["d"].get("place"), 5)
    check("o outro aparelho recebe o push da cidade", bool(m) and m["d"]["place"]["name"] == "São Paulo" and len(m["d"]["daily"]) == 7)
    cfg = json.loads((srv / "config.json").read_text(encoding="utf-8"))
    check("config.json guarda a cidade (e só isso mudou: token e porta iguais)", cfg["weather"]["place"]["name"] == "São Paulo" and cfg["weather"]["units"] == "metric"
          and cfg["token"] == TOKEN and cfg["web"]["port"] == PORT)
    check("weather.json gravado", (srv / "weather.json").exists())
    r = await req(a, "weather.refresh", rid=8)
    check("refresh logo depois é recusado pelo limite (espera ~60 s)", bool(r) and r["ok"] and 0 < r["d"]["wait_s"] <= 60, str(r and r["d"].get("wait_s")))
    r = await req(a, "weather.get", rid=9)
    check("weather.get devolve o estado", bool(r) and r["ok"] and r["d"]["d"]["place"]["name"] == "São Paulo")
    c = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await c.send(json.dumps({"t": "hello", "token": TOKEN}))
    m, _ = await recv_until(c, lambda m: m.get("t") == "weather", 5)
    check("aparelho novo já recebe a previsão ao conectar", bool(m) and len(m["d"].get("hourly", [])) == 24)
    for ws in (a, b, c):
        await ws.close()
    return fails


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json", "weather*.json", "dota-img"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}), encoding="utf-8")
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
        fails = asyncio.run(run(srv))
        if fails is None:
            return 3
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        for secret, what in ((TOKEN, "token"), ("São Paulo", "nome da cidade"), ("23.54", "latitude"), ("46.63", "longitude")):
            ok = secret not in log
            print(("OK   " if ok else "FALHA"), f"log sem {what}")
            if not ok:
                fails.append("log " + what)
        print("\n".join(ln for ln in log.splitlines() if "previs" in ln.lower() or "Traceback" in ln or "ERROR" in ln))
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
