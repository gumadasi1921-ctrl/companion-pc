"""Avisos do servidor sem I/O próprio: "arquivo fora do lugar" (Misplaced) e "modo foco" do Pomodoro (FocusHold).

Nada daqui vai ao log (nomes de arquivo, apps e textos de notificação são pessoais) nem ao disco: tudo em memória.
"""
import time
from datetime import date

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
