"""Retrospectiva do mês ("stories"): monta, só com o que JÁ EXISTE no histórico do PC, as telas do mês pedido.
Nada é estimado: uma tela sem dado simplesmente não existe, e o mês sem nada devolve slides vazios.
Fontes (todas já gravadas por outros módulos): WorkTracker.month, Pomodoro.history (só contagem de focos), CoWork.days (Rendeu/Travei/Me
distraí), cards do Trello/monday concluídos por dia e as partidas de Dota arquivadas aqui (dota-month.json: o OpenDota só devolve as
últimas 10, então o arquivo só tem partidas vistas desde que o arquivamento começou).
Nomes (cliente/job) só vão ao celular dentro de recap.get; nunca ao log. O cartão de imagem é desenhado no aparelho."""
import json
import os
import threading
from datetime import datetime

TOP = 5
MAX_MATCHES = 6000


def _month_days(d, ym):
    return {k: v for k, v in (d or {}).items() if isinstance(k, str) and k.startswith(ym)}


def _best(per_day, minimum=1):
    """(dia, valor) do maior valor do mês, ou None. Empate = o dia mais antigo."""
    rows = [(k, v) for k, v in per_day.items() if isinstance(v, (int, float)) and v >= minimum]
    if not rows:
        return None
    day, v = sorted(rows, key=lambda r: (-r[1], r[0]))[0]
    return {"date": day, "n": int(v)}


# ---- Dota: arquivo das partidas (só o que o OpenDota informa) -----------------------------------------------------------
class DotaMonth:
    """dota-month.json {v:1, m:{match_id: {w, h, k, d, a, t, day}}}. w True/False/None, h herói, KDA, t duração em s, day AAAA-MM-DD local."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.m = self._load()

    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            m = d.get("m") if isinstance(d, dict) and d.get("v") == 1 else None
            return {str(k): v for k, v in m.items() if isinstance(v, dict) and isinstance(v.get("day"), str)} if isinstance(m, dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def add(self, recent):
        """Guarda as partidas novas de recentMatches (já filtradas por dota_od.trim_recent). Devolve quantas entraram."""
        new = 0
        with self.lock:
            for r in recent or []:
                try:
                    mid, start = r.get("match_id"), r.get("start")
                    if isinstance(mid, bool) or not isinstance(mid, int) or isinstance(start, bool) or not isinstance(start, int) or start <= 0:
                        continue
                    key = str(mid)
                    if key in self.m:
                        continue
                    ent = {"day": datetime.fromtimestamp(start).strftime("%Y-%m-%d")}
                    for src, dst in (("win", "w"), ("hero_id", "h"), ("k", "k"), ("d", "d"), ("a", "a"), ("duration_s", "t")):
                        v = r.get(src)
                        if (src == "win" and isinstance(v, bool)) or (src != "win" and isinstance(v, int) and not isinstance(v, bool) and v >= 0):
                            ent[dst] = v
                    self.m[key] = ent
                    new += 1
                except (AttributeError, TypeError, ValueError, OSError, OverflowError):
                    continue
            if new:
                for k in sorted(self.m, key=lambda x: (self.m[x]["day"], x))[:-MAX_MATCHES]:
                    del self.m[k]
                self._save()
        return new

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"v": 1, "m": self.m}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self.path)

    def month(self, ym):
        with self.lock:
            return [dict(v) for v in self.m.values() if v["day"].startswith(ym)]


# ---- montagem ---------------------------------------------------------------------------------------------------------
def build(ym, work_month=None, pomo_hist=None, cowork_days=None, trello_by_day=None, monday_by_day=None, dota_matches=None,
          hero_name=None):
    """{ym, slides:[...], empty}. Cada slide tem `id` e só números/nomes que existem no histórico."""
    slides = []
    wm = work_month or {}
    total = int(wm.get("total_s") or 0)
    if total >= 60:
        best = _best({d["date"]: d["s"] for d in wm.get("days", []) if d.get("s", 0) >= 60}, 60)
        slides.append({"id": "open", "total_s": total, "days_worked": int(wm.get("days_worked") or 0),
                       "prev_total_s": int(wm["prev_total_s"]) if wm.get("has_prev") and wm.get("prev_total_s") else None,
                       "best_day": {"date": best["date"], "s": best["n"]} if best else None})
        clients = [c for c in wm.get("clients", []) if not c.get("unidentified") and c.get("s", 0) >= 60][:TOP]
        if clients:
            slides.append({"id": "clients", "items": [{"name": c["client"], "s": int(c["s"])} for c in clients]})
        jobs = sorted(((c["client"], j["job"], int(j["s"])) for c in wm.get("clients", []) if not c.get("unidentified")
                       for j in c.get("jobs", []) if j.get("s", 0) >= 60), key=lambda r: -r[2])[:TOP]
        if jobs:
            slides.append({"id": "jobs", "items": [{"client": c, "job": j, "s": s} for c, j, s in jobs]})
    foco = {k: v for k, v in _month_days(pomo_hist, ym).items() if isinstance(v, int) and v > 0}
    if foco:
        slides.append({"id": "focus", "n": sum(foco.values()), "days": len(foco), "best_day": _best(foco)})
    ck = {"r": 0, "t": 0, "d": 0}
    for jobs in _month_days(cowork_days, ym).values():
        for c in (jobs or {}).values():
            for a in ck:
                ck[a] += int(c.get(a, 0) or 0)
    if sum(ck.values()) > 0:
        slides.append({"id": "checkins", **ck, "total": sum(ck.values())})
    tr, md = _month_days(trello_by_day, ym), _month_days(monday_by_day, ym)
    if sum(tr.values()) + sum(md.values()) > 0:
        both = {d: tr.get(d, 0) + md.get(d, 0) for d in set(tr) | set(md)}
        slides.append({"id": "done", "trello": sum(tr.values()), "monday": sum(md.values()), "total": sum(both.values()), "best_day": _best(both)})
    ms = dota_matches or []
    if ms:
        known = [m for m in ms if "w" in m]
        heroes = {}
        for m in ms:
            if "h" in m:
                heroes[m["h"]] = heroes.get(m["h"], 0) + 1
        top = sorted(heroes.items(), key=lambda kv: (-kv[1], kv[0]))[:1]
        name = None
        if top and hero_name:
            try:
                name = hero_name(top[0][0])
            except Exception:  # noqa: BLE001
                name = None
        slides.append({"id": "dota", "matches": len(ms), "wins": sum(1 for m in known if m["w"]), "losses": sum(1 for m in known if not m["w"]),
                       "minutes": sum(m.get("t", 0) for m in ms) // 60,
                       "top_hero": {"id": top[0][0], "name": name, "n": top[0][1]} if top else None})
    return {"ym": ym, "slides": slides, "empty": not slides}
