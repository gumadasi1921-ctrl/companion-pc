"""Supervisor do Companion PC: sobe o servidor como processo filho e o religa se ele cair.
Uso: companion_server.py --supervise [--console]. Instância dupla: mutex próprio (Local\\CompanionPCSupervisor).
Código de saída do filho: 0 = encerrado de propósito (não religa); 75 = reinício pedido pela bandeja (religa já);
qualquer outro / morto à força = queda (religa com espera 1, 2, 5, 15, 30 s; 5 quedas em 5 min = desiste e registra).
O log só traz códigos de saída e contagens, nunca dados pessoais."""
import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("companion.supervisor")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
ERROR_ALREADY_EXISTS = 183
BACKOFF = (1, 2, 5, 15, 30)
RESTART_CODE = 75
MAX_CRASHES, WINDOW_S = 5, 300
_mutex = None


def acquire():
    """True se este é o único supervisor (guarda o handle até o processo acabar)."""
    global _mutex
    name = "Local\\CompanionPCSupervisor" + os.environ.get("COMPANION_INSTANCE_SUFFIX", "")
    h = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return False
    _mutex = h
    return True


def next_delay(crashes_in_window):
    """Espera antes de religar, pela quantidade de quedas recentes (1ª queda = 1 s)."""
    return BACKOFF[min(max(crashes_in_window, 1), len(BACKOFF)) - 1]


def run(script, extra_args=()):
    """Loop do supervisor. Devolve quando o filho sai com 0 ou quando desiste (queda em laço)."""
    args = [sys.executable, str(script), *extra_args]
    env = dict(os.environ, COMPANION_SUPERVISED="1")
    crashes = []
    while True:
        started = time.monotonic()
        log.info("supervisor: iniciando o servidor")
        p = subprocess.Popen(args, cwd=str(Path(script).parent), env=env,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        code = p.wait()
        if code == 0:
            log.info("supervisor: servidor encerrado normalmente")
            return 0
        if code == RESTART_CODE:
            log.info("supervisor: reinício pedido")
            continue
        now = time.monotonic()
        crashes = [t for t in crashes if now - t < WINDOW_S] + [now]
        if len(crashes) >= MAX_CRASHES:
            log.error("supervisor: %d quedas em %d min (última: código %s); desisti de religar",
                      len(crashes), WINDOW_S // 60, code)
            return 1
        wait = next_delay(len(crashes))
        if now - started > 60:    # rodou bem por mais de 1 min: a próxima espera volta ao início
            crashes = [now]
            wait = BACKOFF[0]
        log.warning("supervisor: servidor caiu (código %s); religando em %ds", code, wait)
        time.sleep(wait)
