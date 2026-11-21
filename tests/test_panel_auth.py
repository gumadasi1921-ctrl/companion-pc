"""Testes de segurança do modo painel. Sobe uma CÓPIA do servidor numa pasta temporária (config, token e porta de teste;
nunca toca no servidor instalado nem nos dados reais) e encerra só o processo que ele mesmo iniciou.
Rodar: py -3 tests/test_panel_auth.py"""
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
PORT = 8799
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 1))
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except OSError:
        return None


async def open_ws(host, hello, timeout=5):
    ws = await websockets.connect(f"ws://{host}:{PORT}", open_timeout=timeout)
    await ws.send(json.dumps(hello))
    return ws


async def closed_with(ws, timeout=4):
    """Código de fechamento se o servidor recusou (sem mandar welcome/panel_ok); None se aceitou."""
    try:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        return None if msg.get("t") in ("welcome", "panel_ok") else msg
    except websockets.ConnectionClosed as e:
        return e.rcvd.code if e.rcvd else 1006


async def pop(ws, op, **p):
    await ws.send(json.dumps({"t": "pop", "id": 1, "a": op, "p": p}))
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if m.get("t") == "res" and m.get("id") == 1:
            return m


async def run():
    fails = []

    def check(name, cond, extra=""):
        print(("OK   " if cond else "FALHA"), name, extra)
        if not cond:
            fails.append(name)

    async def tasks_now():
        ws = await open_ws("127.0.0.1", {"t": "hello", "panel": PANEL})
        assert await closed_with(ws) is None
        r = await pop(ws, "tasks_get")
        await ws.close()
        return [i["text"] for i in r["d"]["items"]]

    # painel certo, loopback
    pw = await open_ws("127.0.0.1", {"t": "hello", "panel": PANEL})
    check("painel (loopback + panel_token) aceito", await closed_with(pw) is None)
    r = await pop(pw, "tasks_add", text="tarefa de teste")
    check("painel: tasks_add funciona", r["ok"] and r["d"]["total"] == 1)
    tid = r["d"]["items"][0]["id"]
    r = await pop(pw, "tasks_remove", id=tid)
    r = await pop(pw, "tasks_restore", id=tid, text="tarefa de teste", done=False, pos=0)
    check("painel: tasks_restore funciona", r["ok"] and r["d"]["items"][0]["id"] == tid)
    r = await pop(pw, "tasks_add", text="")
    check("painel: texto vazio dá erro de validação", not r["ok"])
    r = await pop(pw, "config_get")
    check("painel: config_get sem token", r["ok"] and "token" not in json.dumps(r["d"]) and TOKEN not in json.dumps(r["d"]) and PANEL not in json.dumps(r["d"]))
    r = await pop(pw, "nao_existe")
    check("painel: operação fora da lista recusada", not r["ok"])
    base = await tasks_now()

    # (a) sem panel_token: conexão normal (só o token) tentando gerenciar
    cw = await open_ws("127.0.0.1", {"t": "hello", "token": TOKEN})
    check("(a) conexão normal aceita como celular", await closed_with(cw) is None)
    r = await pop(cw, "tasks_add", text="invasor")
    check("(a) tasks_add por pop recusado", not r["ok"])
    await cw.send(json.dumps({"t": "req", "id": 2, "a": "tasks_add", "p": {"text": "invasor"}}))
    r2 = None
    while r2 is None:
        m = json.loads(await asyncio.wait_for(cw.recv(), 5))
        if m.get("t") == "res" and m.get("id") == 2:
            r2 = m
    check("(a) tasks_add como ação do celular recusado", not r2["ok"])
    for op, p in (("config_save", {"changes": {"allow_power": False}}), ("pair_new", {}), ("work_add_root", {"folder": "C:\\"}),
                  ("tasks_restore", {"id": "0123abcd", "text": "x", "done": False, "pos": 0}), ("pick_folder", {})):
        r = await pop(cw, op, **p)
        check(f"(a) {op} recusado", not r["ok"])
    await cw.close()
    w = await open_ws("127.0.0.1", {"t": "hello", "panel": "token-errado"})
    check("(a) hello com panel errado recusado (4001)", await closed_with(w) == 4001)
    w = await open_ws("127.0.0.1", {"t": "hello"})
    check("(a) hello sem token nenhum recusado (4001)", await closed_with(w) == 4001)

    # (b) cabo (adb reverse) = loopback com o token normal; não pode servir de panel_token
    w = await open_ws("127.0.0.1", {"t": "hello", "panel": TOKEN})
    check("(b) loopback com o token normal no lugar do panel_token recusado", await closed_with(w) == 4001)
    w = await open_ws("127.0.0.1", {"t": "hello", "token": TOKEN, "panel": ""})
    check("(b) loopback com token + panel vazio recusado", await closed_with(w) == 4001)

    # (c) panel_token certo, mas vindo de um IP que não é loopback
    ip = lan_ip()
    if ip:
        w = await open_ws(ip, {"t": "hello", "panel": PANEL})
        check(f"(c) panel_token certo vindo do IP da rede recusado", await closed_with(w) == 4001)
    else:
        print("PULOU (c): sem IP de rede local")
        fails.append("(c) sem IP de rede")

    check("nada foi criado pelas tentativas recusadas", await tasks_now() == base, str(base))

    # pareamento pelo painel web
    r = await pop(pw, "pair_new")
    code = r["d"]["code"]
    check("pareamento: código de 6 dígitos gerado pelo painel", r["ok"] and len(code) == 6 and code.isdigit() and r["d"]["left"] > 100)
    ph = await open_ws("127.0.0.1", {"t": "pair", "code": code})
    msg = json.loads(await asyncio.wait_for(ph.recv(), 4))
    check("pareamento: código vale uma vez e entrega o token", msg.get("t") == "paired" and msg.get("token") == TOKEN)
    await ph.close()
    r = await pop(pw, "pair_new")
    code2 = r["d"]["code"]
    await pw.close()      # fechar o painel invalida o código
    await asyncio.sleep(0.5)
    ph = await open_ws("127.0.0.1", {"t": "pair", "code": code2})
    check("pareamento: código invalidado ao fechar o painel (4002)", await closed_with(ph) is not None)
    return fails


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "panel_token": PANEL,
                                                 "web": {"host": "0.0.0.0", "port": PORT, "adb_reverse": False, "adb_path": ""}}),
                                     encoding="utf-8")
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
        for secret, what in ((TOKEN, "token"), (PANEL, "panel_token"), ("tarefa de teste", "texto de tarefa"), ("invasor", "texto invasor")):
            ok = secret not in log
            print(("OK   " if ok else "FALHA"), f"log sem {what}")
            if not ok:
                fails.append("log " + what)
        print("--- log do servidor de teste (linhas de painel/recusa) ---")
        print("\n".join(ln for ln in log.splitlines() if "painel" in ln or "recus" in ln or "pareamento" in ln))
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
