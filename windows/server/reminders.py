"""Lembretes (reminders.json, ao lado do config.json). Separado de Tarefas. O relógio da verdade é o servidor.

Cada lembrete: texto (≤120), `at` (data/hora LOCAL do PC, "AAAA-MM-DD HH:MM"), repetição none|daily|weekly|weekdays e nível
low|normal|high. `next` = epoch do próximo disparo. Regras:
- low/normal: só avisam se o disparo chega na hora (atraso ≤ GRACE_S) E há aparelho conectado; senão ficam "perdido" na lista
  (um lembrete repetido só pula para a próxima ocorrência). Nunca avisam depois, nunca inventam.
- high: toca (`ringing`) e fica pendente no servidor, mesmo sem aparelho ou depois de reiniciar, até done() ou snooze().
- snooze(): adia o aviso de agora (10 min por padrão), sem mudar a agenda de repetição.
Roda na fila de disco do servidor (sem locks). Relógio injetável (testes). Texto nunca vai ao log.
"""
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("companion.reminders")

REPEATS = ("none", "daily", "weekly", "weekdays")
LEVELS = ("low", "normal", "high")
MAX_ITEMS = 100
MAX_TEXT = 120
GRACE_S = 120
KEEP_DONE_S = 7 * 86400
SNOOZE_MIN = (5, 10, 15, 30, 60)
_AT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_ID = re.compile(r"^[0-9a-f]{8}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def parse_at(s):
    if not isinstance(s, str) or not _AT.match(s):
        raise ValueError("data/hora inválida")
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("data/hora inválida") from None


def to_epoch(dt):
    return dt.timestamp()


def next_after(at, repeat, after):
    """Primeira ocorrência de (at, repeat) com epoch > after. Sem repetição: o próprio at, se > after, senão None."""
    base = parse_at(at)
    if repeat == "none":
        return to_epoch(base) if to_epoch(base) > after else None
    day = max(base.date(), datetime.fromtimestamp(after).date())
    for _ in range(9):
        cand = datetime.combine(day, base.time())
        ok = (repeat == "daily" or (repeat == "weekly" and cand.weekday() == base.weekday())
              or (repeat == "weekdays" and cand.weekday() < 5))
        if ok and cand >= base and to_epoch(cand) > after:
            return to_epoch(cand)
        day += timedelta(days=1)
    return None


def clean(text, at, repeat, level):
    if not isinstance(text, str) or _CTRL.search(text) or not text.strip() or len(text.strip()) > MAX_TEXT:
        raise ValueError("texto inválido (1 a 120 caracteres)")
    parse_at(at)
    if repeat not in REPEATS:
        raise ValueError("repetição inválida")
    if level not in LEVELS:
        raise ValueError("nível inválido")
    return text.strip(), at, repeat, level


class Reminders:
    def __init__(self, base: Path, clock=time.time):
        self.path = Path(base) / "reminders.json"
        self.clock = clock
        self.items = []
        self._load()

    # -------------------------------------------------------------- disco
    def _load(self):
        if not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or d.get("v") != 1 or not isinstance(d.get("items"), list):
                raise ValueError("estrutura inesperada")
            items = []
            for r in d["items"]:
                text, at, rep, lvl = clean(r["text"], r["at"], r["repeat"], r["level"])
                if not _ID.match(str(r["id"])):
                    raise ValueError("id inválido")
                items.append({"id": r["id"], "text": text, "at": at, "repeat": rep, "level": lvl,
                              "next": float(r["next"]) if r.get("next") is not None else None,
                              "state": r["state"] if r["state"] in ("active", "ringing", "done", "missed") else "active",
                              "ended": float(r["ended"]) if r.get("ended") else None})
            self.items = items
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("reminders.json ilegível: guardando cópia .bad e começando vazio")
            try:
                bad = self.path.with_name("reminders.json.bad-" + time.strftime("%Y%m%d-%H%M%S"))
                os.replace(self.path, bad)
            except OSError:
                pass
            self.items = []

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"v": 1, "items": self.items}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # -------------------------------------------------------------- consulta
    def _find(self, rid):
        for r in self.items:
            if r["id"] == rid:
                return r
        raise ValueError("lembrete não encontrado")

    def state(self):
        """Lista para os aparelhos: tocando primeiro, depois os ativos por horário, depois os encerrados (mais recentes antes).
        `when`/`local_now` são hora LOCAL DO PC ("AAAA-MM-DD HH:MM"): o aparelho nunca converte fuso."""
        order = {"ringing": 0, "active": 1}
        live = sorted((r for r in self.items if r["state"] in order), key=lambda r: (order[r["state"]], r["next"] or 0))
        end = sorted((r for r in self.items if r["state"] not in order), key=lambda r: -(r["ended"] or 0))
        fmt = lambda e: datetime.fromtimestamp(e).strftime("%Y-%m-%d %H:%M") if e else None   # noqa: E731
        now = self.clock()
        return {"items": [{**r, "when": fmt(r["next"]), "ended_at": fmt(r["ended"])} for r in live + end], "now": now,
                "local_now": fmt(now), "ringing": [r["id"] for r in self.items if r["state"] == "ringing"]}

    def next_due(self):
        """Segundos até o próximo disparo (>= 0), ou None."""
        t = [r["next"] for r in self.items if r["state"] == "active" and r["next"] is not None]
        return max(0.0, min(t) - self.clock()) if t else None

    # -------------------------------------------------------------- edição
    def add(self, text, at, repeat="none", level="normal"):
        text, at, repeat, level = clean(text, at, repeat, level)
        if len(self.items) >= MAX_ITEMS:
            raise ValueError("no máximo 100 lembretes")
        nxt = next_after(at, repeat, self.clock() - 60)
        if nxt is None:
            raise ValueError("a data/hora já passou")
        r = {"id": secrets.token_hex(4), "text": text, "at": at, "repeat": repeat, "level": level, "next": nxt, "state": "active", "ended": None}
        self.items.append(r)
        self._save()
        return dict(r)

    def edit(self, rid, text, at, repeat, level):
        r = self._find(rid)
        text, at, repeat, level = clean(text, at, repeat, level)
        nxt = next_after(at, repeat, self.clock() - 60)
        if nxt is None:
            raise ValueError("a data/hora já passou")
        r.update(text=text, at=at, repeat=repeat, level=level, next=nxt, state="active", ended=None)
        self._save()
        return dict(r)

    def remove(self, rid):
        r = self._find(rid)
        self.items.remove(r)
        self._save()
        return dict(r)

    def restore(self, entry):
        """Desfazer de apagar: volta o lembrete como estava (id, estado e horário)."""
        text, at, rep, lvl = clean(entry.get("text"), entry.get("at"), entry.get("repeat"), entry.get("level"))
        if not _ID.match(str(entry.get("id"))) or any(x["id"] == entry["id"] for x in self.items) or len(self.items) >= MAX_ITEMS:
            raise ValueError("não deu para desfazer")
        r = {"id": entry["id"], "text": text, "at": at, "repeat": rep, "level": lvl, "next": entry.get("next"),
             "state": entry.get("state") if entry.get("state") in ("active", "ringing", "done", "missed") else "active", "ended": entry.get("ended")}
        self.items.append(r)
        self._save()
        return dict(r)

    def clear_ended(self):
        n = len(self.items)
        self.items = [r for r in self.items if r["state"] in ("active", "ringing")]
        self._save()
        return n - len(self.items)

    # -------------------------------------------------------------- aviso
    def _advance(self, r, now):
        """Depois de avisar/encerrar: repetido vai para a próxima ocorrência; único termina."""
        nxt = next_after(r["at"], r["repeat"], now)
        if nxt is None:
            r.update(state="done", next=None, ended=now)
        else:
            r.update(state="active", next=nxt)

    def tick(self, online):
        """Processa o que venceu. Devolve a lista de eventos a avisar agora ({id,text,level,repeat,ringing})."""
        now = self.clock()
        out = []
        changed = False
        for r in self.items:
            if r["state"] != "active" or r["next"] is None or r["next"] > now:
                continue
            late = now - r["next"]
            changed = True
            if r["level"] == "high":
                r["state"] = "ringing"
                out.append({"id": r["id"], "text": r["text"], "level": "high", "repeat": r["repeat"], "ringing": True})
            elif online and late <= GRACE_S:
                out.append({"id": r["id"], "text": r["text"], "level": r["level"], "repeat": r["repeat"], "ringing": False})
                self._advance(r, now)
            else:   # sem aparelho (ou servidor desligado na hora): perdido, nunca avisa depois
                if r["repeat"] == "none":
                    r.update(state="missed", next=None, ended=now)
                else:
                    self._advance(r, now)
        keep = [r for r in self.items if not (r["state"] in ("done", "missed") and now - (r["ended"] or now) > KEEP_DONE_S)]
        if len(keep) != len(self.items):
            changed, self.items = True, keep
        if changed:
            self._save()
        return out

    def done(self, rid):
        r = self._find(rid)
        if r["state"] == "ringing" or r["state"] == "active":
            self._advance(r, self.clock())
        self._save()
        return dict(r)

    def snooze(self, rid, minutes=10):
        if minutes not in SNOOZE_MIN:
            raise ValueError("adiar por 5, 10, 15, 30 ou 60 minutos")
        r = self._find(rid)
        if r["state"] not in ("ringing", "active"):
            raise ValueError("esse lembrete já terminou")
        r.update(state="active", next=self.clock() + minutes * 60)
        self._save()
        return dict(r)
