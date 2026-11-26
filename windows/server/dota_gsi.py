"""Game State Integration (GSI) do Dota 2 direto no Companion, sem o DotaCopilot.

O Dota manda um POST JSON para o endereço do arquivo gamestate_integration_companion.cfg. Aqui:
  - GsiListener: servidor HTTP mínimo SÓ em 127.0.0.1 (porta própria, não a do app, que é acessível pela rede e só
    fala GET). Só POST, corpo <= 256 KB, token conferido com compare_digest; o resto = 4xx. Nada vai para o log.
  - GSI: guarda só os campos usados (tipos conferidos) e os traduz para o MESMO formato da ponte do Copilot
    (phase/hero/live/result), sem calcular nada. Payload mais velho que 35 s = sem dado (o Dota não manda GSI no menu
    principal; quem decide "menu" pelo processo é dota.compose).
  - Instalador do .cfg (cfg_text/install/find_dota): cria o arquivo na pasta cfg/gamestate_integration do Dota;
    nunca sobrescreve um arquivo que não seja nosso.
Sem fonte para build, timers, posição e próximo item: ficam null (a tela esconde)."""
import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path

log = logging.getLogger("companion.dota.gsi")

DEFAULT_PORT = 8771
STALE_S = 35               # heartbeat de 30 s + timeout de 5 s (igual ao Copilot)
MAX_BODY = 256 * 1024
MAX_HEAD = 16 * 1024
READ_TIMEOUT_S = 5
STEAM64_BASE = 76561197960265728
CFG_NAME = "gamestate_integration_companion.cfg"
MARKER = "// Companion PC"

PH_DRAFT = {"DOTA_GAMERULES_STATE_WAIT_FOR_PLAYERS_TO_LOAD", "DOTA_GAMERULES_STATE_CUSTOM_GAME_SETUP",
            "DOTA_GAMERULES_STATE_HERO_SELECTION", "DOTA_GAMERULES_STATE_STRATEGY_TIME",
            "DOTA_GAMERULES_STATE_PLAYER_DRAFT", "DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
            "DOTA_GAMERULES_STATE_TEAM_SHOWCASE", "DOTA_GAMERULES_STATE_SCENARIO_SETUP"}
PH_PRE = {"DOTA_GAMERULES_STATE_PRE_GAME"}
PH_GAME = {"DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"}
PH_POST = {"DOTA_GAMERULES_STATE_POST_GAME", "DOTA_GAMERULES_STATE_DISCONNECT"}


def new_token():
    return secrets.token_urlsafe(24)


def steam64(value):
    """SteamID64 a partir do que o painel guarda (17 dígitos ou o número de 32 bits). None se inválido."""
    s = str(value or "").strip()
    if not s.isdigit():
        return None
    n = int(s)
    if n >= STEAM64_BASE:
        return n if len(s) == 17 else None
    return STEAM64_BASE + n if 0 < n < 2**32 else None


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _num(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _str(v, n=80):
    return v[:n] if isinstance(v, str) else None


def sanitize(p):
    """Só os campos usados, com o tipo conferido (o resto do payload é descartado)."""
    if not isinstance(p, dict):
        return None
    m, pl, h, it = (p.get(k) if isinstance(p.get(k), dict) else {} for k in ("map", "player", "hero", "items"))
    items = {}
    for k in [f"slot{i}" for i in range(9)] + ["neutral0"]:
        v = it.get(k)
        name = _str(v.get("name"), 60) if isinstance(v, dict) else None
        if name and name != "empty":
            items[k] = name
    return {
        "map": {"game_state": _str(m.get("game_state"), 60), "matchid": _str(str(m["matchid"]), 24) if m.get("matchid") not in (None, "") else None,
                "clock": _num(m.get("clock_time")), "day": m["daytime"] if isinstance(m.get("daytime"), bool) else None,
                "win_team": _str(m.get("win_team"), 12)} if m else None,
        "player": {"steamid": _str(str(pl["steamid"]), 24) if pl.get("steamid") not in (None, "") else None,
                   "team": _str(pl.get("team_name"), 12), "k": _num(pl.get("kills")), "d": _num(pl.get("deaths")),
                   "a": _num(pl.get("assists")), "lh": _num(pl.get("last_hits")), "dn": _num(pl.get("denies")),
                   "gold": _num(pl.get("gold")), "gpm": _num(pl.get("gpm")), "xpm": _num(pl.get("xpm"))} if pl else None,
        "hero": {"id": _int(h.get("id")), "level": _num(h.get("level"))} if h else None,
        "items": items,
    }


class GSI:
    def __init__(self, token_fn, steam_fn=lambda: "", clock=time.monotonic):
        self.token_fn, self.steam_fn, self.clock = token_fn, steam_fn, clock
        self.snap = None
        self.at = None            # relógio da última carga aceita
        self.count = 0
        self.on_change = None     # chamado (sem argumentos) a cada payload aceito

    def accept(self, payload):
        """True se o token confere e o payload tem o formato esperado."""
        if not isinstance(payload, dict):
            return False
        auth = payload.get("auth")
        tok = auth.get("token") if isinstance(auth, dict) else None
        want = str(self.token_fn() or "")
        if not want or not isinstance(tok, str) or not hmac.compare_digest(tok.encode("utf-8"), want.encode("utf-8")):
            return False
        snap = sanitize(payload)
        if snap is None:
            return False
        self.snap, self.at = snap, self.clock()
        self.count += 1
        if self.on_change:
            self.on_change()
        return True

    def fresh(self):
        return self.snap is not None and self.at is not None and self.clock() - self.at <= STALE_S

    def last_age(self):
        return None if self.at is None else max(0, self.clock() - self.at)

    def match_id(self):
        m = (self.snap or {}).get("map") or {}
        return m.get("matchid")

    def state(self, lookup=None):
        """Estado no formato da ponte (v1) ou None se não há GSI recente. lookup: hero_name(id) e item(nome) opcionais."""
        if not self.fresh():
            return None
        s = self.snap
        m, pl, h = s["map"], s["player"], s["hero"]
        gs = (m or {}).get("game_state")
        if not m:
            phase = "menu"
        elif gs in PH_DRAFT:
            phase = "draft"
        elif gs in PH_PRE:
            phase = "pregame"
        elif gs in PH_GAME:
            phase = "game"
        elif gs in PH_POST:
            phase = "postgame"
        else:
            phase = "menu"   # estado desconhecido: o mesmo critério do Copilot
        want = steam64(self.steam_fn())
        mine = bool(pl) and bool(pl.get("steamid")) and (want is None or str(want) == pl["steamid"])   # espectador (sem steamid) ou outra conta: sem dados
        hid = h.get("id") if h else None
        hero = None
        if phase != "menu" and mine and hid and hid > 0:
            name = lookup.hero_name(hid) if lookup else None
            hero = {"hero_id": hid, "hero": name, "position": None, "position_source": None, "mine": None, "build": None}
        live = None
        if phase in ("pregame", "game") and mine:
            names, detail = [], []
            for i, key in enumerate([f"slot{n}" for n in range(9)] + ["neutral0"]):
                raw = s["items"].get(key)
                if not raw:
                    continue
                info = lookup.item(raw) if lookup else None
                nm = (info or {}).get("name") or raw
                names.append(nm)
                detail.append({"item": nm, "item_id": (info or {}).get("id"), "slot": i})
            live = {"clock_s": m.get("clock"), "daytime": m.get("day"), "gold": pl.get("gold"), "gpm": pl.get("gpm"),
                    "xpm": pl.get("xpm"), "k": pl.get("k"), "d": pl.get("d"), "a": pl.get("a"), "last_hits": pl.get("lh"),
                    "denies": pl.get("dn"), "level": h.get("level") if h else None, "items": names,
                    "items_detail": detail, "next_item": None, "timers": None}
        result = None
        if phase == "postgame" and mine:
            wt, tm = m.get("win_team"), pl.get("team")
            win = (wt == tm) if wt in ("radiant", "dire") and tm in ("radiant", "dire") else None
            result = {"hero_id": hid if hid and hid > 0 else None, "win": win, "k": pl.get("k"), "d": pl.get("d"),
                      "a": pl.get("a"), "match_id": _int(int(m["matchid"])) if (m.get("matchid") or "").isdigit() else None}
        return {"v": 1, "phase": phase, "lobby": None, "hero": hero, "live": live, "result": result}


# ---------------------------------------------------------------- listener (só 127.0.0.1)
class GsiListener:
    def __init__(self, gsi, port=DEFAULT_PORT):
        self.gsi, self.port, self.server = gsi, int(port), None
        self.bad = 0      # recusados (sem detalhe; só contagem para o painel)

    async def start(self):
        self.server = await asyncio.start_server(self._conn, "127.0.0.1", self.port, limit=MAX_HEAD)
        return self.server

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    @staticmethod
    async def _reply(w, code, text):
        w.write(f"HTTP/1.1 {code} {text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii"))
        try:
            await w.drain()
        except OSError:
            pass

    async def _conn(self, r, w):
        try:
            peer = w.get_extra_info("peername")
            if not peer or peer[0] != "127.0.0.1":
                return
            code, text = await asyncio.wait_for(self._serve(r), READ_TIMEOUT_S)
            if code != 200:
                self.bad += 1
            await self._reply(w, code, text)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError, OSError):
            self.bad += 1
        finally:
            try:
                w.close()
            except OSError:
                pass

    async def _serve(self, r):
        head = await r.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
            return 400, "Bad Request"
        if parts[0] != "POST":
            return 405, "Method Not Allowed"
        hdr = {}
        for ln in lines[1:]:
            if ":" in ln:
                k, _, v = ln.partition(":")
                hdr[k.strip().lower()] = v.strip()
        if "transfer-encoding" in hdr:
            return 411, "Length Required"
        n = hdr.get("content-length", "")
        if not n.isdigit():
            return 411, "Length Required"
        if int(n) > MAX_BODY:
            return 413, "Payload Too Large"
        body = await r.readexactly(int(n))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return 400, "Bad Request"
        if not isinstance(payload, dict):
            return 400, "Bad Request"
        return (200, "OK") if self.gsi.accept(payload) else (403, "Forbidden")


# ---------------------------------------------------------------- arquivo .cfg do Dota
def cfg_text(port, token):
    return (f'{MARKER} (gerado pelo painel do PC; pode apagar este arquivo para desligar)\n'
            '"Companion PC"\n{\n'
            f'    "uri"       "http://127.0.0.1:{int(port)}/"\n'
            '    "timeout"   "5.0"\n    "buffer"    "0.1"\n    "throttle"  "0.5"\n    "heartbeat" "30.0"\n'
            f'    "auth"\n    {{\n        "token" "{token}"\n    }}\n'
            '    "data"\n    {\n        "provider" "1"\n        "map"      "1"\n        "player"   "1"\n'
            '        "hero"     "1"\n        "items"    "1"\n    }\n}\n')


def find_dota(libs):
    """Pasta ...\\dota 2 beta\\game\\dota da primeira biblioteca da Steam que tem o Dota, ou None."""
    for lib in libs:
        d = Path(lib, "steamapps", "common", "dota 2 beta", "game", "dota")
        if d.is_dir():
            return d
    return None


def resolve_dota(folder):
    """A pasta escolhida pelo usuário vira ...\\game\\dota: aceita 'dota 2 beta', 'game', 'dota' ou 'cfg'."""
    p = Path(folder)
    for cand in (p, p / "game" / "dota", p / "dota", p.parent if p.name.lower() == "cfg" else p):
        if cand.name.lower() == "dota" and (cand / "cfg").is_dir():
            return cand
    for cand in (p / "game" / "dota", p / "dota"):
        if (cand / "cfg").is_dir():
            return cand
    if p.name.lower() == "cfg" and (p.parent / "cfg").is_dir():
        return p.parent
    return None


def target_for(dota_dir):
    return Path(dota_dir) / "cfg" / "gamestate_integration" / CFG_NAME


def status(dota_dir, port, token):
    """installed: o arquivo é nosso e tem a porta e o token atuais."""
    if not dota_dir:
        return {"found": False, "installed": False}
    f = target_for(dota_dir)
    try:
        txt = f.read_text(encoding="utf-8")
    except OSError:
        return {"found": True, "installed": False}
    return {"found": True, "installed": txt == cfg_text(port, token), "ours": txt.startswith(MARKER)}


def install(dota_dir, port, token):
    """Cria/atualiza o .cfg. Recusa (ValueError) um arquivo existente que não seja nosso. Atômico."""
    f = target_for(dota_dir)
    try:
        if f.exists() and not f.read_text(encoding="utf-8", errors="ignore").startswith(MARKER):
            raise ValueError("já existe um arquivo com esse nome que não foi criado pelo Companion")
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(CFG_NAME + ".tmp")
        tmp.write_text(cfg_text(port, token), encoding="utf-8")
        os.replace(tmp, f)
    except OSError as e:
        raise ValueError(f"não consegui gravar o arquivo ({type(e).__name__})") from None
    log.info("integração do Dota instalada")
    return {"installed": True}
