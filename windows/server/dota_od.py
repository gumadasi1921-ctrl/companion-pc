"""OpenDota (https://api.opendota.com, sem chave, SOMENTE GET) para a aba Dota sem o DotaCopilot.

Só o Steam ID público configurado no painel (SteamID64 ou o número de 32 bits). Endpoints:
  /players/{id}               perfil e medalha (rank_tier)
  /players/{id}/recentMatches últimas partidas (herói, V/D, KDA, duração, GPM/XPM; também serve de "resultado oficial")
  /players/{id}/heroes        mais jogados
  /players/{id}/wl            vitórias e derrotas totais
  /constants/heroes           id -> nome (e caminho da imagem no CDN da Valve)
  /constants/items            nome interno -> id, nome, custo (e imagem)
Cache em disco (dota-od.json ao lado do config.json, atômico, corrompido vira .bad-<data-hora>): perfil/recentes/heróis/wl
renovam a cada 10 min só com aparelho conectado; as constantes a cada 7 dias. Limite próprio de 500 chamadas por dia
(o plano grátis dá 2000/dia e 60/min); 429 ou erro = recuo e a última cópia continua valendo, com a data.
No log só "OpenDota ok/erro" (nunca ID, nome ou conteúdo). Nada é escrito em nenhuma API."""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request

from dota_gsi import steam64, STEAM64_BASE

log = logging.getLogger("companion.dota.od")

BASE = "https://api.opendota.com/api"
CDN_HOST = "https://cdn.cloudflare.steamstatic.com"
TTL_S = 600
CONST_TTL_S = 7 * 86400
DAILY_MAX = 500
TIMEOUT_S = 8
MAX_BYTES = 8 * 1024 * 1024
BACKOFF_429_S = 300
BACKOFF_ERR_S = 60
WATCH_EVERY_S, WATCH_MAX_S, WATCH_MAX_CALLS = 30, 600, 20
RECENT_KEEP = 10
TOP_KEEP = 10
MEDALS = ("Arauto", "Guardião", "Cruzado", "Arconte", "Lenda", "Ancião", "Divino", "Imortal")
_IMG_PATH = re.compile(r"/apps/dota2/images/[a-z0-9_/\-]{1,120}\.png")
SECTIONS = ("profile", "recent", "heroes", "wl")


class OdError(Exception):
    def __init__(self, kind, code=None):
        super().__init__(kind)
        self.kind, self.code = kind, code


def account_id(value):
    """Número de 32 bits (account_id do OpenDota) a partir do que o painel guarda, ou None."""
    s = steam64(value)
    return None if s is None else s - STEAM64_BASE


def medal(rank_tier):
    """'Lenda 3' a partir do rank_tier do OpenDota (dezena = medalha, unidade = estrelas); None se não houver."""
    if not isinstance(rank_tier, int) or isinstance(rank_tier, bool) or rank_tier < 10:
        return None
    tier, stars = divmod(rank_tier, 10)
    if not 1 <= tier <= len(MEDALS):
        return None
    name = MEDALS[tier - 1]
    return f"{name} {stars}" if 1 <= stars <= 5 and tier < 8 else name


def http_get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "companion-pc"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise OdError("limite" if e.code == 429 else "http", e.code) from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise OdError("rede") from None
    if len(raw) > MAX_BYTES:
        raise OdError("grande")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise OdError("formato") from None


def _i(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def trim_profile(d):
    if not isinstance(d, dict):
        raise OdError("formato")
    p = d.get("profile") if isinstance(d.get("profile"), dict) else {}
    name = p.get("personaname")
    return {"name": name[:40] if isinstance(name, str) else None, "rank_tier": _i(d.get("rank_tier")),
            "leaderboard": _i(d.get("leaderboard_rank")), "has_profile": bool(p)}


def trim_recent(d):
    if not isinstance(d, list):
        raise OdError("formato")
    out = []
    for m in d[:RECENT_KEEP]:
        if not isinstance(m, dict) or _i(m.get("match_id")) is None:
            continue
        slot, rw = _i(m.get("player_slot")), m.get("radiant_win")
        out.append({"match_id": m["match_id"], "hero_id": _i(m.get("hero_id")),
                    "win": (slot < 128) == rw if slot is not None and isinstance(rw, bool) else None,
                    "k": _i(m.get("kills")), "d": _i(m.get("deaths")), "a": _i(m.get("assists")),
                    "duration_s": _i(m.get("duration")), "start": _i(m.get("start_time")),
                    "gpm": _i(m.get("gold_per_min")), "xpm": _i(m.get("xp_per_min")),
                    "last_hits": _i(m.get("last_hits")), "hero_damage": _i(m.get("hero_damage"))})
    return out


def trim_heroes(d):
    if not isinstance(d, list):
        raise OdError("formato")
    out = []
    for h in d[:TOP_KEEP]:
        try:
            hid, g, w = int(h["hero_id"]), _i(h.get("games")), _i(h.get("win"))
        except (KeyError, TypeError, ValueError):
            continue
        if g:
            out.append({"hero_id": hid, "games": g, "win": w, "last_played": _i(h.get("last_played"))})
    return out


def trim_wl(d):
    if not isinstance(d, dict) or _i(d.get("win")) is None or _i(d.get("lose")) is None:
        raise OdError("formato")
    return {"win": d["win"], "lose": d["lose"]}


def _img(v):
    p = v.split("?", 1)[0] if isinstance(v, str) else ""
    return p if _IMG_PATH.fullmatch(p) else None


def trim_const_heroes(d):
    if not isinstance(d, dict):
        raise OdError("formato")
    out = {}
    for h in d.values():
        if isinstance(h, dict) and _i(h.get("id")) and isinstance(h.get("localized_name"), str):
            out[str(h["id"])] = {"n": h["localized_name"][:40], "img": _img(h.get("img")), "icon": _img(h.get("icon"))}
    if not out:
        raise OdError("formato")
    return out


def trim_const_items(d):
    if not isinstance(d, dict):
        raise OdError("formato")
    out = {}
    for key, it in d.items():
        if isinstance(it, dict) and _i(it.get("id")) and isinstance(it.get("dname"), str) and re.fullmatch(r"[a-z0-9_]{1,60}", key):
            out[key] = {"id": it["id"], "n": it["dname"][:40], "cost": _i(it.get("cost")), "img": _img(it.get("img"))}
    if not out:
        raise OdError("formato")
    return out


class OpenDota:
    def __init__(self, path, steam_fn, fetch=http_get, clock=time.time):
        self.path, self.steam_fn, self.fetch, self.clock = path, steam_fn, fetch, clock
        self.lock = threading.Lock()
        self.data = self._load()
        self.blocked_until = 0.0
        self.error = None            # tipo do último erro (None = ok)
        self.watch = None            # {"id", "since", "last", "calls"}
        self.official = None         # entrada de recentMatches da partida observada, quando aparece
        self._sync_account()

    # ------------------------------------------------------------ disco
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("v") == 1:
                d.setdefault("sec", {})
                d.setdefault("consts", {})
                d.setdefault("calls", {"day": "", "n": 0})
                return d
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".bad-" + time.strftime("%Y%m%d-%H%M%S")))
            except OSError:
                pass
        return {"v": 1, "account": None, "sec": {}, "consts": {}, "calls": {"day": "", "n": 0}}

    def _save(self):
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _sync_account(self):
        """Steam ID trocado: esquece perfil/partidas da conta anterior (as constantes ficam)."""
        acc = account_id(self.steam_fn())
        if self.data.get("account") != acc:
            self.data["account"] = acc
            self.data["sec"] = {}
            self.watch = self.official = None
            self._save()
        return acc

    # ------------------------------------------------------------ chamadas
    def _day(self):
        return time.strftime("%Y-%m-%d", time.localtime(self.clock()))

    def calls_today(self):
        c = self.data["calls"]
        return c["n"] if c.get("day") == self._day() else 0

    def _get(self, path):
        now = self.clock()
        if now < self.blocked_until:
            raise OdError("espera")
        c = self.data["calls"]
        if c.get("day") != self._day():
            c["day"], c["n"] = self._day(), 0
        if c["n"] >= DAILY_MAX:
            raise OdError("cota")
        c["n"] += 1
        try:
            return self.fetch(BASE + path)
        except OdError as e:
            self.blocked_until = now + (BACKOFF_429_S if e.kind == "limite" else BACKOFF_ERR_S)
            raise

    def _fill(self, group, key, path, trim):
        d = trim(self._get(path))
        self.data[group][key] = {"at": int(self.clock()), "d": d}
        self._save()

    def _stale(self, group, key, ttl):
        e = self.data[group].get(key)
        return not e or self.clock() - e["at"] >= ttl

    # ------------------------------------------------------------ agenda de leituras
    def watch_match(self, match_id):
        """Pós-jogo: procura essa partida em recentMatches (a cada 30 s, até 10 min e 20 chamadas)."""
        mid = _i(match_id)
        if mid is None or (self.watch and self.watch["id"] == mid) or (self.official and self.official["match_id"] == mid):
            return
        self.official = None
        self.watch = {"id": mid, "since": self.clock(), "last": 0.0, "calls": 0}

    def _watching(self):
        w = self.watch
        if not w:
            return False
        if self.clock() - w["since"] > WATCH_MAX_S or w["calls"] >= WATCH_MAX_CALLS:
            self.watch = None
            return False
        return bool(account_id(self.steam_fn()))

    def needs(self, has_clients=True):
        """Lista do que está vencido (vazia = nada a fazer agora)."""
        if not has_clients or self.clock() < self.blocked_until:
            return []
        acc = self._sync_account()
        todo = []
        if self._stale("consts", "heroes", CONST_TTL_S):
            todo.append(("consts", "heroes"))
        if self._stale("consts", "items", CONST_TTL_S):
            todo.append(("consts", "items"))
        if acc:
            todo += [("sec", s) for s in SECTIONS if self._stale("sec", s, TTL_S)]
            if self._watching() and self.clock() - self.watch["last"] >= WATCH_EVERY_S and ("sec", "recent") not in todo:
                todo.append(("sec", "recent"))
        return todo

    def due(self, has_clients=True):
        return bool(self.needs(has_clients))

    def refresh(self, force=False, has_clients=True):
        """Faz as leituras vencidas (uma por vez, parando no primeiro erro). True se algo mudou."""
        with self.lock:
            acc = self._sync_account()
            todo = self.needs(has_clients) if not force else [("sec", s) for s in SECTIONS] if acc else []
            changed = False
            for group, key in todo:
                try:
                    if group == "consts":
                        self._fill("consts", key, f"/constants/{key}", trim_const_heroes if key == "heroes" else trim_const_items)
                    else:
                        path = {"profile": f"/players/{acc}", "recent": f"/players/{acc}/recentMatches",
                                "heroes": f"/players/{acc}/heroes", "wl": f"/players/{acc}/wl"}[key]
                        trim = {"profile": trim_profile, "recent": trim_recent, "heroes": trim_heroes, "wl": trim_wl}[key]
                        if key == "recent" and self.watch:
                            self.watch["last"], self.watch["calls"] = self.clock(), self.watch["calls"] + 1
                        self._fill("sec", key, path, trim)
                        if key == "recent":
                            self._check_watch()
                    changed = True
                    if self.error:
                        log.info("OpenDota ok")
                    self.error = None
                except OdError as e:
                    if e.kind != "espera" and self.error != e.kind:
                        log.warning("OpenDota erro (%s)", e.kind)
                    self.error = e.kind
                    self._save()
                    break
            return changed

    def refresh_manual(self):
        """Botão 'atualizar': no mínimo 60 s entre pedidos. Devolve (mudou, segundos_para_poder_de_novo)."""
        wait = 60 - (self.clock() - getattr(self, "_manual_at", -1e9))
        if wait > 0:
            return False, int(wait) + 1
        self._manual_at = self.clock()
        return self.refresh(force=True), 0

    def _check_watch(self):
        w = self.watch
        if not w:
            return
        for m in self.data["sec"]["recent"]["d"]:
            if m["match_id"] == w["id"]:
                self.official = {**m, "source": "opendota"}
                self.watch = None
                return

    # ------------------------------------------------------------ consulta (usado pelo servidor)
    def hero_name(self, hid):
        h = (self.data["consts"].get("heroes") or {}).get("d", {}).get(str(hid))
        return h["n"] if h else None

    def item(self, raw):
        """Item da GSI ('item_blink') -> {'id','name','cost'} do catálogo, ou None."""
        key = raw[5:] if isinstance(raw, str) and raw.startswith("item_") else raw
        it = (self.data["consts"].get("items") or {}).get("d", {}).get(key)
        return {"id": it["id"], "name": it["n"], "cost": it["cost"]} if it else None

    def img_path(self, kind, ident):
        """Caminho da imagem no CDN da Valve para um id JÁ conhecido (hero icon|portrait|loadout, ou item); senão None."""
        if kind == "item":
            for it in (self.data["consts"].get("items") or {}).get("d", {}).values():
                if it["id"] == ident:
                    return it["img"]
            return None
        h = (self.data["consts"].get("heroes") or {}).get("d", {}).get(str(ident))
        return (h["icon"] if kind == "icon" else h["img"]) if h else None

    def _sec(self, key):
        e = self.data["sec"].get(key)
        return e["d"] if e else None

    def hero_mine(self, hid):
        """{'matches','winrate','last_played','avg_kda'} do herói nos mais jogados, ou None (só os top; nunca estima)."""
        for h in self._sec("heroes") or []:
            if h["hero_id"] == hid:
                return {"matches": h["games"], "winrate": round(h["win"] / h["games"], 3) if h["win"] is not None else None,
                        "last_played": h["last_played"], "avg_kda": None}
        return None

    def lobby(self):
        """Bloco 'lobby' no formato da ponte, ou None sem dado (nunca inventa)."""
        prof, rec, top, wl = (self._sec(k) for k in SECTIONS)
        if not self.steam_fn() or (prof is None and rec is None and top is None):
            return None
        out = {"mmr": None, "today": None, "streak": None, "recent": [], "top_heroes": [], "source": "opendota"}
        if prof:
            md = medal(prof["rank_tier"])
            if md:
                out["mmr"] = {"value": None, "estimated": False, "medal": md}
            out["profile"] = {"name": prof["name"]}
        if wl:
            out["record"] = wl
        if rec is not None:
            out["recent"] = [{"match_id": m["match_id"], "hero_id": m["hero_id"], "hero": self.hero_name(m["hero_id"]), "win": m["win"],
                              "k": m["k"], "d": m["d"], "a": m["a"], "duration_s": m["duration_s"],
                              "ended_at": m["start"] + m["duration_s"] if m["start"] is not None and m["duration_s"] is not None else None}
                             for m in rec]
            lt = time.localtime(self.clock())
            midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
            day = [m for m in out["recent"] if m["ended_at"] is not None and m["ended_at"] >= midnight and m["win"] is not None]
            out["today"] = {"wins": sum(1 for m in day if m["win"]), "losses": sum(1 for m in day if not m["win"])}
            wins = [m["win"] for m in out["recent"] if m["win"] is not None]
            if wins:
                n = next((i for i, w in enumerate(wins) if w != wins[0]), len(wins))
                out["streak"] = n if wins[0] else -n
        out["top_heroes"] = [{"hero_id": h["hero_id"], "hero": self.hero_name(h["hero_id"]), "matches": h["games"],
                              "winrate": round(h["win"] / h["games"], 3) if h["win"] is not None else None}
                             for h in (top or [])[:5]]
        ats = [e["at"] for k, e in self.data["sec"].items()]
        out["fetched_at"] = min(ats) if ats else None
        out["stale"] = bool(self.error) or (bool(ats) and self.clock() - min(ats) > 2 * TTL_S)
        return out

    def status(self):
        acc = account_id(self.steam_fn())
        prof, rec = self._sec("profile"), self._sec("recent")
        if not acc:
            state = "no_id"
        elif prof is None and rec is None:
            state = "error" if self.error else "pending"
        elif not (prof and (prof["rank_tier"] or prof["has_profile"])) and not rec:
            state = "empty"      # perfil privado ou sem partidas públicas
        else:
            state = "ok"
        ats = [e["at"] for e in self.data["sec"].values()]
        return {"configured": bool(acc), "state": state, "error": self.error, "fetched_at": min(ats) if ats else None,
                "calls_today": self.calls_today(), "calls_max": DAILY_MAX,
                "blocked_s": max(0, int(self.blocked_until - self.clock()))}
