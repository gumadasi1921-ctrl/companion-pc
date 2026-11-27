"""Trello SOMENTE LEITURA: meus cards abertos (members/me/cards), com nomes de quadro e lista.

- "A fazer" (a lista do dia, que muda de nome) NÃO é achada por nome: ou pelo id de uma lista escolhida, ou pela posição no quadro
  (campo pos do Trello: "1ª lista"), que acompanha a troca de nome. Lista que sumiu = "não encontrada", nunca adivinha outra.

- Só entram os cards das listas escolhidas (por NOME): "Em execução" (viram tarefas) e "Aprovar" (contam como feito), nos quadros
  que o filtro aceita (todos, prefixo do nome do quadro ou escolhidos; área opcional). Comparação por igualdade sem maiúsculas/espaços.
- Feito = card em Aprovar. Histórico em trello-done.json (nunca podado). Data real pelas actions do quadro (updateCard:idList), senão "visto em".

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
TODO_MODES, MAX_POSITION = ("off", "today", "list", "position"), 20
TODO_DEFAULT = {"mode": "today", "list_id": "", "position": 1, "scope": "all", "job_only": True}     # padrão: lista "DIA <hoje>", todos os cards abertos, só no padrão CLIENTE - JOB
TODO_SCOPES = ("mine", "all")
STAGE_SCOPES = ("mine", "all")        # Em execução / Aprovar: só os meus | todos (meus + sem responsável, só CLIENTE - JOB); cards de OUTRA pessoa nunca entram
SHORT_RX = re.compile(r"^[A-Za-z0-9]{8}$")
CRED_RX = re.compile(r"^[A-Za-z0-9_-]{16,200}$")
GROUPS = ("overdue", "today", "tomorrow", "week", "later", "none")
CARD_FIELDS = "name,due,idBoard,idList,labels,shortLink,dateLastActivity"
ID_RX = re.compile(r"^[A-Za-z0-9]{1,32}$")
NOFOUND = {"boards": 0, "running": 0, "approved": 0, "todo": "off", "todo_clash": 0, "todo_hidden": 0, "running_hidden": 0, "approved_hidden": 0}
MAX_NAMES, MAX_PREFIXES, MAX_IDS, MAX_NAME_LEN = 5, 5, 20, 60
DEFAULT_RUNNING, DEFAULT_APPROVED = ["Em execução"], ["Aprovar"]
SCOPE_MODES = ("all", "prefix", "pick")
ACTION_DAYS, ACTION_PAGES = 30, 3

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




def norm(s):
    """Nome para comparar: sem maiúsculas e com espaços colapsados. Só isso (sem "contém", sem ignorar acento)."""
    return " ".join(str(s or "").split()).casefold()


def _names(v, what, default):
    if v is None:
        return list(default)
    if not isinstance(v, list) or len(v) > MAX_NAMES:
        raise ValueError(f"{what}: no máximo {MAX_NAMES} nomes")
    out, seen = [], set()
    for x in v:
        if not isinstance(x, str) or not x.strip() or len(x.strip()) > MAX_NAME_LEN:
            raise ValueError(f"{what}: nome inválido")
        n = " ".join(x.split())
        if norm(n) not in seen:
            seen.add(norm(n))
            out.append(n)
    return out


def clean_scope(v, cur=None):
    """Filtro de quadros: all | prefix (nome começa com…) | pick (ids escolhidos), mais a área (workspace) opcional."""
    if v is None:
        v = cur if isinstance(cur, dict) else {"mode": "all"}
    if not isinstance(v, dict) or set(v) - {"mode", "prefixes", "ids", "workspace"} or v.get("mode") not in SCOPE_MODES:
        raise ValueError("Trello: filtro de quadros inválido")
    ws = v.get("workspace") or ""
    if not isinstance(ws, str) or len(ws.strip()) > 80:
        raise ValueError("Trello: área inválida")
    pre = _names(v.get("prefixes", []), "prefixo", [])
    ids = v.get("ids", [])
    if not isinstance(ids, list) or len(ids) > MAX_IDS or any(not isinstance(i, str) or not ID_RX.match(i) for i in ids):
        raise ValueError(f"Trello: no máximo {MAX_IDS} quadros")
    ids = list(dict.fromkeys(ids))
    if v["mode"] == "prefix" and not pre:
        raise ValueError("Trello: informe o começo do nome do quadro")
    if v["mode"] == "pick" and not ids:
        raise ValueError("Trello: escolha ao menos um quadro")
    return {"mode": v["mode"], "prefixes": pre, "ids": ids, "workspace": " ".join(ws.split())}


def today_list_rx(day):
    """Regra fixa da lista do dia: nome (já normalizado por norm) começa com "dia " + dia do mês, com ou sem zero à esquerda,
    seguido de espaço, hífen ou fim. "dia 2" nunca casa com "dia 26"; só esse formato, nada de adivinhar outro dia."""
    return re.compile(r"^dia 0*%d(?:[\s-]|$)" % int(day))


def split_name(name):
    """Regra fixa "CLIENTE - JOB": o texto antes do primeiro " - " é o cliente e o resto é o job. Sem " - " (ou um dos lados vazio) = (None, nome)."""
    s = str(name or "")
    i = s.find(" - ")
    cl, jb = s[:i].strip(), s[i + 3:].strip()
    return (cl, jb) if i > 0 and cl and jb else (None, s.strip())


def clean_todo(v, cur=None):
    """A fazer: today (lista "DIA <hoje>", padrão) | list (id da lista) | position (N-ésima lista aberta de cada quadro) | off."""
    if v is None:
        v = cur if isinstance(cur, dict) else {"mode": "today"}
    if not isinstance(v, dict) or set(v) - {"mode", "list_id", "position", "scope", "job_only"} or v.get("mode") not in TODO_MODES:
        raise ValueError("Trello: A fazer inválido")
    sc, jo = v.get("scope", (cur or {}).get("scope", "all") if isinstance(cur, dict) else "all"), v.get("job_only", (cur or {}).get("job_only", True) if isinstance(cur, dict) else True)
    if sc not in TODO_SCOPES or not isinstance(jo, bool):
        raise ValueError("Trello: A fazer inválido")
    out = {"mode": v["mode"], "list_id": "", "position": 1, "scope": sc, "job_only": jo}
    if v["mode"] == "list":
        if not isinstance(v.get("list_id"), str) or not ID_RX.match(v["list_id"]):
            raise ValueError("Trello: escolha a lista do A fazer")
        out["list_id"] = v["list_id"]
    elif v["mode"] == "position":
        n = v.get("position")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_POSITION:
            raise ValueError(f"Trello: posição de 1 a {MAX_POSITION}")
        out["position"] = n
    return out


def clean_cfg(v, cur):
    """config_save: key/token em branco = mantém os salvos (nunca voltam ao painel). Formato antigo {key, token, on} continua valendo."""
    cur = cur if isinstance(cur, dict) else {}
    if not isinstance(v, dict) or set(v) - {"key", "token", "on", "scope", "running", "approved", "todo", "running_scope", "approved_scope"} or not isinstance(v.get("on"), bool):
        raise ValueError("Trello: formato inválido")
    out = {"on": v["on"]}
    for k, label in (("key", "API key"), ("token", "token")):
        new = v.get(k)
        out[k] = valid_cred(new, label) if isinstance(new, str) and new.strip() else cur.get(k, "")
    if v["on"] and not (out["key"] and out["token"]):
        raise ValueError("Informe a API key e o token para ligar o Trello")
    out["scope"] = clean_scope(v.get("scope"), cur.get("scope"))
    out["todo"] = clean_todo(v.get("todo"), cur.get("todo"))
    for k in ("running_scope", "approved_scope"):
        val = v.get(k, cur.get(k, "all"))
        if val not in STAGE_SCOPES:
            raise ValueError("Trello: opção inválida")
        out[k] = val
    out["running"] = _names(v.get("running", cur.get("running")), "Em execução", DEFAULT_RUNNING)
    out["approved"] = _names(v.get("approved", cur.get("approved")), "Aprovar", DEFAULT_APPROVED)
    if not out["running"] and not out["approved"]:
        raise ValueError("Trello: marque ao menos uma lista de Em execução ou de Aprovar")
    if {norm(x) for x in out["running"]} & {norm(x) for x in out["approved"]}:
        raise ValueError("Trello: a mesma lista não pode ser Em execução e Aprovar")
    return out


def _atomic(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def in_scope(board, scope):
    """board = {name, ws}. Reavaliado a cada leitura pelo nome ATUAL: quadro mensal novo entra sozinho."""
    ws = norm(scope.get("workspace"))
    if ws and norm(board.get("ws")) != ws:
        return False
    m = scope.get("mode")
    if m == "prefix":
        n = norm(board.get("name"))
        return any(n.startswith(norm(p)) for p in scope.get("prefixes", []))
    if m == "pick":
        return board.get("id") in scope.get("ids", [])
    return True


class Trello:
    def __init__(self, base: Path, get_cfg, fetch=http_get, clock=time.time):
        self.path, self.done_path = Path(base) / "trello.json", Path(base) / "trello-done.json"
        self.get_cfg, self.fetch, self.clock = get_cfg, fetch, clock
        self.cards, self.boards, self.lists = [], {}, {}
        self.at = self.lists_at = self.try_at = 0.0
        self.blocked = False          # 401/403: para de tentar até salvar a credencial de novo
        self.error = None             # "net" na última tentativa
        self.user = None
        self.notified = ""
        self.ignored, self.found = 0, dict(NOFOUND)
        self.todo_day = None          # dia (do Brasil) em que "A fazer" foi calculado: virou a meia-noite = relê
        self.lists_used = {"todo": [], "running": [], "approved": []}   # só para o painel: quais listas valem AGORA
        self.done, self.baseline, self.baseline_all = {}, set(), set()   # baseline_all: quadros já lidos com "Todos" em Aprovar (os sem responsável que já estavam lá = anterior)
        self._load()

    # -- disco ----------------------------------------------------------------
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None
            if isinstance(d, dict):
                self.notified, self.blocked = str(d.get("notified") or ""), bool(d.get("blocked"))
                if d.get("v") == 2:    # v1 (todos os cards de todas as listas) é descartado: a próxima leitura reconstrói
                    self.cards = [c for c in d.get("cards", []) if isinstance(c, dict) and SHORT_RX.match(str(c.get("short", ""))) and ID_RX.match(str(c.get("id", "")))]
                    self.boards = d.get("boards") if isinstance(d.get("boards"), dict) else {}
                    self.lists = d.get("lists") if isinstance(d.get("lists"), dict) else {}
                    self.at, self.lists_at = float(d.get("at") or 0), float(d.get("lists_at") or 0)
                    self.ignored = int(d.get("ignored") or 0)
                    self.found = {**NOFOUND, **d["found"]} if isinstance(d.get("found"), dict) else self.found
        except (OSError, ValueError, TypeError):
            log.warning("trello.json inválido: ignorado")
        try:
            d = json.loads(self.done_path.read_text(encoding="utf-8")) if self.done_path.exists() else None
            if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("done"), dict):
                self.done = {k: v for k, v in d["done"].items() if ID_RX.match(str(k)) and isinstance(v, dict)}
                self.baseline = {str(x) for x in d.get("baseline", [])}
                self.baseline_all = {str(x) for x in d.get("baseline_all", [])}
        except (OSError, ValueError, TypeError):
            log.warning("trello-done.json inválido: ignorado")

    def _save(self):
        try:
            _atomic(self.path, {"v": 2, "cards": self.cards, "boards": self.boards, "lists": self.lists, "at": self.at, "lists_at": self.lists_at,
                                "blocked": self.blocked, "notified": self.notified, "ignored": self.ignored, "found": self.found})
        except OSError as e:
            log.warning("trello.json: %s", type(e).__name__)

    def _save_done(self):
        try:
            _atomic(self.done_path, {"v": 1, "done": self.done, "baseline": sorted(self.baseline), "baseline_all": sorted(self.baseline_all)})
        except OSError as e:
            log.warning("trello-done.json: %s", type(e).__name__)

    # -- configuração ---------------------------------------------------------
    def _cred(self):
        c = self.get_cfg() or {}
        k, t = c.get("key"), c.get("token")
        return (k, t) if isinstance(k, str) and isinstance(t, str) and k and t else None

    def cfg(self):
        """Configuração efetiva, com os padrões para o que não foi salvo (formato antigo = todos os quadros, Em execução, Aprovar)."""
        c = self.get_cfg() or {}
        try:
            scope = clean_scope(c.get("scope"), {"mode": "all"}) if c.get("scope") else {"mode": "all", "prefixes": [], "ids": [], "workspace": ""}
            running, approved = _names(c.get("running"), "", DEFAULT_RUNNING), _names(c.get("approved"), "", DEFAULT_APPROVED)
            todo = clean_todo(c.get("todo"), {"mode": "today"})
        except ValueError:
            scope, running, approved = {"mode": "all", "prefixes": [], "ids": [], "workspace": ""}, list(DEFAULT_RUNNING), list(DEFAULT_APPROVED)
            todo = dict(TODO_DEFAULT)
        rs, ap = c.get("running_scope", "all"), c.get("approved_scope", "all")
        return {"scope": scope, "running": running, "approved": approved, "todo": todo,
                "running_scope": rs if rs in STAGE_SCOPES else "all", "approved_scope": ap if ap in STAGE_SCOPES else "all"}

    def enabled(self):
        c = self.get_cfg() or {}
        return bool(c.get("on")) and self._cred() is not None

    def reset(self):
        """Credencial regravada: tenta de novo e não guarda dados da conta anterior (o histórico de Aprovar fica)."""
        self.blocked, self.error, self.try_at, self.user = False, None, 0.0, None
        self.cards, self.boards, self.lists, self.at, self.lists_at = [], {}, {}, 0.0, 0.0
        self.ignored, self.found = 0, dict(NOFOUND)
        self._save()

    def invalidate(self):
        """Filtro ou listas mudaram: relê na próxima volta (sem mexer na credencial nem no histórico)."""
        self.try_at, self.cards, self.at = 0.0, [], 0.0
        self.ignored, self.found = 0, dict(NOFOUND)
        self._save()

    def status(self):
        c = self.get_cfg() or {}
        cred = self._cred()
        state = "off" if not c.get("on") or not cred else "bad_cred" if self.blocked else "net_error" if self.error else "ok" if self.at else "waiting"
        return {"on": bool(c.get("on")), "has_key": bool(c.get("key")), "has_secret": bool(c.get("token")),
                "state": state, "at": self.at or None, "user": self.user}

    def cfg_view(self):
        """Para o painel: filtro e listas (nunca credencial) + o que a última leitura achou."""
        today = self.today()
        cards = [c for c in self.cards if c.get("cat") in ("todo", "running")]
        counts = {"todo": sum(c["cat"] == "todo" for c in cards), "running": sum(c["cat"] == "running" for c in cards),
                  "done": self.done_counts(today)["today"]}
        return {**self.cfg(), "found": dict(self.found), "ignored": self.ignored, "lists": {k: list(v) for k, v in self.lists_used.items()},
                "counts": counts, "day": today.day}

    # -- leitura --------------------------------------------------------------
    def due(self, has_clients):
        if not has_clients or not self.enabled() or self.blocked:
            return False
        gap = self.clock() - self.try_at
        if self.todo_day and self.todo_day != self.today() and self.cfg()["todo"]["mode"] == "today":
            return gap >= MANUAL_MIN_S                                  # virou o dia: a lista de hoje é outra, relê sem esperar 5 min
        return gap >= (MANUAL_MIN_S if self.error else REFRESH_S)   # depois de erro de rede, tenta em 1 min

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

    def _boards_lists(self, key, token, only=None):
        """{board_id: {name, ws, lists:{list_id: name}}} dos quadros abertos. Listas embutidas na mesma chamada; quadro que vier sem
        o campo `lists` (resposta fora do formato) é buscado por /boards/<id>/lists. only = ids que precisam de listas."""
        bs = self.fetch("members/me/boards", {"filter": "open", "fields": "name", "lists": "open", "list_fields": "name,pos",
                                              "organization": "true", "organization_fields": "displayName"}, key, token)
        if not isinstance(bs, list):
            raise NetError("resposta")
        out = {}
        for b in bs:
            if not isinstance(b, dict) or not ID_RX.match(str(b.get("id", ""))):
                continue
            org = b.get("organization") if isinstance(b.get("organization"), dict) else {}
            ls = b.get("lists")
            if not isinstance(ls, list) and (only is None or b["id"] in only):
                ls = self.fetch(f"boards/{b['id']}/lists", {"fields": "name,pos", "filter": "open"}, key, token)
            out[b["id"]] = {"name": str(b.get("name", ""))[:120], "ws": str(org.get("displayName") or "")[:120],
                            "lists": {li["id"]: {"n": str(li.get("name", ""))[:120], "p": li["pos"] if isinstance(li.get("pos"), (int, float)) and not isinstance(li.get("pos"), bool) else 0}
                                      for li in (ls if isinstance(ls, list) else []) if isinstance(li, dict) and ID_RX.match(str(li.get("id", "")))}}
        return out

    def boards_info(self, key=None, token=None, scope=None):
        """Para o painel escolher quadros e listas. Sem key/token usa os salvos; scope (do formulário, ainda não salvo) marca quem entra."""
        cred = (key, token) if key and token else self._cred()
        if not cred:
            raise CredError("sem credencial")
        sc = clean_scope(scope, {"mode": "all"}) if scope else self.cfg()["scope"]
        meta = self._boards_lists(cred[0], cred[1])
        boards, names = [], {}
        for bid, b in sorted(meta.items(), key=lambda kv: kv[1]["name"].casefold()):
            ok = in_scope({**b, "id": bid}, sc)
            ordered = sorted(b["lists"].items(), key=lambda kv: (kv[1]["p"], kv[1]["n"].casefold()))
            boards.append({"id": bid, "name": b["name"], "ws": b["ws"], "in": ok, "lists": sorted({v["n"] for v in b["lists"].values()}, key=str.casefold),
                           "ordered": [{"id": k, "name": v["n"], "n": i + 1} for i, (k, v) in enumerate(ordered)]})
            if ok:
                for n in {v["n"] for v in b["lists"].values()}:
                    k = norm(n)
                    names[k] = {"name": names[k]["name"] if k in names else n, "boards": (names[k]["boards"] if k in names else 0) + 1}
        return {"boards": boards, "names": sorted(names.values(), key=lambda x: x["name"].casefold()),
                "workspaces": sorted({b["ws"] for b in meta.values() if b["ws"]}, key=str.casefold)}

    def refresh(self):
        """Busca meus cards, classifica pelas listas escolhidas e atualiza o histórico de Aprovar. True se mudou algo. Nunca levanta."""
        cred = self._cred()
        if not cred or self.blocked:
            return False
        key, token = cred
        self.try_at = self.clock()
        try:
            raw = self.fetch("members/me/cards", {"filter": "open", "fields": CARD_FIELDS}, key, token)
            if not isinstance(raw, list):
                raise NetError("resposta")
            raw = [c for c in raw if isinstance(c, dict)]
            now = self.clock()
            meta = self._boards_lists(key, token)         # a lista do dia muda de nome e de lugar: lê quadros e listas a cada ciclo
            self.boards = {bid: {"name": b["name"], "ws": b["ws"]} for bid, b in meta.items()}
            self.lists = {lid: {"name": li["n"], "board": bid, "pos": li["p"]} for bid, b in meta.items() for lid, li in b["lists"].items()}
            self.lists_at = now
            cfg = self.cfg()
            run, apr = {norm(x) for x in cfg["running"]}, {norm(x) for x in cfg["approved"]}
            scoped = {bid for bid, b in self.boards.items() if in_scope({**b, "id": bid}, cfg["scope"])}
            found = {"boards": len(scoped), "running": 0, "approved": 0, "todo": "off", "todo_clash": 0}
            for li in self.lists.values():
                if li["board"] in scoped:
                    found["running"] += norm(li["name"]) in run
                    found["approved"] += norm(li["name"]) in apr
            today = datetime.fromtimestamp(now, TZ).date()
            todo_ids = self._todo_ids(cfg["todo"], scoped, today.day)
            if cfg["todo"]["mode"] != "off":
                found["todo"] = "ok" if todo_ids else "missing"       # sem lista de hoje / lista escolhida sumiu / posição que nenhum quadro tem: nunca adivinha
                found["todo_clash"] = sum(1 for i in todo_ids if norm(self.lists[i]["name"]) in run | apr)
            cards, ignored = [], 0

            def mk(c, cat, lname, nobody=False):
                return {"id": c["id"], "short": c["shortLink"], "name": str(c.get("name", ""))[:200], "board_id": c["idBoard"],
                        "board": self.boards[c["idBoard"]]["name"], "list": lname, "cat": cat, "nobody": nobody,
                        "due": c["due"] if isinstance(c.get("due"), str) else None,
                        "labels": [{"n": str(lb.get("name", ""))[:40], "c": str(lb.get("color") or "")[:16]}
                                   for lb in (c.get("labels") or []) if isinstance(lb, dict)][:8],
                        "act": c.get("dateLastActivity") if isinstance(c.get("dateLastActivity"), str) else None}

            def valid(c):
                return SHORT_RX.match(str(c.get("shortLink", ""))) and ID_RX.match(str(c.get("id", ""))) and c.get("idBoard") in scoped
            for c in raw:
                if c.get("idBoard") not in scoped:
                    continue
                lname = (self.lists.get(c.get("idList")) or {}).get("name", "")
                cat = "running" if norm(lname) in run else "approved" if norm(lname) in apr else "todo" if c.get("idList") in todo_ids else None
                if cat is None:
                    ignored += 1
                    continue
                if not valid(c):
                    continue
                cards.append(mk(c, cat, lname))
            mine = {c["id"] for c in cards}
            found["todo_hidden"] = found["running_hidden"] = found["approved_hidden"] = 0
            all_stage = {"todo": cfg["todo"]["scope"] == "all", "running": cfg["running_scope"] == "all", "approved": cfg["approved_scope"] == "all"}
            stage_lists = {"todo": todo_ids,
                           "running": {i for i, li in self.lists.items() if li["board"] in scoped and norm(li["name"]) in run},
                           "approved": {i for i, li in self.lists.items() if li["board"] in scoped and norm(li["name"]) in apr}}
            # "Todos": além dos meus (members/me/cards), os cards abertos das listas da etapa SEM responsável. Card de OUTRA pessoa (tem membro e não
            # sou eu) nunca entra, não aparece nem conta. Fora do padrão CLIENTE - JOB (separadores) também não, exceto no A fazer se o painel liberar.
            for cat in ("running", "approved", "todo"):
                if not all_stage[cat]:
                    continue
                job_only = cfg["todo"]["job_only"] if cat == "todo" else True
                for lid in sorted(stage_lists[cat]):
                    extra = self.fetch(f"lists/{lid}/cards", {"filter": "open", "fields": CARD_FIELDS + ",idMembers"}, key, token)
                    if not isinstance(extra, list):
                        raise NetError("resposta")
                    for c in extra:
                        if not isinstance(c, dict) or c.get("id") in mine or not valid(c) or c.get("idMembers"):
                            continue
                        if job_only and split_name(c.get("name"))[0] is None:
                            found[cat + "_hidden"] += 1
                            continue
                        mine.add(c["id"])
                        cards.append(mk(c, cat, self.lists[lid]["name"], nobody=True))
            self.cards, self.at, self.error, self.ignored, self.found = cards, now, None, ignored, found
            self.todo_day = today
            self.lists_used = {k: self._used(scoped, pick) for k, pick in
                               (("todo", lambda i, n: i in todo_ids), ("running", lambda i, n: norm(n) in run), ("approved", lambda i, n: norm(n) in apr))}
            self._track_done(key, token, cards, scoped, apr, all_stage["approved"])
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

    def _used(self, scoped, pick):
        """[{list, board}] das listas dos quadros do filtro que `pick(id, nome)` aceita (só para o painel mostrar o que vale agora)."""
        out = [{"list": li["name"], "board": self.boards[li["board"]]["name"]} for i, li in self.lists.items() if li["board"] in scoped and pick(i, li["name"])]
        return sorted(out, key=lambda x: (x["board"].casefold(), x["list"].casefold()))[:10]

    def _todo_ids(self, todo, scoped, day=None):
        """Ids das listas de "A fazer": today = a lista "DIA <dia de hoje>" de cada quadro do filtro; list = a escolhida (se ainda existe num
        quadro do filtro); position = a N-ésima lista aberta de cada quadro do filtro."""
        if todo["mode"] == "today":
            rx = today_list_rx(day if day is not None else self.today().day)
            return {i for i, li in self.lists.items() if li["board"] in scoped and rx.match(norm(li["name"]))}
        if todo["mode"] == "list":
            li = self.lists.get(todo["list_id"])
            return {todo["list_id"]} if li and li["board"] in scoped else set()
        if todo["mode"] == "position":
            ids = set()
            for bid in scoped:
                ls = sorted(((i, li) for i, li in self.lists.items() if li["board"] == bid), key=lambda kv: (kv[1].get("pos", 0), kv[1]["name"].casefold()))
                if len(ls) >= todo["position"]:
                    ids.add(ls[todo["position"] - 1][0])
            return ids
        return set()

    # -- feito = card em Aprovar --------------------------------------------------
    def _activity(self, key, token, board_id, ids, apr):
        """{card_id: AAAA-MM-DD} da última vez que o card entrou numa lista de Aprovar (updateCard:idList), só no formato esperado."""
        frm = (datetime.fromtimestamp(self.clock(), timezone.utc) - timedelta(days=ACTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        found, before = {}, None
        for _ in range(ACTION_PAGES):
            params = {"filter": "updateCard:idList", "since": frm, "limit": "1000", "fields": "date,data,type"}
            if before:
                params["before"] = before
            acts = self.fetch(f"boards/{board_id}/actions", params, key, token)
            if not isinstance(acts, list):
                raise NetError("resposta")
            for a in acts:
                try:
                    if a["type"] != "updateCard":
                        continue
                    cid = a["data"]["card"]["id"]
                    if cid not in ids or norm(a["data"]["listAfter"]["name"]) not in apr:
                        continue
                    dt = datetime.fromisoformat(a["date"].replace("Z", "+00:00")).astimezone(TZ)
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
                day = dt.date().isoformat()
                if cid not in found or day > found[cid]:
                    found[cid] = day
            if len(acts) < 1000:
                break
            try:
                before = min(a["date"] for a in acts if isinstance(a, dict) and isinstance(a.get("date"), str))
            except ValueError:
                break
        return found

    def _track_done(self, key, token, cards, scoped, apr, all_approved=False):
        changed, need, today = False, {}, self.today()
        for c in cards:      # voltou para A fazer / Em execução: deixa de contar (continua no histórico; se entrar de novo em Aprovar, conta na nova data)
            cur = self.done.get(c["id"])
            if cur is not None and c["cat"] in ("todo", "running") and not cur.get("open"):
                cur["open"] = True
                changed = True
        if not all_approved and self.baseline_all:
            self.baseline_all = set()       # voltou para "Só os meus": ao ligar "Todos" de novo, a base dos sem responsável é refeita
            changed = True
        for bid in sorted(scoped):
            first = bid not in self.baseline
            first_all = all_approved and bid not in self.baseline_all
            for c in (x for x in cards if x["board_id"] == bid and x["cat"] == "approved"):
                cur = self.done.get(c["id"])
                if cur is None:
                    prior = first_all if c.get("nobody") else first      # sem responsável que já estava em Aprovar quando "Todos" foi ligado = anterior
                    self.done[c["id"]] = {"name": c["name"], "board": c["board"], "board_id": bid, "short": c["short"],
                                          "src": "prior" if prior else "seen", "day": None if prior else today.isoformat()}
                    if not (prior and c.get("nobody")):      # só os sem responsável "anteriores" nunca consultam as actions (nunca contam); os meus seguem como sempre
                        need.setdefault(bid, set()).add(c["id"])
                    changed = True
                elif cur.get("open"):      # reentrou em Aprovar depois de reaberto: um registro só por card, com a data nova
                    cur.update({"open": False, "src": "seen", "day": today.isoformat(), "name": c["name"], "short": c["short"]})
                    need.setdefault(bid, set()).add(c["id"])
                    changed = True
                elif cur.get("name") != c["name"] or cur.get("short") != c["short"]:
                    cur["name"], cur["short"] = c["name"], c["short"]
                    changed = True
            if first:
                self.baseline.add(bid)
                changed = True
            if all_approved and bid not in self.baseline_all:
                self.baseline_all.add(bid)
                changed = True
        for bid, ids in need.items():
            try:
                found = self._activity(key, token, bid, ids, apr)
            except (CredError, NetError, KeyError, TypeError):
                continue          # sem data real: fica "visto em"/"anterior"
            for cid, day in found.items():
                self.done[cid].update({"src": "entered", "day": day})
        if changed:
            self._save_done()

    def today(self):
        return datetime.fromtimestamp(self.clock(), TZ).date()

    @staticmethod
    def _dm(day):
        return f"{day[8:10]}/{day[5:7]}"

    def _counted(self):
        return [(v["day"], k, v) for k, v in self.done.items() if v.get("src") in ("entered", "seen") and v.get("day") and not v.get("open")]

    def done_counts(self, today=None):
        today = today or self.today()
        mon = today - timedelta(days=today.weekday())
        c = {"today": 0, "week": 0, "month": 0}
        for day, _k, _v in self._counted():
            d = date.fromisoformat(day)
            c["today"] += d == today
            c["week"] += mon <= d <= today
            c["month"] += d.year == today.year and d.month == today.month
        return c

    def done_by_day(self, days=62, today=None):
        today = today or self.today()
        lo, out = (today - timedelta(days=days)).isoformat(), {}
        for day, _k, _v in self._counted():
            if day >= lo:
                out[day] = out.get(day, 0) + 1
        return out

    def done_range(self, first, last):
        """{dia: nº de cards contados como feitos} entre duas datas (AAAA-MM-DD, inclusive), do histórico completo (retrospectiva)."""
        out = {}
        for day, _k, _v in self._counted():
            if first <= day <= last:
                out[day] = out.get(day, 0) + 1
        return out

    def done_names(self, first, last):
        """Nomes dos cards contados como feitos entre duas datas (AAAA-MM-DD, inclusive): vem do histórico completo, sem o limite de 62 dias do celular."""
        return [v.get("name", "") for day, _k, v in self._counted() if first <= day <= last]

    def done_items(self, first, last):
        """[(dia, nome)] dos feitos entre duas datas (para o Trabalho por dia)."""
        return [(day, v.get("name", "")) for day, _k, v in self._counted() if first <= day <= last]

    def running_names(self):
        """Nomes dos meus cards em Em execução (última leitura)."""
        return [c["name"] for c in self.cards if c.get("cat") == "running"]

    def done_list(self, limit=8):
        rows = sorted(self._counted(), key=lambda r: (r[0], r[1]), reverse=True)
        return [{"id": k, "name": v.get("name", ""), "board": v.get("board", ""), "day": d,
                 "label": ("entrou em Aprovar em " if v["src"] == "entered" else "visto em ") + self._dm(d)} for d, k, v in rows[:limit]]

    # -- estado para o celular e o painel ---------------------------------------
    def state(self, times=None, links=None):
        """Estado do celular: só os cards de "Em execução" por prazo + a contagem de Aprovar. Nunca leva credencial."""
        today = self.today()
        groups, todo = {g: [] for g in GROUPS}, []
        for c in self.cards:
            if c.get("cat") not in ("running", "todo"):
                continue
            dt = due_local(c["due"])
            item = {"short": c["short"], "name": c["name"], "board": c["board"], "list": c["list"], "labels": c["labels"],
                    "due": dt.date().isoformat() if dt else None, "time": dt.strftime("%H:%M") if dt else None}
            if links and c["short"] in links:
                item["link"] = links[c["short"]]       # {client, job} da ligação feita no painel (o celular mostra o card ao lado do programa em foco)
            if c.get("nobody"):
                item["nobody"] = True      # card da lista do dia que ninguém assumiu
            if times and c["short"] in times:
                item["spent"] = times[c["short"]]
            if c["cat"] == "todo":
                item["late"] = group_of(c["due"], today) == "overdue"
                todo.append(item)
            else:
                groups[group_of(c["due"], today)].append(item)
        todo.sort(key=lambda x: (x["due"] or "9999", x["time"] or "", x["name"].casefold()))
        for g in GROUPS:
            groups[g].sort(key=lambda x: (x["due"] or "9999", x["time"] or "", x["name"].casefold()))
        st = self.status()
        prior = sum(1 for v in self.done.values() if v.get("src") == "prior")
        return {"state": st["state"], "on": st["on"], "at": st["at"], "hhmm": datetime.fromtimestamp(self.at, TZ).strftime("%H:%M") if self.at else None,
                "today": today.isoformat(), "groups": groups, "counts": {g: len(groups[g]) for g in GROUPS},
                "ignored": self.ignored, "found": dict(self.found),
                "todo": {"state": self.found.get("todo", "off"), "items": todo, "count": len(todo),
                         "day": today.day if self.cfg()["todo"]["mode"] == "today" else None},
                "done": {**self.done_counts(today), "recent": self.done_list(), "by_day": self.done_by_day(today=today), "prior": prior,
                         "today_ids": [k for d, k, _v in self._counted() if d == today.isoformat()][:100]}}

    def all_cards(self):
        """Para o painel (busca ao ligar um card a um job): meus cards de Em execução e de Aprovar nos quadros do filtro."""
        return [{"short": c["short"], "name": c["name"], "board": c["board"], "list": c["list"], "cat": c["cat"]} for c in self.cards]

    def has_card(self, short):
        return isinstance(short, str) and bool(SHORT_RX.match(short)) and any(c["short"] == short for c in self.cards)

    def card_name(self, short):
        return next((c["name"] for c in self.cards if c["short"] == short), "")

    # -- aviso das 9h ---------------------------------------------------------
    def digest(self):
        """Uma vez por dia, a partir das 9h (horário do Brasil): quantos "A fazer" e quantos "Em execução". None se nada a avisar."""
        now = datetime.fromtimestamp(self.clock(), TZ)
        day = now.date().isoformat()
        if now.hour < 9 or self.notified == day or not self.at or self.blocked:
            return None
        self.notified = day
        self._save()
        st = self.state()
        n_f, n_e, n_o = st["todo"]["count"], sum(st["counts"].values()), st["counts"]["overdue"]
        if not n_f and not n_e:
            return None
        parts = ([f"{n_f} a fazer"] if n_f else []) + ([f"{n_e} em execução"] if n_e else [])
        late = [c["name"] for c in st["groups"]["overdue"][:3]]
        return {"title": "Trello: " + " · ".join(parts), "body": "; ".join(late), "todo": n_f, "running": n_e, "overdue": n_o}
