"""Trello SOMENTE LEITURA: meus cards abertos (members/me/cards), com nomes de quadro e lista.

- Só GET, só https://api.trello.com/1/. Nenhum POST/PUT/DELETE existe neste módulo (há teste que garante).
- Credenciais (key + token de LEITURA) ficam no config.json do PC e vão no cabeçalho Authorization (OAuth), nunca
  na URL; nada delas (nem do Trello: nomes, cards, quadros) vai para o log, e as mensagens de erro são fixas.
- Roda na fila de disco do servidor (sem locks). Cache em trello.json (gravação atômica).
- Fuso do agrupamento por prazo: America/Sao_Paulo (UTC-3 fixo se o sistema não tem tzdata: o Brasil não tem horário de verão desde 2019).
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("companion.trello")

API = "https://api.trello.com/1/"
TIMEOUT_S = 10
REFRESH_S = 300          # automático, só com aparelho
MANUAL_MIN_S = 60        # ao abrir Tarefas, no máximo 1x por minuto
LISTS_TTL_S = 6 * 3600
SHORT_RX = re.compile(r"^[A-Za-z0-9]{8}$")
CRED_RX = re.compile(r"^[A-Za-z0-9_-]{16,200}$")
GROUPS = ("overdue", "today", "tomorrow", "week", "later", "none")
CARD_FIELDS = "name,due,dueComplete,idBoard,idList,labels,shortLink,dateLastActivity"

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:  # noqa: BLE001  (sem tzdata no Windows)
    TZ = timezone(timedelta(hours=-3))


class CredError(Exception):
    """401/403: key/token recusados."""


class NetError(Exception):
    """Rede, timeout, resposta inesperada."""


def valid_cred(v, what):
    if not isinstance(v, str) or not CRED_RX.match(v.strip()):
        raise ValueError(f"{what} inválido")
    return v.strip()


def http_get(path, params, key, token):
    """Único acesso à rede deste módulo. Erros sempre viram CredError/NetError com texto fixo."""
    q = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(API + path + ("?" + q if q else ""), method="GET", headers={
        "Authorization": f'OAuth oauth_consumer_key="{key}", oauth_token="{token}"', "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:   # noqa: S310  (host fixo, https)
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise (CredError if e.code in (401, 403) else NetError)(f"http {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        raise NetError("rede") from None


def due_local(due):
    """ISO UTC do Trello -> datetime no fuso do Brasil, ou None."""
    if not isinstance(due, str):
        return None
    try:
        return datetime.fromisoformat(due.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        return None


def group_of(due, today):
    """overdue|today|tomorrow|week|later|none para a data (no fuso do Brasil); sem prazo = none, nunca inventa."""
    dt = due_local(due)
    if dt is None:
        return "none"
    d = dt.date()
    if d < today:
        return "overdue"
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    if d <= today + timedelta(days=6 - today.weekday()):   # até o domingo desta semana
        return "week"
    return "later"


class Trello:
    def __init__(self, base: Path, get_cfg, fetch=http_get, clock=time.time):
        self.path = Path(base) / "trello.json"
        self.get_cfg, self.fetch, self.clock = get_cfg, fetch, clock
        self.cards, self.boards, self.lists = [], {}, {}
        self.at = self.lists_at = self.try_at = 0.0
        self.blocked = False          # 401/403: para de tentar até salvar a credencial de novo
        self.error = None             # "net" na última tentativa
        self.user = None
        self.notified = ""
        self._load()

    # -- disco ----------------------------------------------------------------
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None
            if not isinstance(d, dict) or d.get("v") != 1:
                return
            self.cards = [c for c in d.get("cards", []) if isinstance(c, dict) and SHORT_RX.match(str(c.get("short", "")))]
            self.boards = d.get("boards") if isinstance(d.get("boards"), dict) else {}
            self.lists = d.get("lists") if isinstance(d.get("lists"), dict) else {}
            self.at, self.lists_at = float(d.get("at") or 0), float(d.get("lists_at") or 0)
            self.blocked, self.notified = bool(d.get("blocked")), str(d.get("notified") or "")
        except (OSError, ValueError, TypeError):
            log.warning("trello.json inválido: ignorado")

    def _save(self):
        data = {"v": 1, "cards": self.cards, "boards": self.boards, "lists": self.lists, "at": self.at,
                "lists_at": self.lists_at, "blocked": self.blocked, "notified": self.notified}
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.warning("trello.json: %s", type(e).__name__)

    # -- configuração ---------------------------------------------------------
    def _cred(self):
        c = self.get_cfg() or {}
        k, t = c.get("key"), c.get("token")
        return (k, t) if isinstance(k, str) and isinstance(t, str) and k and t else None

    def enabled(self):
        c = self.get_cfg() or {}
        return bool(c.get("on")) and self._cred() is not None

    def reset(self):
        """Credencial regravada: tenta de novo e não guarda dados da conta anterior."""
        self.blocked, self.error, self.try_at, self.user = False, None, 0.0, None
        self.cards, self.boards, self.lists, self.at, self.lists_at = [], {}, {}, 0.0, 0.0
        self._save()

    def status(self):
        c = self.get_cfg() or {}
        cred = self._cred()
        state = "off" if not c.get("on") or not cred else "bad_cred" if self.blocked else "net_error" if self.error else "ok" if self.at else "waiting"
        return {"on": bool(c.get("on")), "has_key": bool(c.get("key")), "has_token": bool(c.get("token")),
                "state": state, "at": self.at or None, "user": self.user}

    # -- leitura --------------------------------------------------------------
    def due(self, has_clients):
        if not has_clients or not self.enabled() or self.blocked:
            return False
        return self.clock() - self.try_at >= (MANUAL_MIN_S if self.error else REFRESH_S)   # depois de erro de rede, tenta em 1 min

    def refresh_manual(self):
        """Ao abrir Tarefas: no máximo 1x por minuto. True se buscou."""
        if not self.enabled() or self.blocked or self.clock() - self.try_at < MANUAL_MIN_S:
            return False
        return self.refresh()

    def test(self, key, token):
        """Botão "Testar": só o nome do usuário. Levanta CredError/NetError."""
        me = self.fetch("members/me", {"fields": "fullName,username"}, key, token)
        name = me.get("fullName") or me.get("username") if isinstance(me, dict) else None
        if not name:
            raise NetError("resposta")
        return str(name)[:80]

    def refresh(self):
        """Busca cards, quadros e listas. True se mudou algo. Nunca levanta."""
        cred = self._cred()
        if not cred or self.blocked:
            return False
        key, token = cred
        self.try_at = self.clock()
        try:
            raw = self.fetch("members/me/cards", {"filter": "open", "fields": CARD_FIELDS}, key, token)
            if not isinstance(raw, list):
                raise NetError("resposta")
            now = self.clock()
            need_lists = now - self.lists_at > LISTS_TTL_S or any(
                isinstance(c, dict) and c.get("idList") not in self.lists for c in raw)
            if need_lists or any(isinstance(c, dict) and c.get("idBoard") not in self.boards for c in raw):
                bs = self.fetch("members/me/boards", {"fields": "name", "filter": "open"}, key, token)
                boards = {b["id"]: str(b.get("name", ""))[:120] for b in bs if isinstance(b, dict) and b.get("id")}
                lists = {}
                for bid in sorted({c.get("idBoard") for c in raw if isinstance(c, dict) and c.get("idBoard")}):
                    ls = self.fetch(f"boards/{bid}/lists", {"fields": "name", "filter": "all"}, key, token)
                    for li in ls:
                        if isinstance(li, dict) and li.get("id"):
                            lists[li["id"]] = {"name": str(li.get("name", ""))[:120], "board": bid}
                self.boards, self.lists, self.lists_at = boards, lists, now
            cards = []
            for c in raw:
                if not isinstance(c, dict) or not SHORT_RX.match(str(c.get("shortLink", ""))):
                    continue
                cards.append({"short": c["shortLink"], "name": str(c.get("name", ""))[:200],
                              "board": self.boards.get(c.get("idBoard"), ""), "list": (self.lists.get(c.get("idList")) or {}).get("name", ""),
                              "due": c["due"] if isinstance(c.get("due"), str) else None, "done": bool(c.get("dueComplete")),
                              "labels": [{"n": str(lb.get("name", ""))[:40], "c": str(lb.get("color") or "")[:16]}
                                         for lb in (c.get("labels") or []) if isinstance(lb, dict)][:8],
                              "act": c.get("dateLastActivity") if isinstance(c.get("dateLastActivity"), str) else None})
            self.cards, self.at, self.error = cards, now, None
            self._save()
            return True
        except CredError:
            self.blocked, self.error = True, None
            self._save()
            log.warning("trello: credencial recusada")
            return True
        except NetError:
            self.error = "net"          # mantém a última leitura
            return True
        except Exception as e:  # noqa: BLE001
            self.error = "net"
            log.warning("trello: %s", type(e).__name__)
            return True

    # -- estado para o celular e o painel ---------------------------------------
    def today(self):
        return datetime.fromtimestamp(self.clock(), TZ).date()

    def state(self, times=None, links=None):
        """Estado do celular: grupos por prazo (cards com prazo concluído ficam de fora) + tempo dos cards ligados.
        Nunca leva credencial."""
        today = self.today()
        groups = {g: [] for g in GROUPS}
        hidden = 0
        for c in self.cards:
            if c["done"]:
                hidden += 1
                continue
            dt = due_local(c["due"])
            item = {"short": c["short"], "name": c["name"], "board": c["board"], "list": c["list"], "labels": c["labels"],
                    "due": dt.date().isoformat() if dt else None, "time": dt.strftime("%H:%M") if dt else None}
            if times and c["short"] in times:
                item["spent"] = times[c["short"]]
            groups[group_of(c["due"], today)].append(item)
        for g in GROUPS:
            groups[g].sort(key=lambda x: (x["due"] or "9999", x["time"] or "", x["name"].casefold()))
        st = self.status()
        return {"state": st["state"], "on": st["on"], "at": st["at"], "hhmm": datetime.fromtimestamp(self.at, TZ).strftime("%H:%M") if self.at else None,
                "today": today.isoformat(), "groups": groups, "hidden_done": hidden,
                "counts": {g: len(groups[g]) for g in GROUPS}}

    def all_cards(self):
        """Para o painel (busca ao ligar um card a um job): todos os meus cards abertos."""
        return [{"short": c["short"], "name": c["name"], "board": c["board"], "list": c["list"]} for c in self.cards]

    def has_card(self, short):
        return isinstance(short, str) and bool(SHORT_RX.match(short)) and any(c["short"] == short for c in self.cards)

    def card_name(self, short):
        return next((c["name"] for c in self.cards if c["short"] == short), "")

    # -- aviso das 9h ---------------------------------------------------------
    def digest(self):
        """Uma vez por dia, a partir das 9h (horário do Brasil), com os atrasados e os de hoje. None se nada a avisar."""
        now = datetime.fromtimestamp(self.clock(), TZ)
        day = now.date().isoformat()
        if now.hour < 9 or self.notified == day or not self.at or self.blocked:
            return None
        self.notified = day
        self._save()
        st = self.state()
        n_o, n_t = st["counts"]["overdue"], st["counts"]["today"]
        if not n_o and not n_t:
            return None
        names = [c["name"] for c in st["groups"]["overdue"][:3]] + [c["name"] for c in st["groups"]["today"][:3]]
        parts = []
        if n_o:
            parts.append(f"{n_o} atrasado{'s' if n_o != 1 else ''}")
        if n_t:
            parts.append(f"{n_t} para hoje")
        return {"title": "Trello: " + " · ".join(parts), "body": "; ".join(names[:4]), "overdue": n_o, "today": n_t}
