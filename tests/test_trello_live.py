"""Teste ao vivo (cópia do servidor, outra porta, SEM rede): Trello (cache pré-carregado, "ligar" desligado), credenciais que
nunca voltam, trello.open recusando shortLink fora do cache, backup pelo painel, histórico mensal e nota do dia.
Rodar: py -3 tests/test_trello_live.py"""
import asyncio
import json
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import websockets

SRC = Path(__file__).resolve().parent.parent / "windows" / "server"
PORT = 8794
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
KEY, TTOKEN = "K" * 32, "ATTAlivetoken" + "T" * 40
CARD_NAME = "Cartaz-secreto-do-cliente"
MONTH = date.today().strftime("%Y-%m")


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


async def call(ws, kind, a, **p):
    await ws.send(json.dumps({"t": kind, "id": 5, "a": a, "p": p}))
    return await recv_until(ws, lambda m: m.get("t") == "res" and m.get("id") == 5, 10)


async def run(bk):
    fails, seen = [], []

    def check(name, cond, extra=""):
        print(("OK   " if cond else "FALHA"), name, extra)
        if not cond:
            fails.append(name)

    phone = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await phone.send(json.dumps({"t": "hello", "token": TOKEN}))
    panel = await websockets.connect(f"ws://127.0.0.1:{PORT}")
    await panel.send(json.dumps({"t": "hello", "panel": PANEL}))
    await recv_until(panel, lambda m: m.get("t") == "panel_ok", 5)
    m = await recv_until(phone, lambda m: m.get("t") == "trello", 5)
    seen.append(json.dumps(m))
    check("celular recebe o estado do Trello ao conectar", bool(m) and m["d"]["state"] == "off" and m["d"]["counts"]["none"] == 1, str(m and m["d"]["counts"]))
    r = await call(phone, "req", "trello.get")
    seen.append(json.dumps(r))
    check("trello.get devolve os cards em cache", bool(r) and r["ok"] and r["d"]["counts"]["none"] == 1 and r["d"]["groups"]["none"][0]["name"] == CARD_NAME)
    r = await call(panel, "pop", "work_link_set", client="Acme", job="Site", card="ZZZZZZZZ")
    check("ligar a card fora do cache é recusado", bool(r) and not r["ok"])
    r = await call(panel, "pop", "work_link_set", client="Acme", job="Site", card="AAAAAAA8")
    check("painel liga o job ao card", bool(r) and r["ok"] and r["d"]["link"]["card_name"] == CARD_NAME, str(r and r.get("e")))
    link_id = r["d"]["link"]["id"] if r and r.get("ok") else ""
    r = await call(phone, "req", "trello.get")
    seen.append(json.dumps(r))
    check("card mostra o tempo do job ligado", bool(r) and r["ok"] and r["d"]["groups"]["none"][0].get("spent", {}).get("total_s") == 3600, str(r and r["d"]["groups"]["none"][0].get("spent")))
    r = await call(panel, "pop", "work_links_get")
    check("work_links_get traz hoje/semana/total", bool(r) and r["ok"] and r["d"][0]["total_s"] == 3600)
    r = await call(panel, "pop", "work_link_delete", id=link_id)
    check("desligar", bool(r) and r["ok"] and len(r["d"]["removed"]) == 1)
    r = await call(panel, "pop", "work_link_restore", entries=r["d"]["removed"])
    check("desfazer devolve a ligação", bool(r) and r["ok"] and r["d"]["restored"] == 1)
    r = await call(panel, "pop", "config_save", changes={"trello": {"on": True}})
    check("ligar sem key/token é recusado", bool(r) and not r["ok"])
    r = await call(panel, "pop", "config_save", changes={"trello": {"on": False, "key": "curta", "token": TTOKEN}})
    check("key inválida é recusada", bool(r) and not r["ok"])
    seen.append(json.dumps(r))
    r = await call(panel, "pop", "config_save", changes={"trello": {"on": False, "key": KEY, "token": TTOKEN}})
    check("painel salva key e token", bool(r) and r["ok"], str(r))
    seen.append(json.dumps(r))
    r = await call(panel, "pop", "config_get")
    seen.append(json.dumps(r))
    check("config_get traz só o estado (sem key/token)", bool(r) and r["ok"] and r["d"]["trello"]["has_key"] and r["d"]["trello"]["has_secret"]
          and KEY not in json.dumps(r) and TTOKEN not in json.dumps(r))
    r = await call(phone, "req", "trello.get")
    check("credencial nova esquece os cards da conta anterior", bool(r) and r["ok"] and r["d"]["counts"]["none"] == 0)
    for bad in ("ZZZZZZZZ", "AAAAAAA", "../../x", "http://evil.example"):
        r = await call(phone, "req", "trello.open", short=bad)
        check("trello.open recusa " + bad, bool(r) and not r["ok"])
    r = await call(phone, "req", "trello.open")
    check("trello.open sem shortLink é recusado", bool(r) and not r["ok"])
    # celular não consegue mexer em config nem testar credenciais
    r = await call(phone, "req", "trello_test")
    check("celular não chama operações do painel", bool(r) and not r["ok"])
    # histórico e nota do dia
    r = await call(panel, "pop", "work_note_set", day=f"{MONTH}-02", text="nota de teste")
    check("nota do dia salva pelo painel", bool(r) and r["ok"])
    r = await call(panel, "pop", "work_month", ym=MONTH)
    check("work_month traz o dia e a nota", bool(r) and r["ok"] and r["d"]["total_s"] == 3600 and r["d"]["days"][1].get("note") == "nota de teste")
    r = await call(phone, "req", "work.month", ym=MONTH)
    check("celular recebe o mês sem notas", bool(r) and r["ok"] and r["d"]["total_s"] == 3600 and "note" not in json.dumps(r))
    r = await call(panel, "pop", "work_month_csv", ym=MONTH)
    check("CSV do mês por link /dl/", bool(r) and r["ok"] and r["d"]["name"] == f"trabalho-{MONTH}.csv")
    # backup
    r = await call(panel, "pop", "config_save", changes={"backup_dir": str(bk)})
    check("pasta de backup salva", bool(r) and r["ok"])
    r = await call(panel, "pop", "backup_run")
    day = bk / "Companion-backup" / time.strftime("%Y-%m-%d")
    check("backup feito agora", bool(r) and r["ok"] and r["d"]["ok"] and (day / "work-history.json").exists() and (day / "work-notes.json").exists(), str(r and r["d"]))
    check("config.json fora do backup", not (day / "config.json").exists())
    r = await call(panel, "pop", "config_get")
    check("config_get mostra o último backup", bool(r) and r["d"]["backup"]["ok"] is True)
    await phone.close()
    await panel.close()
    return fails, seen


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-test-"))
    srv, bk = tmp / "server", tmp / "bk"
    bk.mkdir()
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json", "deck.json*", "trello.json", "backup-status.json"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "panel_token": PANEL,
                                                 "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}), encoding="utf-8")
    (srv / "trello.json").write_text(json.dumps({"v": 1, "at": time.time(), "lists_at": time.time(), "boards": {}, "lists": {}, "cards": [
        {"short": "AAAAAAA8", "name": CARD_NAME, "board": "Q", "list": "L", "due": None, "done": False, "labels": [], "act": None}]}), encoding="utf-8")
    (srv / "work-history.json").write_text(json.dumps({"version": 2, "days": {f"{MONTH}-02": {"Acme\tSite\tPremiere\ta.prproj": 3600.0}}}), encoding="utf-8")
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
        fails, seen = asyncio.run(run(bk))
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        for secret, what in ((TOKEN, "token do celular"), (PANEL, "panel_token"), (KEY, "API key do Trello"), (TTOKEN, "token do Trello"), (CARD_NAME, "nome de card"), ("nota de teste", "nota do dia")):
            ok = secret not in log
            print(("OK   " if ok else "FALHA"), f"log sem {what}")
            if not ok:
                fails.append("log " + what)
        ok = KEY not in "".join(seen) and TTOKEN not in "".join(seen)
        print(("OK   " if ok else "FALHA"), "nenhuma resposta ao celular/painel leva key ou token")
        if not ok:
            fails.append("credencial em resposta")
        print("\n".join(ln for ln in log.splitlines() if "Traceback" in ln or "ERROR" in ln or "rello" in ln or "backup" in ln))
        return 1 if fails else 0
    finally:
        p.kill()
        p.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
