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

DEFAULT_CFG = {"bridge": "http://127.0.0.1:32123/companion/v1/state", "auto_open": True,
               "steam_id": "", "gsi_port": 8771, "gsi_token": "", "dota_dir": ""}
TIMEOUT_S = 0.3
MAX_BYTES = 512 * 1024
PHASES = ("offline", "menu", "draft", "pregame", "game", "postgame")
REASON_DOWN = "DotaCopilot fechado"
OPEN_INTERVAL_S = 1
CLOSED_INTERVAL_S = 10


def offline(reason=REASON_DOWN):
    return {"v": 1, "phase": "offline", "reason": reason, "lobby": None, "hero": None, "live": None,
            "result": None}


def with_process(data, bridge_up, running, started):
    """A única regra derivada do Companion (fatos: processo do Dota aberto, visto pelo games.py, + ponte no ar
    respondendo "offline", porque o Dota não manda GSI no menu principal) => phase "menu". Nada mais muda.
    O bloco "companion" diz de onde veio: dota_running/dota_started (abertura do jogo, para o app abrir a tela
    uma vez por abertura) e menu_from_process (a fase menu veio desta regra)."""
    out = dict(data)
    menu = bool(bridge_up and running and data.get("phase") == "offline")
    if menu:
        out["phase"] = "menu"
        out.pop("reason", None)
    out["companion"] = {"dota_running": bool(running),
                        "dota_started": int(started) if running and started else None,
                        "menu_from_process": menu}
    return out


def compose(bridge, up, gsi, od, lookup, running, started, od_status=None):
    """Estado final para os aparelhos. Prioridade (o Copilot é opcional): 1) ponte do Copilot com fase real; 2) GSI direta
    (recente); 3) ponte no ar (menu pelo processo ou offline com o lobby dela); 4) Dota aberto sem GSI = menu pelo processo;
    5) fechado = offline sem motivo (a tela diz "Dota fechado"). lobby ausente vem do OpenDota (od = OpenDota ou None);
    hero.mine e o resultado oficial também. Nada é estimado: sem fonte o campo fica null."""
    bridge = bridge or offline()
    bc = bridge.get("companion") or {}
    from_bridge = bool(up) and bridge.get("phase") != "offline" and not bc.get("menu_from_process")
    if from_bridge:
        out, src = dict(bridge), "copilot"
    elif gsi:
        out, src = dict(gsi), "gsi"
    elif up:
        out, src = dict(bridge), "copilot"
    elif running:
        out, src = {**offline(), "phase": "menu"}, "process"
        out.pop("reason", None)
    else:
        out, src = {**offline(), "reason": None}, None
    out.pop("companion", None)
    if out.get("lobby") is None and od is not None:
        out["lobby"] = od.lobby()
    h = out.get("hero")
    if isinstance(h, dict) and h.get("mine") is None and od is not None and h.get("hero_id"):
        out["hero"] = {**h, "mine": od.hero_mine(h["hero_id"])}
    r = out.get("result")
    off = getattr(od, "official", None) if od is not None else None
    if isinstance(r, dict) and off and r.get("match_id") == off.get("match_id"):
        out["result"] = {**r, "official": {k: off.get(k) for k in ("win", "k", "d", "a", "duration_s", "gpm", "xpm", "last_hits", "hero_damage", "source")}}
    out["companion"] = {"dota_running": bool(running),
                        "dota_started": int(started) if running and started else None,
                        "menu_from_process": src == "process" or bool(bc.get("menu_from_process")) and src == "copilot",
                        "source": src, "opendota": od_status}
    return out


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

    def poll(self, running=False, started=None):
        """Consulta a ponte e devolve o estado (o dict do contrato + o bloco "companion").
        running/started: o Dota está aberto (games.py) e desde quando (epoch s)."""
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
        self.state = with_process(data, up, running, started)
        return self.state
