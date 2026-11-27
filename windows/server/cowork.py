"""Trabalhando junto (body doubling): o Lume acompanha o foco do Pomodoro com o job real em foco no PC.

Tudo na fila de disco do servidor (sem locks), relógio injetável. Só duas coisas ficam no disco, em cowork.json
(ao lado do config.json, atômico; corrompido -> .bad-<data-hora>): contadores {r, t, d} (Rendeu, Travei, Me distraí)
por dia e por job ("cliente\\tjob", ou "" sem job identificado). Sem texto livre. O tempo do foco por job e a
sessão ficam só em memória; nenhum nome vai ao log.

Tempo no job = mesmo critério do WorkTracker.poll (app de trabalho em foco, input recente, "Não é trabalho" fora),
somado só enquanto o foco está RODANDO. Sem job identificado, só o programa (nunca nome de arquivo).
"""
import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("companion.cowork")

ANSWERS = ("r", "t", "d")
EVERY_OPTS = (0, 15)         # 0 = só no fim de cada foco; 15 = a cada 15 min de foco rodando (e no fim se não respondeu)
GAP_S = 5                    # intervalo máximo entre leituras para somar (o work_loop lê a cada 2 s)
KEEP_DAYS = 400
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def clean_every(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v in EVERY_OPTS else 0


def job_key(client, job):
    return f"{client or ''}\t{job}" if job else ""


class CoWork:
    def __init__(self, base: Path, clock=time.time):
        self.path = Path(base) / "cowork.json"
        self.clock = clock
        self.days = {}
        self._load()
        self.sess = None          # {secs{(client,job):s}, apps{app:s}, run_s, last_t, next_ask, answered}
        self.ask = None           # {id, end} pendente
        self.summary = None       # {min, job, app, answer} do último foco concluído (até o próximo foco)
        self._id = 0
        self._last_key = ""
        self.cur = {"job": None, "app": None}

    # -- disco ----------------------------------------------------------------
    def _load(self):
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or d.get("v") != 1 or not isinstance(d.get("days"), dict):
                raise ValueError("estrutura inesperada")
            for day, jobs in d["days"].items():
                if not _DAY.match(day) or not isinstance(jobs, dict):
                    raise ValueError("dia inválido")
                for k, c in jobs.items():
                    if not isinstance(k, str) or not isinstance(c, dict) or not all(
                            isinstance(c.get(a, 0), int) and not isinstance(c.get(a, 0), bool) and c.get(a, 0) >= 0
                            for a in ANSWERS):
                        raise ValueError("contagem inválida")
            self.days = d["days"]
        except (OSError, ValueError, TypeError) as e:
            bad = self.path.with_name(f"{self.path.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}")
            try:
                self.path.replace(bad)
                log.warning("cowork.json inválido (%s): renomeado, começando do zero", type(e).__name__)
            except OSError as e2:
                log.warning("cowork.json inválido (%s) e não consegui renomear: %s", type(e).__name__, type(e2).__name__)

    def _save(self):
        for k in sorted(self.days)[:-KEEP_DAYS]:
            del self.days[k]
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"v": 1, "days": self.days}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, key, answer, now=None):
        if answer not in ANSWERS:
            raise ValueError("Resposta inválida.")
        day = time.strftime("%Y-%m-%d", time.localtime(self.clock() if now is None else now))
        c = self.days.setdefault(day, {}).setdefault(key, {})
        c[answer] = c.get(answer, 0) + 1
        self._save()

    def day_counts(self, day=None):
        """Totais do dia (todos os jobs): {r, t, d}. Só leitura, para o painel/testes."""
        day = day or time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        out = {a: 0 for a in ANSWERS}
        for c in self.days.get(day, {}).values():
            for a in ANSWERS:
                out[a] += c.get(a, 0)
        return out

    # -- sessão do foco ---------------------------------------------------------
    def sync(self, phase, running, on):
        """Chamado a cada mudança do Pomodoro. A sessão nasce quando o foco roda e morre quando a fase sai do foco
        (sem resumo: foco pulado/zerado não conta). Desligado = nada."""
        if not on:
            self.sess = self.ask = self.summary = None
            return
        if phase != "focus":
            if self.sess:                    # saiu do foco sem concluir (pulou/zerou): nada é guardado
                self.sess = self.ask = self.summary = None
            elif phase == "idle":
                self.ask = self.summary = None
            return
        if self.sess is None and running:
            self.sess = {"secs": {}, "apps": {}, "run_s": 0.0, "last_t": None, "next_ask": None, "answered": None}
            self.summary, self.ask = None, None

    def step(self, info, phase, running, on, every, now=None):
        """Leitura do work_loop (2 s). Soma o tempo do job só com o foco rodando; cria o check-in periódico."""
        now = self.clock() if now is None else now
        self.sync(phase, running, on)
        s = self.sess
        if info and info.get("identified") and info.get("job") and info.get("counting") and not info.get("ignored"):
            self.cur = {"job": info["job"], "app": info.get("app")}
        elif info and info.get("app") and info.get("counting") and not info.get("ignored"):
            self.cur = {"job": None, "app": info["app"]}
        else:
            self.cur = {"job": None, "app": None}
        if s is None:
            return
        if not running:
            s["last_t"] = None
            return
        last, s["last_t"] = s["last_t"], now
        if last is None or not 0 < now - last <= GAP_S:
            return
        dt = now - last
        s["run_s"] += dt
        if self.cur["app"]:
            s["apps"][self.cur["app"]] = s["apps"].get(self.cur["app"], 0) + dt
            if self.cur["job"]:
                k = (info.get("client"), info["job"])
                s["secs"][k] = s["secs"].get(k, 0) + dt
        if every and self.ask is None and s["answered"] is None:
            if s["next_ask"] is None:
                s["next_ask"] = every * 60
            if s["run_s"] >= s["next_ask"]:
                self._id += 1
                self.ask = {"id": self._id, "end": False}

    def _top(self, s):
        if s["secs"]:
            k = max(s["secs"], key=lambda x: s["secs"][x])
            return k, s["secs"][k]
        return None, 0

    def focus_done(self):
        """O foco acabou e contou: monta o resumo e (se ainda sem resposta) pede o check-in do fim. Desligado = nada."""
        s = self.sess
        if s is None:
            return
        (k, secs) = self._top(s)
        app = max(s["apps"], key=lambda x: s["apps"][x]) if s["apps"] else None
        tot = sum(s["apps"].values())
        self.summary = {"min": int(secs // 60) if k else int(tot // 60), "job": k[1] if k else None,
                        "app": None if k else app, "answer": s["answered"]}
        self.sess = None
        self._last_key = job_key(*k) if k else ""
        if s["answered"] is None:
            self._id += 1
            self.ask = {"id": self._id, "end": True}
        else:
            self.ask = None

    def answer(self, ask_id, value):
        """Grava a resposta do check-in pendente (id conferido; valor da lista fechada). Devolve True se gravou."""
        if value not in ANSWERS:
            raise ValueError("Resposta inválida.")
        a = self.ask
        if not a or a["id"] != ask_id:
            return False
        s = self.sess
        if s is not None:       # durante o foco: o job com mais tempo, senão o job em foco agora
            k, _ = self._top(s)
            key = job_key(*k) if k else (job_key(None, self.cur["job"]) if self.cur["job"] else "")
            s["answered"] = value
        else:                   # no fim: o job do resumo
            key = self._last_key
            if self.summary:
                self.summary["answer"] = value
        self.record(key, value)
        self.ask = None
        return True

    # -- o que vai ao aparelho ----------------------------------------------------
    def public(self, phase, running, on, every):
        s = self.sess
        out = {"on": bool(on), "every": every, "ask": dict(self.ask) if self.ask else None,
               "summary": dict(self.summary) if self.summary else None, "live": None}
        if on and s is not None and phase == "focus":
            job, app = self.cur["job"], self.cur["app"]
            k = None
            if job:
                k = max((x for x in s["secs"] if x[1] == job), key=lambda x: s["secs"][x], default=None)
            secs = s["secs"].get(k, 0) if k else sum(s["apps"].values())
            out["live"] = {"running": bool(running), "job": job, "app": None if job else app, "min": int(secs // 60),
                           "answered": s["answered"]}
        return out
