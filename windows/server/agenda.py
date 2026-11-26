"""Agenda (SOMENTE LEITURA): URL secreta .ics do Google Agenda (ou outro .ics https), guardada só no config.json do PC.

Só GET; nada é escrito na agenda. Lê eventos com horário (cancelados ficam de fora; o dia inteiro só entra no `cal` do widget
Calendário, nunca em `items`/aviso), expande recorrências
(RRULE/EXDATE/RECURRENCE-ID) com fuso correto (zoneinfo + tzdata, python-dateutil) e guarda as ocorrências dos próximos
14 dias em agenda.json (só o necessário: título, horário, link). A URL e os títulos nunca vão ao log.
Link de reunião: somente X-GOOGLE-CONFERENCE, URL ou LOCATION (quando é exatamente um link) em https e de meet.google.com,
zoom.us ou teams.microsoft.com. Links soltos na descrição são ignorados. Sem dado = estado vazio, nunca inventa.
"""
import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rruleset, rrulestr

log = logging.getLogger("companion.agenda")

REFRESH_S = 300
RETRY_S = 60
MANUAL_MIN_S = 60
HORIZON_DAYS = 14
MAX_BYTES = 5_000_000
MAX_EVENTS = 5000
MAX_CAL = 800
NOTIFY_OPTIONS = (1, 5, 10, 15)
DEFAULT_NOTIFY = 5
HOSTS = ("meet.google.com", "zoom.us", "teams.microsoft.com")
URL_RX = re.compile(r"^https://[^\s\x00-\x1f\x7f]{8,1500}$")
BAD_FREQ = ("SECONDLY", "MINUTELY", "HOURLY")
MONTHS_BACK, MONTHS_AHEAD = 12, 24   # janela que `agenda.month` aceita, contada a partir do mês de hoje (fuso do PC)
REREAD_AFTER_S = 120   # depois de abrir o Google Agenda no PC: relê sozinho (respeitando MANUAL_MIN_S)
DAY_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EVENT_LINK_RX = re.compile(r"^https://calendar\.google\.com/calendar/event\?eid=[A-Za-z0-9_=-]{4,300}$")


class FeedError(Exception):
    """kind: bad_url | net_error | bad_feed"""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


def valid_url(v):
    if not isinstance(v, str) or not URL_RX.match(v.strip()):
        raise ValueError("Endereço do calendário inválido (precisa começar com https://)")
    return v.strip()


def valid_notify(v):
    if isinstance(v, bool) or v not in NOTIFY_OPTIONS:
        raise ValueError("Avisar 1, 5, 10 ou 15 minutos antes")
    return v


def safe_link(u):
    """Só https e só dos serviços de reunião conhecidos; qualquer outra coisa vira None."""
    if not isinstance(u, str):
        return None
    u = u.strip()
    if len(u) > 500 or re.search(r"[\s\x00-\x1f\x7f]", u):
        return None
    p = urlsplit(u)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not host or p.username or p.password:
        return None
    return u if any(host == h or host.endswith("." + h) for h in HOSTS) else None


# ---------------------------------------------------------------- parser .ics
def _unfold(text):
    out = []
    for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return out


def _prop(line):
    """'NAME;P=V;P2="x":valor' -> (NAME, {P: V}, valor). Aspas respeitadas; None se a linha não é propriedade."""
    i, q = 0, False
    for i, c in enumerate(line):
        if c == '"':
            q = not q
        elif c == ":" and not q:
            break
    else:
        return None
    head, val = line[:i], line[i + 1:]
    parts, cur, q = [], "", False
    for c in head:
        if c == '"':
            q = not q
        if c == ";" and not q:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    parts.append(cur)
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return parts[0].upper(), params, val


def _text(v):
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _dt(val, params, local_tz):
    """-> datetime aware | date (dia inteiro). Levanta ValueError para TZID desconhecido ou formato estranho."""
    val = val.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", val):
        return datetime.strptime(val, "%Y%m%d").date()
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", val)
    if not m:
        raise ValueError("data inválida")
    naive = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    if m.group(2):
        return naive.replace(tzinfo=timezone.utc)
    tzid = params.get("TZID")
    if tzid:
        try:
            return naive.replace(tzinfo=ZoneInfo(tzid))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("fuso desconhecido") from None
    return naive.replace(tzinfo=local_tz)   # horário "flutuante" = hora do próprio PC


def _dur(val):
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", val.strip())
    if not m:
        raise ValueError("duração inválida")
    sign = -1 if m.group(1) == "-" else 1
    w, d, h, mi, s = (int(x or 0) for x in m.groups()[1:])
    return sign * timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse_ics(text, local_tz=None, allday=False):
    """-> (eventos, nome_do_calendario, ignorados). Cada evento: dict(uid,title,start,end,rrule,exdates,rid,cancelled,links).
    `ignorados` conta eventos com horário que não deu para ler (fuso/regra desconhecidos): nunca são adivinhados."""
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    lines = _unfold(text)
    if not any(ln.strip().upper() == "BEGIN:VCALENDAR" for ln in lines[:20]):
        raise FeedError("bad_feed")
    events, name, skipped, cur = [], "", 0, None
    for ln in lines:
        u = ln.strip().upper()
        if u == "BEGIN:VEVENT":
            cur = {"props": []}
            continue
        if u == "END:VEVENT" and cur is not None:
            ev, bad = _event(cur["props"], local_tz, allday)
            skipped += bad
            if ev:
                events.append(ev)
            cur = None
            if len(events) >= MAX_EVENTS:
                break
            continue
        pr = _prop(ln) if ln else None
        if not pr:
            continue
        if cur is not None:
            cur["props"].append(pr)
        elif pr[0] == "X-WR-CALNAME":
            name = _text(pr[2])[:60]
    return events, name, skipped


def _event(props, local_tz, allday=False):
    g = {}
    exd, links = [], []
    for n, p, v in props:
        if n == "EXDATE":
            exd.append((p, v))
        elif n in ("X-GOOGLE-CONFERENCE", "URL", "LOCATION"):
            links.append((n, v))
        elif n not in g:
            g[n] = (p, v)
    if g.get("STATUS", ("", ""))[1].strip().upper() == "CANCELLED":
        if "RECURRENCE-ID" not in g:
            return None, 0
        if _is_date(*g["RECURRENCE-ID"]):
            r = _date_rid(g)
            return ({"uid": g.get("UID", ("", ""))[1], "cancelled": True, "rid": r, "allday": True}, 0) if (allday and r) else (None, 0)
        return {"uid": g.get("UID", ("", ""))[1], "cancelled": True, "rid": _safe_rid(g, local_tz)}, 0
    if "DTSTART" not in g:
        return None, 0
    try:
        start = _dt(g["DTSTART"][1], g["DTSTART"][0], local_tz)
        if isinstance(start, date) and not isinstance(start, datetime):
            if not allday:
                return None, 0   # dia inteiro: não é reunião (só o `cal` do widget Calendário pede)
            return _allday_event(g, exd, links, start), 0
        if "DTEND" in g:
            end = _dt(g["DTEND"][1], g["DTEND"][0], local_tz)
        elif "DURATION" in g:
            end = start + _dur(g["DURATION"][1])
        else:
            end = start
        if isinstance(end, date) and not isinstance(end, datetime):
            end = start
        rrule = g.get("RRULE", ("", ""))[1].strip() or None
        if rrule:
            freq = re.search(r"FREQ=([A-Z]+)", rrule.upper())
            if not freq or freq.group(1) in BAD_FREQ:
                raise ValueError("regra não suportada")
        exdates = []
        for p, v in exd:
            for part in v.split(","):
                if part.strip():
                    d = _dt(part, p, local_tz)
                    if isinstance(d, datetime):
                        exdates.append(d)
        rid = _safe_rid(g, local_tz, strict=True)
    except ValueError:
        return None, 1
    lk = _pick_link(links)
    title = _text(g.get("SUMMARY", ("", ""))[1]).strip()[:120] or "(sem título)"
    return {"uid": g.get("UID", ("", ""))[1].strip(), "title": title, "start": start, "end": max(end, start), "rrule": rrule,
            "exdates": exdates, "rid": rid, "cancelled": False, "link": lk, "elink": _pick_elink(links)}, 0


def _pick_elink(links):
    """Link do PRÓPRIO evento no Google Agenda (só se o .ics trouxer, na propriedade URL, nesse formato exato). Nunca é montado."""
    for n, v in links:
        if n == "URL":
            u = _text(v).strip()
            if EVENT_LINK_RX.match(u):
                return u
    return None


def _ok_elink(u):
    return u if isinstance(u, str) and EVENT_LINK_RX.match(u) else None


def _pick_link(links):
    for n, v in sorted(links, key=lambda x: ("X-GOOGLE-CONFERENCE", "URL", "LOCATION").index(x[0])):
        lk = safe_link(_text(v))
        if lk:
            return lk
    return None


def _is_date(params, val):
    return params.get("VALUE") == "DATE" or bool(re.fullmatch(r"\d{8}", val.strip()))


def _date_rid(g):
    try:
        d = datetime.strptime(g["RECURRENCE-ID"][1].strip()[:8], "%Y%m%d")
        return d
    except ValueError:
        return None


def _allday_event(g, exd, links, start_d):
    """Dia inteiro: datas ingênuas (meia-noite do dia), sem fuso, imune a horário de verão. `days` = quantos dias cobre."""
    if "DTEND" in g:
        e = _dt(g["DTEND"][1], g["DTEND"][0], None)
        end_d = e.date() if isinstance(e, datetime) else e
    elif "DURATION" in g:
        end_d = start_d + timedelta(days=max(1, _dur(g["DURATION"][1]).days))
    else:
        end_d = start_d + timedelta(days=1)
    rrule = g.get("RRULE", ("", ""))[1].strip() or None
    if rrule:
        freq = re.search(r"FREQ=([A-Z]+)", rrule.upper())
        if not freq or freq.group(1) in BAD_FREQ:
            raise ValueError("regra não suportada")
    exdates = []
    for p, v in exd:
        for part in v.split(","):
            if part.strip():
                d = _dt(part, p, None)
                exdates.append(datetime.combine(d.date() if isinstance(d, datetime) else d, dtime.min))
    rid = None
    if "RECURRENCE-ID" in g:
        rid = _date_rid(g) if _is_date(*g["RECURRENCE-ID"]) else None
        if rid is None:
            raise ValueError("exceção inválida")
    title = _text(g.get("SUMMARY", ("", ""))[1]).strip()[:120] or "(sem título)"
    return {"uid": g.get("UID", ("", ""))[1].strip(), "title": title, "allday": True, "start": datetime.combine(start_d, dtime.min),
            "days": max(1, (end_d - start_d).days), "rrule": rrule, "exdates": exdates, "rid": rid, "cancelled": False, "link": _pick_link(links), "elink": _pick_elink(links)}


def _safe_rid(g, local_tz, strict=False):
    if "RECURRENCE-ID" not in g:
        return None
    try:
        d = _dt(g["RECURRENCE-ID"][1], g["RECURRENCE-ID"][0], local_tz)
        return d if isinstance(d, datetime) else None
    except ValueError:
        if strict:
            raise
        return None


def expand(events, now, horizon_days=HORIZON_DAYS, lo_ts=None, hi_ts=None):
    """Ocorrências (epoch) que terminam depois de `now` e começam antes do fim do horizonte. Ordenadas por início."""
    lo = datetime.fromtimestamp(lo_ts if lo_ts is not None else now, timezone.utc)
    hi = datetime.fromtimestamp(hi_ts, timezone.utc) if hi_ts is not None else lo + timedelta(days=horizon_days)
    overrides = {}   # (uid, rid em epoch) -> evento (pode estar cancelado)
    events = [e for e in events if not e.get("allday")]   # dia inteiro só pelo expand_allday
    for e in events:
        if e.get("rid") is not None:
            overrides[(e["uid"], int(e["rid"].timestamp()))] = e
    out, skipped = [], 0
    for e in events:
        if e.get("cancelled") and e.get("rid") is None:
            continue
        if e.get("rid") is not None or not e.get("rrule"):
            if e.get("cancelled") or e.get("start") is None:
                continue
            if e["end"] > lo and e["start"] < hi:
                out.append((e, e["start"], e["end"]))
            continue
        dur = e["end"] - e["start"]
        try:
            rs = rruleset()
            rs.rrule(rrulestr(e["rrule"], dtstart=e["start"]))
            for x in e["exdates"]:
                rs.exdate(x)
            for s in rs.between(lo - dur - timedelta(seconds=1), hi, inc=True):
                if (e["uid"], int(s.timestamp())) in overrides:
                    continue   # essa ocorrência foi movida/cancelada por uma exceção
                if s + dur > lo:
                    out.append((e, s, s + dur))
        except (ValueError, TypeError, OverflowError):
            skipped += 1
    res = []
    for e, s, en in sorted(out, key=lambda t: t[1]):
        ts = int(s.timestamp())
        res.append({"id": hashlib.sha1(f"{e['uid']}|{ts}".encode()).hexdigest()[:10], "title": e["title"], "start": ts,
                    "end": int(en.timestamp()), "link": e.get("link"), "elink": e.get("elink")})
    return res, skipped


def expand_allday(events, d_lo, d_hi):
    """Ocorrências de dia inteiro que tocam [d_lo, d_hi] (datas). -> ([{id,title,day,days,link}], ignorados)."""
    ev = [e for e in events if e.get("allday")]
    overrides = {(e["uid"], e["rid"].date()) for e in ev if e.get("rid") is not None}
    out, skipped = [], 0

    def add(e, s):
        n = e.get("days", 1)
        if s <= d_hi and s + timedelta(days=n - 1) >= d_lo:
            out.append({"id": hashlib.sha1(f"{e['uid']}|{s.isoformat()}".encode()).hexdigest()[:10], "title": e["title"], "day": s.isoformat(),
                        "days": n, "link": e.get("link"), "elink": e.get("elink")})

    for e in ev:
        if e.get("cancelled"):
            continue
        if e.get("rid") is not None or not e.get("rrule"):
            add(e, e["start"].date())
            continue
        try:
            rs = rruleset()
            rs.rrule(rrulestr(e["rrule"], dtstart=e["start"]))
            for x in e["exdates"]:
                rs.exdate(x)
            lo = datetime.combine(d_lo - timedelta(days=e.get("days", 1)), dtime.min)
            for s in rs.between(lo, datetime.combine(d_hi, dtime.max), inc=True):
                if (e["uid"], s.date()) not in overrides:
                    add(e, s.date())
        except (ValueError, TypeError, OverflowError):
            skipped += 1
    out.sort(key=lambda x: (x["day"], x["title"]))
    return out, skipped


# ---------------------------------------------------------------- rede
def default_fetch(url):
    """GET único deste módulo. Devolve o texto; erros viram FeedError com tipo fixo."""
    req = urllib.request.Request(url, headers={"User-Agent": "Companion-PC", "Accept": "text/calendar, */*"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:   # noqa: S310 - só https (valid_url)
            if not r.geturl().lower().startswith("https://"):
                raise FeedError("bad_url")
            data = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise FeedError("bad_url" if e.code in (400, 401, 403, 404, 410) else "net_error") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise FeedError("net_error") from None
    if len(data) > MAX_BYTES:
        raise FeedError("bad_feed")
    return data.decode("utf-8", errors="replace")


class Agenda:
    def __init__(self, base, get_cfg, fetch=default_fetch, clock=time.time, local_tz=None):
        self.path = Path(base) / "agenda.json"
        self.get_cfg, self.fetch, self.clock, self.local_tz = get_cfg, fetch, clock, local_tz
        self.items, self.at, self.try_at, self.error, self.name, self.skipped = [], 0, 0, None, "", 0
        self.cal, self.cal_day = [], None
        self.events = None     # eventos parseados da última leitura (só memória): `agenda.month` expande daqui, sem rede
        self.mcache = {}       # (ano, mês) -> registros do mês; zerado a cada leitura
        self.reread_at = 0     # depois de abrir o Google Agenda no PC: hora da releitura automática
        self.notified = set()
        self.cur_url = None
        self._load()

    # -- disco ------------------------------------------------------------
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if d.get("v") != 1:
                raise ValueError("estrutura")
            self.items = [{"id": str(i["id"]), "title": str(i["title"])[:120], "start": int(i["start"]), "end": int(i["end"]), "link": safe_link(i.get("link"))}
                          for i in d["items"]][:MAX_EVENTS]
            self.at, self.name, self.skipped = float(d.get("at", 0)), str(d.get("name", ""))[:60], int(d.get("skipped", 0))
            self.cal = self._cal_load(d.get("cal"))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("agenda.json ilegível: começando vazio")
            self.items = []

    @staticmethod
    def _cal_load(raw):
        out = []
        for i in raw if isinstance(raw, list) else []:
            try:
                r = {"id": str(i["id"]), "title": str(i["title"])[:120], "day": date.fromisoformat(str(i["day"])).isoformat(), "start": int(i["start"]),
                     "end": int(i["end"]), "allday": bool(i.get("allday")), "link": safe_link(i.get("link")), "elink": _ok_elink(i.get("elink"))}
                if r["allday"]:
                    r["days"] = max(1, min(366, int(i.get("days", 1))))
                out.append(r)
            except (KeyError, TypeError, ValueError):
                continue
        return out[:MAX_CAL]

    def _local(self, ts):
        return datetime.fromtimestamp(ts, self.local_tz) if self.local_tz else datetime.fromtimestamp(ts).astimezone()

    def _mid(self, d):
        """Epoch da meia-noite local do dia `d` (fuso do PC, com horário de verão)."""
        return int(datetime(d.year, d.month, d.day, tzinfo=self.local_tz).timestamp() if self.local_tz else datetime(d.year, d.month, d.day).timestamp())

    def _window(self, events, first, end):
        """Eventos de [first, end] (datas do PC): com horário (inclui os que já passaram) e dia inteiro. Cada um leva `day` = data LOCAL DO PC."""
        timed, sk = expand(events, 0, lo_ts=self._mid(first), hi_ts=self._mid(end + timedelta(days=1)))
        out = []
        for t in timed:
            d = self._local(t["start"]).date()
            if first <= d <= end:
                out.append({"id": t["id"], "title": t["title"], "day": d.isoformat(), "start": t["start"], "end": t["end"], "allday": False,
                            "link": t["link"], "elink": t.get("elink")})
        days, sk2 = expand_allday(events, first, end)
        for a in days:
            s = date.fromisoformat(a["day"])
            out.append({"id": a["id"], "title": a["title"], "day": a["day"], "start": self._mid(s), "end": self._mid(s + timedelta(days=a["days"])),
                        "allday": True, "days": a["days"], "link": a["link"], "elink": a.get("elink")})
        out.sort(key=lambda x: (x["day"], not x["allday"], x["start"], x["title"]))
        return out[:MAX_CAL], sk + sk2

    def _cal_build(self, events, now):
        """Janela do widget Calendário: do dia 1 do mês até o maior entre o fim do mês e hoje+8 (fuso do PC). Eventos de hoje que já
        passaram e de dia inteiro entram aqui (e só aqui). Cada um leva `day` = data LOCAL DO PC, para o celular não depender do fuso."""
        today = self._local(now).date()
        first = today.replace(day=1)
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        out, sk = self._window(events, first, max(last, today + timedelta(days=8)))
        return out, sk, today

    def _save(self):
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"v": 1, "at": self.at, "name": self.name, "skipped": self.skipped, "items": self.items, "cal": self.cal}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.warning("agenda.json: não consegui gravar")

    # -- estado -----------------------------------------------------------
    def cfg(self):
        c = self.get_cfg()
        return c if isinstance(c, dict) else {}

    def _sync(self):
        """URL trocada no painel: esquece a leitura do endereço antigo e busca de novo na hora."""
        url = self.cfg().get("url") or ""
        if self.cur_url is None:
            self.cur_url = url
        elif url != self.cur_url:
            self.cur_url = url
            self.items, self.at, self.try_at, self.error, self.name, self.skipped = [], 0, 0, None, "", 0
            self.cal, self.cal_day = [], None
            self.events, self.mcache, self.reread_at = None, {}, 0
            self.notified = set()

    def enabled(self):
        c = self.cfg()
        return bool(c.get("on") and c.get("url"))

    def notify_min(self):
        v = self.cfg().get("notify_min", DEFAULT_NOTIFY)
        return v if v in NOTIFY_OPTIONS else DEFAULT_NOTIFY

    def status(self):
        self._sync()
        c = self.cfg()
        state = "off" if not self.enabled() else self.error or ("ok" if self.at else "waiting")
        return {"on": bool(c.get("on")), "has_url": bool(c.get("url")), "notify_min": self.notify_min(), "state": state,
                "at": self.at or None, "name": self.name, "skipped": self.skipped}

    def state(self):
        """O que o celular recebe: a próxima reunião (ou a em andamento) e as seguintes. Sem dado = next None."""
        now = self.clock()
        st = self.status()
        live = [i for i in self.items if i["end"] > now or i["start"] >= now] if self.enabled() else []
        out = [{"id": i["id"], "title": i["title"], "start": i["start"], "end": i["end"], "join": bool(i["link"]), "now": i["start"] <= now}
               for i in live[:8]]
        return {"on": st["on"], "state": st["state"], "at": st["at"], "now": now, "next": out[0] if out else None, "upcoming": out,
                "notify_min": st["notify_min"], "cal": self.cal_state(now)}

    def cal_state(self, now):
        """Para o widget Calendário: `tz` (minutos de UTC no PC agora), `today` (data do PC) e os eventos do mês (sem link, só `join`)."""
        dt = self._local(now)
        items = []
        if self.enabled():
            for i in self.cal:
                r = {"id": i["id"], "title": i["title"], "day": i["day"], "start": i["start"], "end": i["end"], "allday": i["allday"], "join": bool(i["link"])}
                if i["allday"]:
                    r["days"] = i["days"]
                items.append(r)
        return {"tz": int(dt.utcoffset().total_seconds() // 60), "today": dt.date().isoformat(), "items": items}

    def link_for(self, eid):
        for i in self.items + self.cal + [x for v in self.mcache.values() for x in v]:
            if i["id"] == eid:
                return safe_link(i.get("link"))
        return None

    # -- outros meses (`agenda.month`) --------------------------------------
    def _month_ok(self, y, m, now):
        t = self._local(now).date()
        k, cur = y * 12 + (m - 1), t.year * 12 + (t.month - 1)
        return cur - MONTHS_BACK <= k <= cur + MONTHS_AHEAD

    def month(self, y, m):
        """Eventos de um mês (fuso do PC), com o mesmo parser/recorrência/dia inteiro do `cal`, a partir dos eventos já lidos (sem rede,
        exceto a 1ª vez depois de reiniciar, que relê respeitando MANUAL_MIN_S). Nunca devolve link, só `join`."""
        self._sync()
        if not (type(y) is int and type(m) is int and 1 <= m <= 12 and 1 <= y <= 9998):
            raise ValueError("mês inválido")
        now = self.clock()
        if not self._month_ok(y, m, now):
            raise ValueError("mês fora do período permitido")
        dt = self._local(now)
        out = {"year": y, "month": m, "tz": int(dt.utcoffset().total_seconds() // 60), "today": dt.date().isoformat(),
               "state": self.status()["state"], "items": [], "days": []}
        if not self.enabled():
            return out
        if self.events is None:
            self.refresh_manual()
        if self.events is None:
            out["state"] = self.error or out["state"]
            return out
        recs = self.mcache.get((y, m))
        if recs is None:
            first = date(y, m, 1)
            last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            recs, _ = self._window(self.events, first, last)
            self.mcache[(y, m)] = recs
        days = set()
        for i in recs:
            r = {"id": i["id"], "title": i["title"], "day": i["day"], "start": i["start"], "end": i["end"], "allday": i["allday"], "join": bool(i["link"])}
            if i["allday"]:
                r["days"] = i["days"]
            out["items"].append(r)
            d0 = date.fromisoformat(i["day"])
            for k in range(i["days"] if i["allday"] else 1):
                dk = d0 + timedelta(days=k)
                if (dk.year, dk.month) == (y, m):
                    days.add(dk.isoformat())
        out["days"] = sorted(days)
        return out

    # -- abrir no Google Agenda (no PC; o servidor monta o link, nunca o celular) -------
    def note_opened(self):
        self.reread_at = self.clock() + REREAD_AFTER_S

    def new_url(self, day):
        """Criar evento no Google Agenda. Hoje: da próxima hora cheia até +1 h (hora local, sem fuso: vale o do calendário). Outro dia: dia inteiro."""
        now = self.clock()
        if not isinstance(day, str) or not DAY_RX.match(day):
            raise ValueError("data inválida")
        try:
            d = date.fromisoformat(day)
        except ValueError:
            raise ValueError("data inválida") from None
        if not self._month_ok(d.year, d.month, now):
            raise ValueError("data fora do período permitido")
        dt = self._local(now)
        if d == dt.date():
            a = dt.replace(minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(hours=1)
            dates = f"{a:%Y%m%dT%H%M%S}/{a + timedelta(hours=1):%Y%m%dT%H%M%S}"
        else:
            dates = f"{d:%Y%m%d}/{d + timedelta(days=1):%Y%m%d}"
        return "https://calendar.google.com/calendar/render?action=TEMPLATE&dates=" + dates

    def open_url(self, eid):
        """Abrir um evento: o link do próprio evento se o .ics trouxe; senão a visão do dia dele. Nunca monta link de edição."""
        if not isinstance(eid, str) or not eid:
            raise ValueError("evento inválido")
        for pool in [self.items, self.cal] + list(self.mcache.values()):
            for i in pool:
                if i["id"] == eid:
                    if i.get("elink"):
                        return i["elink"]
                    d = date.fromisoformat(i["day"]) if i.get("day") else self._local(i["start"]).date()
                    return f"https://calendar.google.com/calendar/r/day/{d.year:04d}/{d.month:02d}/{d.day:02d}"
        raise ValueError("evento não encontrado")

    # -- leitura ----------------------------------------------------------
    def due(self, has_clients):
        self._sync()
        if not has_clients or not self.enabled():
            return False
        if self.reread_at and self.clock() >= self.reread_at:   # abriu o Google Agenda no PC há ~2 min: relê (no mínimo 60 s entre leituras)
            return self.clock() - self.try_at >= MANUAL_MIN_S
        # virou o dia (ou o mês): o `cal` precisa dos dias novos, então relê sem esperar os 5 min (respeitando o intervalo de nova tentativa)
        stale = self.cal_day is not None and self.cal_day != self._local(self.clock()).date()
        return self.clock() - self.try_at >= (RETRY_S if (self.error == "net_error" or stale) else REFRESH_S)

    def _read(self, url):
        text = self.fetch(url)
        events, name, skipped = parse_ics(text, self.local_tz, allday=True)
        items, sk2 = expand(events, self.clock())
        cal, sk3, day = self._cal_build(events, self.clock())
        return items, name, skipped + sk2 + sk3, cal, day, events

    def refresh(self):
        if not self.enabled():
            return
        self.try_at = self.clock()
        self.reread_at = 0
        try:
            self.items, self.name, self.skipped, self.cal, self.cal_day, self.events = self._read(self.cfg()["url"])
            self.mcache = {}
            self.at, self.error = self.clock(), None
            self._save()
        except FeedError as e:
            self.error = e.kind   # mantém a última leitura (net_error) ou mostra o erro (bad_url/bad_feed)
        except Exception:  # noqa: BLE001
            self.error = "bad_feed"
        log.info("agenda: %s", self.error or "atualizada")

    def refresh_manual(self):
        if self.enabled() and self.clock() - self.try_at >= MANUAL_MIN_S:
            self.refresh()

    def test(self, url):
        """Botão Testar do painel: lê a URL digitada (ou a salva) e diz só quantas reuniões vêm pela frente."""
        items, name, skipped = self._read(url)[:3]
        return {"count": len(items), "name": name, "skipped": skipped}

    # -- aviso "X min antes" ----------------------------------------------
    def soon(self):
        """Reuniões que entram na janela de aviso agora e ainda não foram avisadas (uma vez por ocorrência)."""
        if not self.enabled():
            return []
        now, lead = self.clock(), self.notify_min() * 60
        out = []
        for i in self.items:
            if i["id"] not in self.notified and 0 < i["start"] - now <= lead:
                self.notified.add(i["id"])
                out.append({"id": i["id"], "title": i["title"], "start": i["start"], "minutes": max(1, round((i["start"] - now) / 60)), "join": bool(i["link"])})
        if len(self.notified) > 500:
            self.notified = {i["id"] for i in self.items}
        return out
