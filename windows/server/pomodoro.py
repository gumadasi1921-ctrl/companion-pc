"""Pomodoro (pomodoro.json, ao lado do config.json). O relógio da verdade é o servidor.

Fases: focus | short | long | idle. Uma fase "pronta" é uma fase com running=False e sem remaining_s
(usa a duração cheia); "pausada" tem remaining_s. Roda sempre na fila de disco do servidor (IO_POOL),
então não há locks. O relógio é injetável (testes). O id da tarefa vai ao estado, nunca o texto, e nada
disso vai ao log.
"""
import json
import logging
import re
import time
from pathlib import Path

from alerts import valid_focus_except
from cowork import EVERY_OPTS

log = logging.getLogger("companion.pomodoro")

LIMITS = {"focus_min": (5, 90), "short_min": (1, 60), "long_min": (1, 60), "long_every": (2, 8)}
DEFAULT_CFG = {"focus_min": 25, "short_min": 5, "long_min": 15, "long_every": 4, "auto_next": False,
               "focus_mode": True, "focus_except": [], "cowork": True, "checkin_min": 0}
PHASES = ("focus", "short", "long", "idle")
_ID = re.compile(r"^[0-9a-f]{8}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HIST_DAYS = 120
_UNSET = object()


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Pomodoro:
    def __init__(self, base: Path, clock=time.time):
        self.path = Path(base) / "pomodoro.json"
        self.clock = clock
        self.cfg = dict(DEFAULT_CFG)
        self.phase = "idle"
        self.running = False
        self.ends_at = None
        self.remaining_s = None
        self.cycle = 0
        self.task_id = None
        self.history = {}
        self.restored_expired = False   # True se a fase venceu com o servidor desligado (concluída sem aviso)
        self._load()

    # -------------------------------------------------------------- disco
    def _load(self):
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or d.get("v") != 1:
                raise ValueError("estrutura inesperada")
            cfg = dict(DEFAULT_CFG)
            for k, (lo, hi) in LIMITS.items():
                v = d["cfg"][k]
                if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                    raise ValueError("config inválida")
                cfg[k] = v
            if not isinstance(d["cfg"]["auto_next"], bool):
                raise ValueError("config inválida")
            cfg["auto_next"] = d["cfg"]["auto_next"]
            fm, fe = d["cfg"].get("focus_mode", True), d["cfg"].get("focus_except", [])   # arquivos antigos não têm
            if isinstance(fm, bool):
                cfg["focus_mode"] = fm
            cw, ck = d["cfg"].get("cowork", True), d["cfg"].get("checkin_min", 0)
            if isinstance(cw, bool):
                cfg["cowork"] = cw
            if isinstance(ck, int) and not isinstance(ck, bool) and ck in EVERY_OPTS:
                cfg["checkin_min"] = ck
            if isinstance(fe, list) and all(isinstance(x, str) for x in fe):
                cfg["focus_except"] = fe[:20]
            st = d["state"]
            if st["phase"] not in PHASES or not isinstance(st["running"], bool):
                raise ValueError("estado inválido")
            if not isinstance(st["cycle"], int) or not 0 <= st["cycle"] < cfg["long_every"]:
                raise ValueError("ciclo inválido")
            for k in ("ends_at", "remaining_s"):
                if st[k] is not None and not _is_num(st[k]):
                    raise ValueError("estado inválido")
            if st["running"] and (st["ends_at"] is None or st["phase"] == "idle"):
                raise ValueError("estado inválido")
            tid = st.get("task_id")
            if tid is not None and not (isinstance(tid, str) and _ID.match(tid)):
                raise ValueError("estado inválido")
            hist = d["history"]
            if not isinstance(hist, dict) or not all(_DAY.match(k) and isinstance(v, int) and not isinstance(v, bool)
                                                     and v >= 0 for k, v in hist.items()):
                raise ValueError("histórico inválido")
        except (OSError, ValueError, KeyError, TypeError) as e:
            bad = self.path.with_name(f"{self.path.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}")
            try:
                self.path.replace(bad)
                log.warning("pomodoro.json inválido (%s): renomeado para %s, começando do zero", type(e).__name__, bad.name)
            except OSError as e2:
                log.warning("pomodoro.json inválido (%s) e não consegui renomear: %s", type(e).__name__, type(e2).__name__)
            return
        self.cfg, self.phase, self.running = cfg, st["phase"], st["running"]
        self.ends_at, self.remaining_s, self.cycle, self.task_id = st["ends_at"], st["remaining_s"], st["cycle"], tid
        self.history = hist
        if self.running and self.clock() >= self.ends_at:   # venceu enquanto o servidor estava desligado
            self._finish(counted=True, start_next=False, at=self.ends_at)
            self.restored_expired = True
            self._save()

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        data = {"v": 1, "cfg": self.cfg,
                "state": {"phase": self.phase, "running": self.running, "ends_at": self.ends_at,
                          "remaining_s": self.remaining_s, "cycle": self.cycle, "task_id": self.task_id},
                "history": self.history}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # -------------------------------------------------------------- cálculos
    def _dur(self, phase):
        return {"focus": self.cfg["focus_min"], "short": self.cfg["short_min"], "long": self.cfg["long_min"],
                "idle": self.cfg["focus_min"]}[phase] * 60

    def _day(self, ts):
        return time.strftime("%Y-%m-%d", time.localtime(ts))

    def _count(self, ts):
        d = self._day(ts)
        self.history[d] = self.history.get(d, 0) + 1
        for k in sorted(self.history)[:-HIST_DAYS]:
            del self.history[k]

    def _next_after(self, phase):
        """Fase seguinte ao fim (ou pulo) de `phase`; ajusta o ciclo. Contagem do foco é feita por quem chama."""
        if phase == "focus":
            n = self.cycle + 1
            if n >= self.cfg["long_every"]:
                self.cycle = 0
                return "long"
            self.cycle = n
            return "short"
        return "focus"

    def _finish(self, counted, start_next, at):
        """Encerra a fase atual. Devolve o evento (fase que acabou e a próxima)."""
        ended = self.phase
        if ended == "focus" and counted:
            self._count(at)
        nxt = self._next_after(ended)
        self.phase, self.running, self.ends_at, self.remaining_s = nxt, False, None, None
        if start_next:
            self.running = True
            self.ends_at = self.clock() + self._dur(nxt)
        return {"finished": ended, "next": nxt, "next_min": self._dur(nxt) // 60, "auto": start_next}

    def today(self):
        return self.history.get(self._day(self.clock()), 0)

    def week(self):
        now = self.clock()
        return sum(self.history.get(self._day(now - i * 86400), 0) for i in range(7))

    # -------------------------------------------------------------- operações
    def get(self):
        now = self.clock()
        if self.running:
            rem = max(0.0, self.ends_at - now)
        else:
            rem = self.remaining_s if self.remaining_s is not None else self._dur(self.phase)
        return {"phase": self.phase, "running": self.running, "ends_at": self.ends_at if self.running else None,
                "remaining_s": round(rem, 1), "total_s": self._dur(self.phase), "cycle": self.cycle,
                "task_id": self.task_id, "config": dict(self.cfg), "today": self.today(), "week": self.week(),
                "server_now": now}

    def next_due(self):
        """Segundos até o fim da fase em andamento (para o único agendamento do servidor), ou None."""
        return max(0.0, self.ends_at - self.clock()) if self.running else None

    def tick(self):
        """Chamado no agendamento: conclui a fase se venceu. Devolve o evento ou None."""
        if not (self.running and self.clock() >= self.ends_at):
            return None
        ev = self._finish(counted=True, start_next=self.cfg["auto_next"], at=self.ends_at)
        self._save()
        return ev

    def start(self, task_id=_UNSET):
        if task_id is not _UNSET:
            if task_id is not None and not (isinstance(task_id, str) and _ID.match(task_id)):
                raise ValueError("Tarefa inválida.")
            self.task_id = task_id
        if not self.running:
            if self.phase == "idle":
                self.phase = "focus"
            rem = self.remaining_s if self.remaining_s is not None else self._dur(self.phase)
            self.running, self.ends_at, self.remaining_s = True, self.clock() + rem, None
        self._save()
        return self.get()

    def pause(self):
        if self.running:
            self.remaining_s = max(0.0, self.ends_at - self.clock())
            self.running, self.ends_at = False, None
            self._save()
        return self.get()

    def resume(self):
        if not self.running and self.phase != "idle":
            return self.start()
        return self.get()

    def skip(self):
        """Pula a fase (o foco pulado não conta). Com auto_next e o relógio rodando, a próxima já começa."""
        if self.phase == "idle":
            return self.get()
        self._finish(counted=False, start_next=self.cfg["auto_next"] and self.running, at=self.clock())
        self._save()
        return self.get()

    def reset(self):
        self.phase, self.running, self.ends_at, self.remaining_s = "idle", False, None, None
        self.cycle, self.task_id = 0, None
        self._save()
        return self.get()

    def set_config(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("Ajustes inválidos.")
        new = dict(self.cfg)
        for k, v in changes.items():
            if k == "auto_next":
                if not isinstance(v, bool):
                    raise ValueError("Valor inválido para começar a próxima fase sozinho.")
                new[k] = v
            elif k == "focus_mode":
                if not isinstance(v, bool):
                    raise ValueError("Valor inválido para o modo foco.")
                new[k] = v
            elif k == "cowork":
                if not isinstance(v, bool):
                    raise ValueError("Valor inválido para o Trabalhando junto.")
                new[k] = v
            elif k == "checkin_min":
                if not isinstance(v, int) or isinstance(v, bool) or v not in EVERY_OPTS:
                    raise ValueError("Intervalo do check-in inválido (fim do foco ou 15 min).")
                new[k] = v
            elif k == "focus_except":
                new[k] = valid_focus_except(v)
            elif k in LIMITS:
                lo, hi = LIMITS[k]
                if not _is_num(v) or int(v) != v:
                    raise ValueError("Use números inteiros.")
                if not lo <= v <= hi:
                    raise ValueError(f"Valor fora do limite ({lo} a {hi}).")
                new[k] = int(v)
            else:
                raise ValueError("Ajuste desconhecido.")
        self.cfg = new
        if self.cycle >= new["long_every"]:
            self.cycle = 0
        if not self.running and self.remaining_s is not None:   # pausada: não passa da nova duração
            self.remaining_s = min(self.remaining_s, self._dur(self.phase))
        self._save()
        return self.get()


def message(ev):
    """Texto do aviso do PC para o evento devolvido por tick()."""
    if ev["finished"] == "focus":
        if ev["next"] == "long":
            return f"Foco concluído! Pausa longa de {ev['next_min']} min"
        return f"Foco concluído! Pausa de {ev['next_min']} min"
    return "Pausa acabou, bora focar"
