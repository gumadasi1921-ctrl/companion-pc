"""monday.com SOMENTE LEITURA: meus itens "em execução" e "aprovar" (feito) dos quadros escolhidos no painel.

- Só queries GraphQL (check_query recusa mutation/subscription; há teste que varre o módulo). Só https://api.monday.com/v2.
- Token pessoal no config.json do PC, no cabeçalho Authorization, nunca na URL; nada dele (nem nomes de itens/quadros)
  vai para o log, e as mensagens de erro são fixas. O celular nunca recebe o token.
- Só itens atribuídos a mim (coluna de Pessoas = meu usuário), conferido de novo em Python.
- "Em execução"/"Aprovar" = valores marcados no painel, por quadro, vindos da coluna de Status ou do Grupo (igualdade, sem "parecido").
- Aprovar conta como feito. Data: quando entrou em Aprovar (activity_logs, só se vier no formato esperado) ("entrou em Aprovar em DD/MM");
  senão a primeira vez que o Companion viu ("visto em DD/MM"). Item já em Aprovar na primeira leitura do quadro e sem data real
  = "anterior ao Companion" (não conta). monday-done.json nunca é podado.
- Roda na fila de disco do servidor. Cache em monday.json (atômico).
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("companion.monday")

API = "https://api.monday.com/v2"
API_VERSION = "2025-04"
TIMEOUT_S = 15
REFRESH_S = 300
MANUAL_MIN_S = 60
BACKOFF_S = 300
META_TTL_S = 6 * 3600
MAX_BOARDS = 10
MAX_PAGES = 5
TOKEN_RX = re.compile(r"^[A-Za-z0-9._-]{20,400}$")
ID_RX = re.compile(r"^[0-9]{1,20}$")
COL_RX = re.compile(r"^[a-z0-9_]{1,60}$")
GROUPS = ("overdue", "today", "tomorrow", "week", "later", "none")
BAD_CODES = ("UserUnauthorizedException", "InvalidTokenException", "AuthorizationException")
RATE_CODES = ("ComplexityException", "RATE_LIMIT_EXCEEDED", "Rate Limit Exceeded", "maxConcurrencyExceeded")

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Sao_Paulo")
except Exception:  # noqa: BLE001
    TZ = timezone(timedelta(hours=-3))


class CredError(Exception):
    """401/403 ou token recusado."""


class NetError(Exception):
    """Rede, timeout, resposta inesperada."""


class RateError(NetError):
    """Limite de requisições/complexidade: espera mais."""


# -- só leitura ----------------------------------------------------------------------------
_WRITE_RX = re.compile(r"\b(mutation|subscription)\b", re.I)


def check_query(q):
    """Só aceita query. Levanta ValueError para qualquer outra coisa (mutation, subscription, texto estranho)."""
    if not isinstance(q, str) or not re.match(r"\s*query\b", q) or _WRITE_RX.search(q):
        raise ValueError("só queries são permitidas")
    return q


def http_post(query, variables, token):
    """Único acesso à rede deste módulo (POST do GraphQL; é leitura, a query é conferida antes)."""
    check_query(query)
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": token, "Content-Type": "application/json", "Accept": "application/json", "API-Version": API_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:   # noqa: S310  (host fixo, https)
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise (CredError if e.code in (401, 403) else RateError if e.code == 429 else NetError)(f"http {e.code}") from None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        raise NetError("rede") from None
    return parse_response(d)


def parse_response(d):
    if not isinstance(d, dict):
        raise NetError("resposta")
    errs = d.get("errors")
    if errs:
        txt = json.dumps(errs, ensure_ascii=False)
        if any(c in txt for c in BAD_CODES):
            raise CredError("token")
        if any(c in txt for c in RATE_CODES):
            raise RateError("limite")
        raise NetError("erro da API")
    if isinstance(d.get("error_code"), str) or not isinstance(d.get("data"), dict):
        raise (RateError if "Complexity" in str(d.get("error_code")) else NetError)("erro da API")
    return d["data"]


Q_ME = "query { me { id name } }"
Q_BOARDS = ("query { boards(limit: 50, state: active, order_by: used_at) { id name "
            "columns { id title type settings_str } groups { id title } } }")
Q_ACT = ("query($ids: [ID!], $from: ISO8601DateTime, $cols: [String], $page: Int) { boards(ids: $ids) { "
         "activity_logs(from: $from, column_ids: $cols, limit: 500, page: $page) { id event data created_at } } }")


def q_items_first(cols):
    c = ",".join(f'"{x}"' for x in cols)
    return ("query($ids: [ID!], $qp: ItemsQuery) { boards(ids: $ids) { id items_page(limit: 100, query_params: $qp) { cursor items { "
            f"id name url state group {{ id title }} column_values(ids: [{c}]) {{ id type text value }} }} }} }} }}")


def q_items_next(cols):
    c = ",".join(f'"{x}"' for x in cols)
    return ("query($c: String!) { next_items_page(limit: 100, cursor: $c) { cursor items { "
            f"id name url state group {{ id title }} column_values(ids: [{c}]) {{ id type text value }} }} }} }}")


# -- validação (config) -----------------------------------------------------------------------
def valid_token(v):
    if not isinstance(v, str) or not TOKEN_RX.match(v.strip()):
        raise ValueError("token inválido")
    return v.strip()


def _labels(v, what):
    if not isinstance(v, list) or len(v) > 20:
        raise ValueError(f"{what}: lista inválida")
    out = []
    for x in v:
        if not isinstance(x, str) or not x.strip() or len(x) > 80 or re.search(r"[\x00-\x1f]", x):
            raise ValueError(f"{what}: valor inválido")
        if x.strip() not in out:
            out.append(x.strip())
    return out


def clean_board(b):
    if not isinstance(b, dict) or set(b) - {"id", "name", "source", "status_col", "people_col", "date_col", "running", "approved"}:
        raise ValueError("quadro: formato inválido")
    if not isinstance(b.get("id"), str) or not ID_RX.match(b["id"]):
        raise ValueError("quadro: id inválido")
    src = b.get("source")
    if src not in ("status", "group"):
        raise ValueError("quadro: escolha Status ou Grupo")
    out = {"id": b["id"], "name": str(b.get("name") or "")[:120], "source": src}
    for k in ("status_col", "people_col", "date_col"):
        v = b.get(k) or ""
        if v and (not isinstance(v, str) or not COL_RX.match(v)):
            raise ValueError("quadro: coluna inválida")
        out[k] = v
    if not out["people_col"]:
        raise ValueError("quadro: escolha a coluna de Pessoas")
    if src == "status" and not out["status_col"]:
        raise ValueError("quadro: escolha a coluna de Status")
    out["running"], out["approved"] = _labels(b.get("running", []), "Em execução"), _labels(b.get("approved", []), "Aprovar")
    if not out["running"] and not out["approved"]:
        raise ValueError("quadro: marque ao menos um valor de Em execução ou de Aprovar")
    if {x.casefold() for x in out["running"]} & {x.casefold() for x in out["approved"]}:
        raise ValueError("quadro: o mesmo valor não pode ser Em execução e Aprovar")
    return out


def clean_cfg(v, cur):
    """config_save: token em branco = mantém o salvo; nunca volta ao painel."""
    if not isinstance(v, dict) or set(v) - {"token", "on", "boards"} or not isinstance(v.get("on"), bool):
        raise ValueError("monday: formato inválido")
    token = valid_token(v["token"]) if isinstance(v.get("token"), str) and v["token"].strip() else (cur or {}).get("token", "")
    bs = v.get("boards", (cur or {}).get("boards", []))
    if not isinstance(bs, list) or len(bs) > MAX_BOARDS:
        raise ValueError(f"monday: no máximo {MAX_BOARDS} quadros")
    boards = [clean_board(b) for b in bs]
    if len({b["id"] for b in boards}) != len(boards):
        raise ValueError("monday: quadro repetido")
    if v["on"] and not token:
        raise ValueError("Informe o token para ligar o monday")
    if v["on"] and not boards:
        raise ValueError("Escolha ao menos um quadro para ligar o monday")
    return {"token": token, "on": v["on"], "boards": boards}


# -- datas ----------------------------------------------------------------------------------
def safe_url(u):
    if not isinstance(u, str):
        return None
    m = re.match(r"^https://([a-z0-9.-]+)(/[^\s]*)?$", u.strip(), re.I)
    if m and (m.group(1).lower() == "monday.com" or m.group(1).lower().endswith(".monday.com")):
        return u.strip()
    return None


def parse_due(val):
    """Valor JSON da coluna Data -> (AAAA-MM-DD, HH:MM|None) no fuso do Brasil, ou (None, None). Sem prazo = nunca inventa."""
    try:
        d = json.loads(val) if isinstance(val, str) else None
        ds, ts = d.get("date"), d.get("time")
        day = date.fromisoformat(ds)
    except (ValueError, TypeError, AttributeError):
        return None, None
    if isinstance(ts, str) and ts:
        try:
            dt = datetime.combine(day, datetime.strptime(ts[:8], "%H:%M:%S").time(), tzinfo=timezone.utc).astimezone(TZ)
            return dt.date().isoformat(), dt.strftime("%H:%M")
        except ValueError:
            return day.isoformat(), None
    return day.isoformat(), None


def group_of(day, today):
    if not day:
        return "none"
    d = date.fromisoformat(day)
    if d < today:
        return "overdue"
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    if d <= today + timedelta(days=6 - today.weekday()):
        return "week"
    return "later"


def _eq(a, b):
    return isinstance(a, str) and a.strip().casefold() == b.strip().casefold()


def _in(label, lst):
    return any(_eq(label, x) for x in lst)


def _atomic(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class Monday:
    def __init__(self, base: Path, get_cfg, fetch=http_post, clock=time.time):
        self.path, self.done_path = Path(base) / "monday.json", Path(base) / "monday-done.json"
        self.get_cfg, self.fetch, self.clock = get_cfg, fetch, clock
        self.items, self.berr, self.meta = [], {}, {}
        self.at = self.try_at = self.meta_at = self.backoff_until = 0.0
        self.blocked, self.error, self.user, self.notified = False, None, None, ""
        self.done, self.baseline = {}, set()
        self._load()

    # -- disco ------------------------------------------------------------------------------
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None
            if isinstance(d, dict) and d.get("v") == 1:
                self.items = [i for i in d.get("items", []) if isinstance(i, dict) and ID_RX.match(str(i.get("id", "")))]
                self.berr = d.get("berr") if isinstance(d.get("berr"), dict) else {}
                self.at, self.blocked, self.notified = float(d.get("at") or 0), bool(d.get("blocked")), str(d.get("notified") or "")
                self.user = d.get("user") if isinstance(d.get("user"), dict) else None
        except (OSError, ValueError, TypeError):
            log.warning("monday.json inválido: ignorado")
        try:
            d = json.loads(self.done_path.read_text(encoding="utf-8")) if self.done_path.exists() else None
            if isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("done"), dict):
                self.done = {k: v for k, v in d["done"].items() if ID_RX.match(str(k)) and isinstance(v, dict)}
                self.baseline = {str(x) for x in d.get("baseline", [])}
        except (OSError, ValueError, TypeError):
            log.warning("monday-done.json inválido: ignorado")

    def _save(self):
        try:
            _atomic(self.path, {"v": 1, "items": self.items, "berr": self.berr, "at": self.at, "blocked": self.blocked,
                                "notified": self.notified, "user": self.user})
        except OSError as e:
            log.warning("monday.json: %s", type(e).__name__)

    def _save_done(self):
        try:
            _atomic(self.done_path, {"v": 1, "done": self.done, "baseline": sorted(self.baseline)})
        except OSError as e:
            log.warning("monday-done.json: %s", type(e).__name__)

    # -- configuração -----------------------------------------------------------------------
    def _cfg(self):
        c = self.get_cfg()
        return c if isinstance(c, dict) else {}

    def _token(self):
        t = self._cfg().get("token")
        return t if isinstance(t, str) and t else None

    def boards_cfg(self):
        return [b for b in self._cfg().get("boards", []) if isinstance(b, dict) and ID_RX.match(str(b.get("id", "")))]

    def enabled(self):
        return bool(self._cfg().get("on")) and self._token() is not None and bool(self.boards_cfg())

    def reset(self, keep_done=True):
        """Token ou quadros regravados: tenta de novo e esquece a leitura anterior (o histórico de Aprovar fica)."""
        self.blocked, self.error, self.try_at, self.backoff_until, self.user = False, None, 0.0, 0.0, None
        self.items, self.berr, self.meta, self.meta_at, self.at = [], {}, {}, 0.0, 0.0
        self._save()

    def status(self):
        c = self._cfg()
        on = bool(c.get("on")) and bool(c.get("token")) and bool(c.get("boards"))
        state = ("off" if not on else "bad_cred" if self.blocked else "net_error" if self.error else
                 "config" if self.berr else "ok" if self.at else "waiting")
        return {"on": bool(c.get("on")), "has_secret": bool(c.get("token")), "boards": len(c.get("boards") or []),
                "state": state, "at": self.at or None, "user": (self.user or {}).get("name")}

    # -- leitura ----------------------------------------------------------------------------
    def due(self, has_clients):
        if not has_clients or not self.enabled() or self.blocked or self.clock() < self.backoff_until:
            return False
        return self.clock() - self.try_at >= (MANUAL_MIN_S if self.error else REFRESH_S)

    def refresh_manual(self):
        if not self.enabled() or self.blocked or self.clock() - self.try_at < MANUAL_MIN_S or self.clock() < self.backoff_until:
            return False
        return self.refresh()

    def test(self, token):
        """Botão "Testar": só o nome. Levanta CredError/NetError."""
        me = self.fetch(Q_ME, {}, token).get("me")
        if not isinstance(me, dict) or not me.get("name"):
            raise NetError("resposta")
        return str(me["name"])[:80]

    def boards_info(self, token=None):
        """Para o painel escolher quadros/colunas/valores. Sem token passado usa o salvo."""
        token = token or self._token()
        if not token:
            raise CredError("sem token")
        raw = self.fetch(Q_BOARDS, {}, token).get("boards")
        if not isinstance(raw, list):
            raise NetError("resposta")
        out = []
        for b in raw:
            if not isinstance(b, dict) or not ID_RX.match(str(b.get("id", ""))):
                continue
            cols = []
            for c in b.get("columns") or []:
                if not isinstance(c, dict) or c.get("type") not in ("status", "people", "date") or not COL_RX.match(str(c.get("id", ""))):
                    continue
                labels = []
                if c["type"] == "status":
                    try:
                        ls = json.loads(c.get("settings_str") or "{}").get("labels") or {}
                        labels = [str(v) for v in (ls.values() if isinstance(ls, dict) else ls) if isinstance(v, str) and v][:40]
                    except (ValueError, AttributeError):
                        labels = []
                cols.append({"id": c["id"], "title": str(c.get("title", ""))[:80], "type": c["type"], "labels": labels})
            out.append({"id": str(b["id"]), "name": str(b.get("name", ""))[:120], "columns": cols,
                        "groups": [{"id": str(g.get("id")), "title": str(g.get("title", ""))[:80]} for g in b.get("groups") or [] if isinstance(g, dict) and g.get("id")]})
        return out

    def _me(self, token):
        if not self.user:
            me = self.fetch(Q_ME, {}, token).get("me")
            if not isinstance(me, dict) or not ID_RX.match(str(me.get("id", ""))):
                raise NetError("resposta")
            self.user = {"id": str(me["id"]), "name": str(me.get("name", ""))[:80]}
        return self.user["id"]

    def _board_items(self, token, cfg, uid):
        cols = [c for c in (cfg.get("status_col") if cfg["source"] == "status" else "", cfg["people_col"], cfg.get("date_col")) if c]
        qp = {"rules": [{"column_id": cfg["people_col"], "compare_value": [f"person-{uid}"], "operator": "any_of"}]}
        d = self.fetch(q_items_first(cols), {"ids": [cfg["id"]], "qp": qp}, token)
        bl = d.get("boards")
        if not isinstance(bl, list) or not bl or not isinstance(bl[0], dict) or not isinstance(bl[0].get("items_page"), dict):
            raise KeyError("board")
        page, raw = bl[0]["items_page"], []
        for _ in range(MAX_PAGES):
            raw += [i for i in page.get("items") or [] if isinstance(i, dict)]
            cur = page.get("cursor")
            if not cur:
                break
            page = (self.fetch(q_items_next(cols), {"c": cur}, token).get("next_items_page")) or {}
        return raw

    @staticmethod
    def _mine(cv, uid):
        try:
            v = json.loads(cv.get("value") or "null")
            return any(isinstance(p, dict) and p.get("kind") == "person" and str(p.get("id")) == uid for p in (v or {}).get("personsAndTeams", []))
        except (ValueError, AttributeError, TypeError):
            return False

    def _classify(self, cfg, raw, uid, bname):
        """Itens meus que casam com Em execução / Aprovar. Coluna que não existe no item = configuração inválida (KeyError)."""
        out = []
        for it in raw:
            if str(it.get("state", "active")) != "active" or not ID_RX.match(str(it.get("id", ""))):
                continue
            cvs = {c.get("id"): c for c in it.get("column_values") or [] if isinstance(c, dict)}
            if cfg["people_col"] not in cvs:
                raise KeyError("people")
            if not self._mine(cvs[cfg["people_col"]], uid):
                continue
            grp = it.get("group") if isinstance(it.get("group"), dict) else {}
            if cfg["source"] == "status":
                if cfg["status_col"] not in cvs:
                    raise KeyError("status")
                label = cvs[cfg["status_col"]].get("text")
            else:
                label = grp.get("title")
            cat = "running" if _in(label, cfg["running"]) else "approved" if _in(label, cfg["approved"]) else None
            if not cat:
                continue
            day, hhmm = parse_due((cvs.get(cfg.get("date_col")) or {}).get("value")) if cfg.get("date_col") else (None, None)
            out.append({"id": str(it["id"]), "name": str(it.get("name", ""))[:200], "board": bname, "board_id": cfg["id"],
                        "group": str(grp.get("title", ""))[:80], "status": str(label or "")[:80], "cat": cat,
                        "due": day, "time": hhmm, "url": safe_url(it.get("url"))})
        return out

    # -- data do "Aprovar" ------------------------------------------------------------------
    def _activity(self, token, cfg, approved_ids):
        """{item_id: AAAA-MM-DD} só do que a API devolver no formato esperado; qualquer outra coisa = ignorado."""
        group_ids = set()
        if cfg["source"] == "group":
            m = self.meta.get(cfg["id"]) or {}
            group_ids = {g for g, t in (m.get("groups") or {}).items() if _in(t, cfg["approved"])}
        frm = (datetime.fromtimestamp(self.clock(), timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        found = {}
        for page in range(1, 4):
            d = self.fetch(Q_ACT, {"ids": [cfg["id"]], "from": frm, "cols": [cfg["status_col"]] if cfg["source"] == "status" else None,
                                   "page": page}, token)
            bl = d.get("boards")
            logs = (bl[0].get("activity_logs") if isinstance(bl, list) and bl and isinstance(bl[0], dict) else None) or []
            for ev in logs:
                try:
                    data = json.loads(ev["data"]) if isinstance(ev.get("data"), str) else ev.get("data")
                    pid = str(data["pulse_id"])
                    if pid not in approved_ids:
                        continue
                    if cfg["source"] == "status":
                        if ev.get("event") != "update_column_value" or data.get("column_id") != cfg["status_col"]:
                            continue
                        if not _in(data["value"]["label"]["text"], cfg["approved"]):
                            continue
                    else:
                        if ev.get("event") != "move_pulse_into_group" or str(data.get("dest_group_id")) not in group_ids:
                            continue
                    ts = int(ev["created_at"]) / 1e7
                    day = datetime.fromtimestamp(ts, TZ).date().isoformat()
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError, OSError):
                    continue
                if pid not in found or day > found[pid]:
                    found[pid] = day
            if len(logs) < 500:
                break
        return found

    def _track_done(self, token, cfgs, now_items, today, checked):
        """Atualiza monday-done.json com os itens em Aprovar lidos agora. Só os quadros lidos sem erro (checked)."""
        changed, need = False, {}
        for cfg in cfgs:
            if cfg["id"] not in checked:
                continue
            first = cfg["id"] not in self.baseline
            for it in (i for i in now_items if i["board_id"] == cfg["id"] and i["cat"] == "approved"):
                cur = self.done.get(it["id"])
                if cur is None:
                    self.done[it["id"]] = {"name": it["name"], "board": it["board"], "board_id": cfg["id"],
                                           "src": "prior" if first else "seen", "day": None if first else today.isoformat()}
                    need.setdefault(cfg["id"], set()).add(it["id"])
                    changed = True
                elif cur.get("name") != it["name"]:
                    cur["name"] = it["name"]
                    changed = True
            if first:
                self.baseline.add(cfg["id"])
                changed = True
        for cfg in cfgs:
            ids = need.get(cfg["id"])
            if not ids or (cfg["source"] == "status" and not cfg.get("status_col")):
                continue
            try:
                found = self._activity(token, cfg, ids)
            except (CredError, NetError, KeyError, TypeError):
                continue          # sem data real: fica "visto em"/"anterior"
            for pid, day in found.items():
                self.done[pid].update({"src": "entered", "day": day})
        if changed or any(need.values()):
            self._save_done()

    def refresh(self):
        """Busca meus itens dos quadros escolhidos. True se mudou algo. Nunca levanta."""
        token, cfgs = self._token(), self.boards_cfg()
        if not token or not cfgs or self.blocked:
            return False
        self.try_at = self.clock()
        try:
            uid = self._me(token)
            items, berr, checked, meta = [], {}, set(), {}
            names = self._board_names(token, cfgs)
            for cfg in cfgs:
                try:
                    raw = self._board_items(token, cfg, uid)
                    items += self._classify(cfg, raw, uid, names.get(cfg["id"]) or cfg.get("name", ""))
                    checked.add(cfg["id"])
                except KeyError:
                    berr[cfg["id"]] = "config"     # quadro/coluna que não existe mais: não mostra nada dele
            self.items, self.berr, self.at, self.error = items, berr, self.clock(), None
            self._track_done(token, cfgs, items, self.today(), checked)
            self._save()
            return True
        except CredError:
            self.blocked, self.error = True, None
            self._save()
            log.warning("monday: token recusado")
            return True
        except RateError:
            self.error, self.backoff_until = "net", self.clock() + BACKOFF_S
            log.warning("monday: limite de requisições")
            return True
        except NetError:
            self.error = "net"          # mantém a última leitura
            return True
        except Exception as e:  # noqa: BLE001
            self.error = "net"
            log.warning("monday: %s", type(e).__name__)
            return True

    def _board_names(self, token, cfgs):
        """Nomes dos quadros e grupos (cache 6 h): grupos só servem para achar o id do grupo "Aprovar" no registro de atividade."""
        if self.clock() - self.meta_at > META_TTL_S or any(c["id"] not in self.meta for c in cfgs):
            raw = self.fetch(Q_BOARDS, {}, token).get("boards")
            if not isinstance(raw, list):
                raise NetError("resposta")
            self.meta = {str(b["id"]): {"name": str(b.get("name", ""))[:120],
                                        "groups": {str(g["id"]): str(g.get("title", "")) for g in b.get("groups") or [] if isinstance(g, dict) and g.get("id")}}
                         for b in raw if isinstance(b, dict) and b.get("id")}
            self.meta_at = self.clock()
        return {k: v["name"] for k, v in self.meta.items()}

    # -- estado para o celular e o painel -----------------------------------------------------
    def today(self):
        return datetime.fromtimestamp(self.clock(), TZ).date()

    @staticmethod
    def _dm(day):
        return f"{day[8:10]}/{day[5:7]}"

    def done_counts(self, today=None):
        today = today or self.today()
        mon = today - timedelta(days=today.weekday())
        c = {"today": 0, "week": 0, "month": 0}
        for v in self.done.values():
            if v.get("src") not in ("entered", "seen") or not v.get("day"):
                continue
            d = date.fromisoformat(v["day"])
            c["today"] += d == today
            c["week"] += d >= mon and d <= today
            c["month"] += d.year == today.year and d.month == today.month
        return c

    def done_by_day(self, days=62, today=None):
        today = today or self.today()
        lo, out = (today - timedelta(days=days)).isoformat(), {}
        for v in self.done.values():
            if v.get("src") in ("entered", "seen") and v.get("day") and v["day"] >= lo:
                out[v["day"]] = out.get(v["day"], 0) + 1
        return out

    def done_list(self, limit=8):
        rows = [(v["day"], k, v) for k, v in self.done.items() if v.get("src") in ("entered", "seen") and v.get("day")]
        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
        return [{"id": k, "name": v.get("name", ""), "board": v.get("board", ""), "day": d,
                 "label": ("entrou em Aprovar em " if v["src"] == "entered" else "visto em ") + self._dm(d)} for d, k, v in rows[:limit]]

    def state(self, times=None):
        """Estado do celular: itens em execução por prazo + contagem de Aprovar. Nunca leva token."""
        today = self.today()
        groups = {g: [] for g in GROUPS}
        for i in self.items:
            if i.get("cat") != "running":
                continue
            item = {k: i.get(k) for k in ("id", "name", "board", "group", "status", "due", "time")}
            item["open"] = bool(i.get("url"))
            if times and i["id"] in times:
                item["spent"] = times[i["id"]]
            groups[group_of(i.get("due"), today)].append(item)
        for g in GROUPS:
            groups[g].sort(key=lambda x: (x["due"] or "9999", x["time"] or "", x["name"].casefold()))
        st = self.status()
        dc = self.done_counts(today)
        prior = sum(1 for v in self.done.values() if v.get("src") == "prior")
        return {"state": st["state"], "on": st["on"], "at": st["at"], "boards_bad": len(self.berr),
                "hhmm": datetime.fromtimestamp(self.at, TZ).strftime("%H:%M") if self.at else None,
                "today": today.isoformat(), "groups": groups, "counts": {g: len(groups[g]) for g in GROUPS},
                "done": {**dc, "recent": self.done_list(), "by_day": self.done_by_day(today=today), "prior": prior,
                         "today_ids": [k for k, v in self.done.items() if v.get("src") in ("entered", "seen") and v.get("day") == today.isoformat()][:100]}}

    def all_items(self):
        """Para o painel (busca ao ligar um item a um job)."""
        return [{"id": i["id"], "name": i["name"], "board": i["board"], "cat": i["cat"]} for i in self.items]

    def has_item(self, iid):
        return isinstance(iid, str) and bool(ID_RX.match(iid)) and any(i["id"] == iid for i in self.items)

    def item_name(self, iid):
        return next((i["name"] for i in self.items if i["id"] == iid), "")

    def item_url(self, iid):
        return next((i.get("url") for i in self.items if i["id"] == iid), None)

    # -- aviso das 9h ---------------------------------------------------------------------
    def digest(self):
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
        return {"title": "monday: " + " · ".join(parts), "body": "; ".join(names[:4]), "overdue": n_o, "today": n_t}
