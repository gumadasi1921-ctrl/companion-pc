"""Ponte do DotaCopilot (contrato v1 em docs/dota-bridge.md).

Lê GET http://127.0.0.1:32123/companion/v1/state (só leitura, mesmo PC) com urllib e timeout curto.
Roda na fila de disco do servidor (IO_POOL), então não há locks. O Companion não calcula nada: repassa o
JSON da ponte. Ponte fora do ar (ou resposta que não é o contrato v1) = phase "offline" com o motivo.
No log só a mudança "ponte do DotaCopilot ok/fora do ar" (nunca o conteúdo).
"""
import http.client
import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("companion.dota")

DEFAULT_CFG = {"bridge": "http://127.0.0.1:32123/companion/v1/state", "auto_open": True}
TIMEOUT_S = 0.3
MAX_BYTES = 512 * 1024
PHASES = ("offline", "menu", "draft", "pregame", "game", "postgame")
REASON_DOWN = "DotaCopilot fechado"
OPEN_INTERVAL_S = 1
CLOSED_INTERVAL_S = 10


def offline(reason=REASON_DOWN):
    return {"v": 1, "phase": "offline", "reason": reason, "lobby": None, "hero": None, "live": None,
            "result": None}


def same(a, b):
    """Mesmo estado para os aparelhos (updated_at muda a cada resposta e não conta)."""
    if a is None or b is None:
        return a is b
    strip = lambda d: {k: v for k, v in d.items() if k != "updated_at"}  # noqa: E731
    return strip(a) == strip(b)


class DotaBridge:
    def __init__(self, cfg=None, clock=time.monotonic, opener=None):
        self.cfg = {**DEFAULT_CFG, **(cfg or {})}
        self.clock = clock
        # sem proxy do sistema: a ponte é local
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        self.state = None
        self.up = None          # None = ainda não consultou
        self.last_poll = None

    @property
    def auto_open(self):
        return bool(self.cfg.get("auto_open"))

    def due(self, dota_open):
        """Hora de consultar: 1×/s com o Dota aberto, a cada 10 s fechado."""
        if self.last_poll is None:
            return True
        interval = OPEN_INTERVAL_S if dota_open else CLOSED_INTERVAL_S
        return self.clock() - self.last_poll >= interval - 0.05

    def _fetch(self):
        url = str(self.cfg.get("bridge") or "")
        if not url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("ponte fora do PC")  # só a ponte local; nunca um endereço da rede
        with self.opener(urllib.request.Request(url, headers={"Accept": "application/json"}),
                         timeout=TIMEOUT_S) as resp:
            raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("resposta grande demais")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("phase") not in PHASES:
            raise ValueError("não é o contrato v1")
        return data

    def poll(self):
        """Consulta a ponte e devolve o estado (sempre um dict do contrato)."""
        self.last_poll = self.clock()
        try:
            data = self._fetch()
            up = True
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, TimeoutError):
            data = offline()
            up = False
        if up != self.up:
            log.info("ponte do DotaCopilot %s", "ok" if up else "fora do ar")
            self.up = up
        self.state = data
        return data
