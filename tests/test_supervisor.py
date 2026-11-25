"""Teste do supervisor numa CÓPIA do servidor (pasta temporária, porta e mutex de teste; não toca no Companion real).
Confere: sobe o filho, religa se o filho for morto à força, impede supervisor duplo, escreve a linha "vivo" no log,
e não religa quando o filho sai com 0. Rodar: py -3 tests/test_supervisor.py"""
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

import psutil

SRC = Path(__file__).resolve().parent.parent / "windows" / "server"
PORT = 8796
TOKEN, PANEL = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FALHA"), name, extra)
    if not cond:
        fails.append(name)


def up(port, wait=25):
    end = time.time() + wait
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 0.5).close()
            return True
        except OSError:
            time.sleep(0.4)
    return False


def down(port, wait=10):
    end = time.time() + wait
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            time.sleep(0.2)
        except OSError:
            return True
    return False


def child_of(sup_pid):
    try:
        kids = psutil.Process(sup_pid).children()
    except psutil.Error:
        return None
    return kids[0] if kids else None


def main():
    tmp = Path(tempfile.mkdtemp(prefix="companion-sup-"))
    srv = tmp / "server"
    shutil.copytree(SRC, srv, ignore=shutil.ignore_patterns("__pycache__", "*.log", "config.json", "tasks.json", "work*.json", "pomodoro*.json"))
    (srv / "config.json").write_text(json.dumps({"token": TOKEN, "panel_token": PANEL,
        "web": {"host": "127.0.0.1", "port": PORT, "adb_reverse": False, "adb_path": ""}}), encoding="utf-8")
    env = dict(os.environ, COMPANION_INSTANCE_SUFFIX="-test-sup", COMPANION_ALIVE_S="2", COMPANION_SEM_JANELA="1")
    cmd = [sys.executable, "companion_server.py", "--supervise", "--console"]
    sup = subprocess.Popen(cmd, cwd=srv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sup2 = None
    try:
        check("servidor sobe pelo supervisor", up(PORT))
        kid = child_of(sup.pid)
        check("há um processo filho", kid is not None)
        sup2 = subprocess.Popen(cmd, cwd=srv, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            code = sup2.wait(15)
        except subprocess.TimeoutExpired:
            code = None
        check("segundo supervisor sai sem fazer nada (instância dupla)", code == 0, str(code))
        check("o primeiro continua com o mesmo filho", sup.poll() is None and child_of(sup.pid) is not None
              and child_of(sup.pid).pid == kid.pid)
        t0 = time.time()
        kid.kill()
        check("porta cai quando o filho é morto", down(PORT))
        check("supervisor religa sozinho", up(PORT, 30), f"{time.time() - t0:.1f}s")
        kid2 = child_of(sup.pid)
        check("filho novo é outro processo", kid2 is not None and kid2.pid != kid.pid)
        time.sleep(3)
        log = (srv / "companion-server.log").read_text(encoding="utf-8", errors="replace")
        slog = (srv / "companion-supervisor.log").read_text(encoding="utf-8", errors="replace")
        check("log tem a linha 'vivo'", "vivo: ligado há" in log)
        check("supervisor registrou a queda e o religamento", "servidor caiu" in slog and slog.count("iniciando o servidor") >= 2)
        for secret, what in ((TOKEN, "token"), (PANEL, "panel_token")):
            check(f"logs sem {what}", secret not in log and secret not in slog)
        sup.terminate()   # o supervisor some; o filho precisa ser derrubado à mão (o teste não usa a bandeja)
    finally:
        for pr in (sup, sup2):
            if pr and pr.poll() is None:
                pr.kill()
        for pr in psutil.process_iter(["cmdline"]):
            try:
                if str(srv) in " ".join(pr.info["cmdline"] or []) or pr.cwd() == str(srv):
                    pr.kill()
            except psutil.Error:
                pass
        time.sleep(1)
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
