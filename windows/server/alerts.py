"""Avisos do servidor sem I/O próprio: "arquivo fora do lugar" (Misplaced) e "modo foco" do Pomodoro (FocusHold).

Nada daqui vai ao log (nomes de arquivo, apps e textos de notificação são pessoais) nem ao disco: tudo em memória.
"""
import re
import time
from datetime import date, datetime

import work

MISPLACED_OPTIONS = (0, 5, 10, 20)   # minutos; 0 = desligado
DEFAULT_MISPLACED_MIN = 10
GAP_MAX_S = 10                       # intervalo maior que isso entre leituras não conta como tempo contínuo


def valid_misplaced_min(v):
    if isinstance(v, bool) or v not in MISPLACED_OPTIONS:
        raise ValueError("minutos do aviso: 5, 10, 20 ou 0 (desligado)")
    return int(v)


class Misplaced:
    """Tempo contínuo no mesmo arquivo (mesmo app e nome) em foco e com input. Avisa UMA vez por arquivo por dia.
    Só usa o que o WorkTracker já decidiu: ignorado/identificado (raiz ou regra) nunca avisa; nome genérico e
    "Não identificado" avisam. Trocar de arquivo zera a contagem; passar para um programa que não é de trabalho só a pausa."""

    def __init__(self):
        self.key, self.acc, self.last = None, 0.0, None
        self.day, self.warned, self.setup_day = None, set(), None

    def observe(self, info, now, has_roots, minutes):
        """info = o que WorkTracker.poll devolveu. Devolve {"kind": "generic"|"outside"|"setup", "title", "body"} ou None."""
        day = date.fromtimestamp(now)
        if day != self.day:
            self.day, self.warned, self.setup_day = day, set(), None
        if minutes <= 0:
            self.key, self.acc, self.last = None, 0.0, None
            return None
        app, f = info.get("app"), info.get("file")
        if not app or not f:
            self.last = None
            return None
        key = (app, f.casefold())
        if key != self.key:
            self.key, self.acc, self.last = key, 0.0, None
        if self.last is not None and 0 < now - self.last <= GAP_MAX_S and info.get("counting"):
            self.acc += now - self.last
        self.last = now
        if info.get("ignored") or info.get("identified") or self.acc < minutes * 60 or key in self.warned:
            return None
        self.warned.add(key)
        if not has_roots:
            if self.setup_day == day:
                return None
            self.setup_day = day
            return {"kind": "setup", "title": "Pastas de clientes", "body": "Configure as pastas de clientes no painel."}
        m = int(self.acc // 60)
        if work.is_generic(f):
            return {"kind": "generic", "title": "Arquivo sem salvar",
                    "body": f"Há {m} min em '{f}' no {app} sem salvar. Salve na pasta do cliente."}
        return {"kind": "outside", "title": "Arquivo fora do lugar", "body": f"'{f}' está fora das pastas de clientes."}


UNIDENT_DEFAULT = {"on": True, "at": "18:00"}
UNIDENT_WINDOW_S = 4 * 3600          # depois da hora marcada ainda avisa por até 4 h (servidor ligado atrasado); depois disso pula o dia
UNIDENT_MIN_S = 60                   # menos de 1 min sem atribuir: nada a avisar
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def clean_unident(v):
    """{on: bool, at: "HH:MM"}: a mesma regra no config.json e no painel."""
    if not isinstance(v, dict) or not isinstance(v.get("on"), bool) or not isinstance(v.get("at"), str) or not _HHMM.match(v["at"]):
        raise ValueError("aviso diário: ligado/desligado e hora no formato HH:MM")
    return {"on": v["on"], "at": v["at"]}


def unident_cfg(v):
    try:
        return clean_unident(v)
    except ValueError:
        return dict(UNIDENT_DEFAULT)


def unident_message(summary):
    """(título, corpo) com horas por programa, ou None se há menos de 1 min sem atribuir. Sem nome de arquivo, cliente ou job."""
    if not summary or summary.get("total_s", 0) < UNIDENT_MIN_S:
        return None

    def dur(s):
        m = round(s / 60)
        return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{max(1, m)} min"
    parts = ", ".join(f"{a} {dur(s)}" for a, s in summary["by_app"].items() if s >= 30)
    return ("Tempo sem atribuir hoje", f"{dur(summary['total_s'])} em \"Não identificado\"" + (f": {parts}." if parts else "."))


class DailyUnident:
    """Avisa 1x por dia, na hora marcada, quanto tempo de hoje ficou "Não identificado". Guarda só o dia do último aviso
    (arquivo pequeno ao lado do config.json) para um reinício não repetir o aviso."""

    def __init__(self, path=None):
        self.path, self.last = path, None
        if path is not None:
            d = work._load_json(path, lambda x: isinstance(x, dict) and isinstance(x.get("last", ""), str))
            self.last = (d or {}).get("last") or None

    def due(self, now, cfg):
        """now = datetime local. True uma vez por dia entre a hora marcada e +4 h."""
        if not cfg.get("on") or self.last == now.date().isoformat():
            return False
        h, m = int(cfg["at"][:2]), int(cfg["at"][3:])
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return 0 <= (now - start).total_seconds() <= UNIDENT_WINDOW_S

    def mark(self, now):
        self.last = now.date().isoformat()
        if self.path is not None:
            try:
                work._save_json(self.path, {"last": self.last})
            except OSError:
                pass


class FocusHold:
    """Segura (só na memória) as notificações do Windows que iriam ao celular enquanto o Pomodoro está em Foco."""
    MAX_ITEMS = 50
    MAX_EXCEPT = 20

    def __init__(self, clock=time.time):
        self.active, self.n, self.items, self.counts, self.clock = False, 0, [], {}, clock

    def set_active(self, on):
        """Devolve o resumo (dict) quando o foco acaba com algo seguro; senão None."""
        on = bool(on)
        out = None
        if self.active and not on:
            out = self.take()
        if on and not self.active:
            self.n, self.items, self.counts = 0, [], {}
        self.active = on
        return out

    def should_hold(self, app, except_apps):
        return self.active and (app or "").casefold() not in {a.casefold() for a in except_apps or []}

    def hold(self, n):
        self.n += 1
        app = n.get("app") or "Windows"
        self.counts[app] = self.counts.get(app, 0) + 1
        if len(self.items) < self.MAX_ITEMS:
            self.items.append({"app": app, "title": n.get("title", ""), "body": n.get("body", ""),
                               "time": time.strftime("%H:%M", time.localtime(self.clock()))})

    def take(self):
        if not self.n:
            return None
        groups = sorted(self.counts.items(), key=lambda kv: -kv[1])
        text = "Durante o foco: " + ", ".join(f"{c} do {a}" for a, c in groups)
        out = {"total": self.n, "groups": [{"app": a, "n": c} for a, c in groups], "items": self.items, "text": text}
        self.n, self.items, self.counts = 0, [], {}
        return out


def valid_focus_except(v):
    if not isinstance(v, list) or len(v) > FocusHold.MAX_EXCEPT:
        raise ValueError(f"até {FocusHold.MAX_EXCEPT} apps de exceção")
    out = []
    for a in v:
        if not isinstance(a, str) or not a.strip() or len(a) > 80 or any(ord(c) < 32 for c in a):
            raise ValueError("nome de app inválido")
        if a.strip().casefold() not in {x.casefold() for x in out}:
            out.append(a.strip())
    return out
