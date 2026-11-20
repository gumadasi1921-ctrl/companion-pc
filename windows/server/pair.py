"""Pareamento por código de 6 dígitos: entrega o token a um aparelho novo sem digitar o token longo.

O código é gerado pelo painel do PC, vale 120 s e só uma vez. O token continua sendo a credencial;
o código só serve para entregá-lo. Nada daqui (código, token) vai para o log.
"""
import secrets
import threading
import time

CODE_TTL_S = 120
IP_MAX_FAILS = 5        # erros por IP na janela
IP_WINDOW_S = 60
IP_BLOCK_S = 60
CODE_MAX_FAILS = 20     # erros no total por código


class Pairing:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._code = None
        self._exp = 0.0
        self._fails = 0
        self._dead = None          # None | "tentativas" | "fechado"
        self._ip_fails = {}        # ip -> [instantes dos erros]
        self._ip_block = {}        # ip -> instante em que o bloqueio termina

    # ----------------------------------------------------------- painel do PC
    def new(self):
        """Gera um código novo (o anterior deixa de valer). Devolve o código."""
        with self._lock:
            self._code = f"{secrets.randbelow(10 ** 6):06d}"
            self._exp = self._clock() + CODE_TTL_S
            self._fails = 0
            self._dead = None
            return self._code

    def close(self):
        """Painel fechado: nenhum código vale."""
        with self._lock:
            self._code = None
            self._dead = "fechado"

    def state(self):
        """{'code': str|None, 'left': s, 'dead': None|'tentativas'|'fechado'|'vencido'}"""
        with self._lock:
            if self._code is None:
                return {"code": None, "left": 0, "dead": self._dead}
            left = self._exp - self._clock()
            if left <= 0:
                return {"code": None, "left": 0, "dead": "vencido"}
            return {"code": self._code, "left": int(left + 0.999), "dead": None}

    # ----------------------------------------------------------- servidor
    def check(self, ip, code):
        """Tenta usar o código. Devolve 'ok', 'bad' (errado/vencido) ou 'blocked' (IP bloqueado)."""
        with self._lock:
            now = self._clock()
            if self._ip_block.get(ip, 0) > now:
                return "blocked"
            self._ip_block.pop(ip, None)
            live = self._code is not None and now < self._exp
            ref = (self._code if live else "000000").encode()
            given = str(code if code is not None else "").encode("utf-8", "replace")
            match = secrets.compare_digest(given, ref)
            if match and live:
                self._code = None      # uso único
                self._dead = "usado"
                self._ip_fails.pop(ip, None)
                return "ok"
            # erro: conta no IP e no código
            fails = [t for t in self._ip_fails.get(ip, []) if now - t < IP_WINDOW_S] + [now]
            self._ip_fails[ip] = fails
            if self._code is not None:
                self._fails += 1
                if self._fails >= CODE_MAX_FAILS:
                    self._code = None
                    self._dead = "tentativas"
            if len(fails) >= IP_MAX_FAILS:
                self._ip_block[ip] = now + IP_BLOCK_S
                self._ip_fails.pop(ip, None)   # a 5ª ainda é "errado"; da próxima em diante, recusa
            return "bad"
