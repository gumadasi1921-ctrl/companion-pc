"""Avisos do servidor sem I/O próprio: "arquivo fora do lugar" (Misplaced) e "modo foco" do Pomodoro (FocusHold).

Nada daqui vai ao log (nomes de arquivo, apps e textos de notificação são pessoais) nem ao disco: tudo em memória.
"""
import re
import time
from datetime import date, datetime

import work

MISPLACED_OPTIONS = (0, 1, 2, 5, 10, 20)   # minutos; 0 = desligado
DEFAULT_MISPLACED_MIN = 2
GAP_MAX_S = 10                       # intervalo maior que isso entre leituras não conta como tempo
SAVE_EVERY_S = 30                    # a contagem vai ao disco no máximo a cada 30 s (e ao avisar)


def valid_misplaced_min(v):
    if isinstance(v, bool) or v not in MISPLACED_OPTIONS:
        raise ValueError("minutos do aviso: 1, 2, 5, 10, 20 ou 0 (desligado)")
    return int(v)


class Misplaced:
    """Tempo ACUMULADO no dia em cada arquivo (mesmo app e nome) em foco e com input; trocar de janela ou de app NÃO zera.
    Avisa UMA vez por arquivo por dia. Só usa o que o WorkTracker já decidiu: ignorado/identificado (raiz ou regra) nunca avisa;
    nome genérico e "Não identificado" avisam. A contagem e os avisos do dia ficam em `path` (misplaced-time.json) e sobrevivem
    a reinício do servidor; o log nunca recebe nomes."""

    def __init__(self, path=None, clock=time.time):
        self.path, self.clock = path, clock
        self.acc, self.last, self.last_key, self.saved_at, self.dirty = {}, None, None, 0.0, False
        self.day, self.warned, self.setup_day = None, set(), None
        if path is not None:
            d = work._load_json(path, lambda x: isinstance(x, dict) and isinstance(x.get("acc", {}), dict) and isinstance(x.get("warned", []), list))
            if d and d.get("day") == date.fromtimestamp(self.clock()).isoformat():
                self.day = date.fromtimestamp(self.clock())
                self.acc = {k: float(v) for k, v in d.get("acc", {}).items() if isinstance(k, str) and isinstance(v, (int, float))}
                self.warned = {k for k in d.get("warned", []) if isinstance(k, str)}
                if d.get("setup") == d["day"]:
                    self.setup_day = self.day

    @staticmethod
    def _k(key):
        return key[0] + "	" + key[1]

    def _save(self, now, force=False):
        if self.path is None or not (self.dirty or force) or (not force and now - self.saved_at < SAVE_EVERY_S):
            return
        self.saved_at, self.dirty = now, False
        try:
            work._save_json(self.path, {"day": self.day.isoformat(), "acc": {k: round(v, 1) for k, v in self.acc.items()},
                                        "warned": sorted(self.warned), "setup": self.setup_day.isoformat() if self.setup_day else ""})
        except OSError:
            pass

    def observe(self, info, now, has_roots, minutes):
        """info = o que WorkTracker.poll devolveu. Devolve {"kind": "generic"|"outside"|"setup", "title", "body"} ou None."""
        day = date.fromtimestamp(now)
        if day != self.day:
            self.day, self.acc, self.warned, self.setup_day, self.dirty = day, {}, set(), None, True
        if minutes <= 0:
            self.last, self.last_key = None, None
            return None
        app, f = info.get("app"), info.get("file")
        if not app or not f:
            self.last, self.last_key = None, None
            return None
        key = (app, f.casefold())
        sk = self._k(key)
        if self.last is not None and self.last_key == key and 0 < now - self.last <= GAP_MAX_S and info.get("counting"):
            self.acc[sk] = self.acc.get(sk, 0.0) + (now - self.last)
            self.dirty = True
        self.last, self.last_key = now, key
        acc = self.acc.get(sk, 0.0)
        if info.get("ignored") or info.get("identified") or acc < minutes * 60 or sk in self.warned:
            self._save(now)
            return None
        self.warned.add(sk)
        if not has_roots:
            if self.setup_day == day:
                self._save(now, force=True)
                return None
            self.setup_day = day
            self._save(now, force=True)
            return {"kind": "setup", "title": "Pastas de clientes", "body": "Configure as pastas de clientes no painel."}
        self._save(now, force=True)
        m = int(acc // 60)
        if work.is_generic(f):
            return {"kind": "generic", "title": "Arquivo sem salvar",
                    "body": f"Há {m} min em '{f}' no {app} sem salvar. Salve na pasta do cliente."}
        return {"kind": "outside", "title": "Arquivo fora do lugar", "body": f"'{f}' está fora das pastas de clientes."}

    def flush(self):
        self._save(self.clock(), force=True)


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


# ---- Avisos IMPORTANTES (níveis Normal / Importante) -------------------------------------------------------------
# Nada daqui vai ao log com texto de aviso. O que o PC faz com um Importante: som (só no PC) + balão da bandeja; o celular recebe
# o evento com level "important" (piscada + Lume no centro). Pendente na memória até o celular tocar (alert.ack), reenviado a quem
# conectar e repetido UMA vez se ninguém tocar em 15 min.
IMPORTANT_KINDS = {"misplaced_generic": "Arquivo sem salvar", "misplaced_outside": "Arquivo fora do lugar",
                   "linkcard": "Ligar job ao card", "unident": "Tempo sem atribuir hoje",
                   "battery": "Bateria baixa (fone, mouse)"}
IMPORTANT_SOUNDS = {"chime": "Sino curto", "bell": "Campainha", "pulse": "Três toques"}
IMPORTANT_DEFAULT = {"kinds": ["misplaced_generic", "linkcard", "battery"], "ignore_focus": ["misplaced_generic"], "sound": "chime", "volume": 70}
REPEAT_S = 15 * 60
MAX_PENDING_S = 2 * 3600


def clean_important(v):
    """{kinds[], ignore_focus[], sound, volume 0..100}: a mesma regra no config.json e no painel."""
    if not isinstance(v, dict):
        raise ValueError("avisos importantes inválidos")
    out = {}
    for k in ("kinds", "ignore_focus"):
        x = v.get(k)
        if not isinstance(x, list) or any(not isinstance(i, str) or i not in IMPORTANT_KINDS for i in x):
            raise ValueError("tipo de aviso desconhecido")
        out[k] = [i for i in IMPORTANT_KINDS if i in x]
    if v.get("sound") not in IMPORTANT_SOUNDS:
        raise ValueError("som desconhecido")
    out["sound"] = v["sound"]
    vol = v.get("volume")
    if isinstance(vol, bool) or not isinstance(vol, int) or not 0 <= vol <= 100:
        raise ValueError("volume: 0 a 100")
    out["volume"] = vol
    return out


def important_cfg(v):
    try:
        return clean_important(v)
    except ValueError:
        return {**IMPORTANT_DEFAULT, "kinds": list(IMPORTANT_DEFAULT["kinds"]), "ignore_focus": list(IMPORTANT_DEFAULT["ignore_focus"])}


_RATE = 22050


def alert_wav(name, volume):
    """WAV mono 16 bits em bytes (Python puro, sem numpy). Sons curtos; volume 0..100 escala a amplitude (pico máximo ~0,8)."""
    import io
    import math
    import struct
    import wave
    if name not in IMPORTANT_SOUNDS:
        name = "chime"
    amp = 0.8 * max(0, min(100, int(volume))) / 100.0

    def tone(f, dur, decay, partials=((1.0, 1.0),)):
        n = int(_RATE * dur)
        return [sum(a * math.sin(2 * math.pi * f * r * i / _RATE) for r, a in partials) * math.exp(-decay * i / _RATE)
                * min(1.0, i / 90.0) for i in range(n)]       # 4 ms de entrada: sem estalo

    def mix(parts):
        total = max(off + len(s) for off, s in parts)
        buf = [0.0] * total
        for off, s in parts:
            for i, x in enumerate(s):
                buf[off + i] += x
        return buf
    if name == "chime":
        buf = mix([(0, tone(1318.5, 0.5, 7.0)), (int(_RATE * 0.16), tone(1760.0, 0.7, 6.0))])
    elif name == "bell":
        buf = mix([(0, tone(660.0, 1.3, 3.2, ((1.0, 1.0), (2.0, 0.5), (2.76, 0.35), (5.4, 0.15))))])
    else:
        buf = mix([(int(_RATE * 0.22 * k), tone(988.0, 0.14, 22.0)) for k in range(3)])
    peak = max(1e-9, max(abs(x) for x in buf))
    pcm = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, x / peak * amp)) * 32767)) for x in buf)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(pcm)
    return bio.getvalue()


class ImportantQueue:
    """Avisos Importantes pendentes (só memória). Cada item: {id, kind, event|None, title, body, ign, fired_at, repeats, acked, created}.
    event None = o aviso é um estado (a pergunta do card), reenviado pelo servidor; ign = ignora o modo foco."""

    def __init__(self, clock=time.time):
        self.clock, self.items = clock, {}

    def add(self, id, kind, event, title, body, ign):
        if id in self.items:
            return self.items[id]
        it = {"id": id, "kind": kind, "event": event, "title": title, "body": body, "ign": bool(ign),
              "fired_at": None, "repeats": 0, "acked": False, "created": self.clock()}
        self.items[id] = it
        return it

    def due(self, focus_active):
        """[(fire|repeat, item)]: item novo sai já (ou ao acabar o foco, se não ignora o foco); sem toque, repete 1 vez em 15 min."""
        now, out = self.clock(), []
        for k in [k for k, it in self.items.items() if it["acked"] or now - it["created"] > MAX_PENDING_S]:
            del self.items[k]
        for it in self.items.values():
            if focus_active and not it["ign"]:
                continue
            if it["fired_at"] is None:
                out.append(("fire", it))
            elif it["repeats"] < 1 and now - it["fired_at"] >= REPEAT_S:
                out.append(("repeat", it))
        return out

    def mark(self, it, act):
        it["fired_at"] = self.clock()
        if act == "repeat":
            it["repeats"] += 1

    def ack(self, id=None, prefix=None):
        n = 0
        for k, it in self.items.items():
            if (id is not None and k == id) or (prefix is not None and k.startswith(prefix)):
                it["acked"] = True
                n += 1
        return n

    def pending_events(self):
        """Eventos já entregues e ainda sem toque, para quem conectar depois."""
        return [{**it["event"], "repeat": it["repeats"]} for it in self.items.values()
                if it["event"] and it["fired_at"] is not None and not it["acked"]]

    def ask_info(self, prefix="ask:"):
        """(n_repetições, ignora_foco) do item pendente da pergunta, ou None."""
        for k, it in self.items.items():
            if k.startswith(prefix) and not it["acked"]:
                return it["repeats"], it["ign"], it["fired_at"] is not None
        return None
