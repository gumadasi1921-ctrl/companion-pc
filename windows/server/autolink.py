"""Ligação automática job ↔ card do Trello (SOMENTE LEITURA no Trello; só grava em work_rules.json via WorkTracker).

Regras (sem heurística: só igualdade sem maiúsculas/acentos/espaços repetidos, via WorkTracker.card_match):
- exato: o arquivo em foco é de um cliente+job do Trabalho, o job ainda não tem ligação e existe UM ÚNICO card em Em execução ou
  A fazer (de hoje) cujo "CLIENTE - JOB" casa com cliente E job, e esse card não está ligado a outro job -> liga sozinho (auto=True).
- pergunta: senão, depois de 2 min seguidos no mesmo job (sem ligação, sem "Nenhum" e sem "Depois" vivo), 1 pergunta pendente com até 3
  cards do mesmo cliente (Em execução antes de A fazer; cards já ligados a outro job ficam de fora). Sem candidato = sem pergunta.
- "Nenhum" (work_rules.json "nolink", sem until) vale para sempre e só o painel desfaz; "Depois" (until) = 30 min.
- monday: a fonte da lista (cards_fn) junta Trello e monday; item do monday entra com short "m:<id>" e a ligação vai para work.mlinks.
- Ligação existente nunca é trocada. Nenhum nome vai para o log.
"""
import hashlib
import time

from trello import split_name
from work import NO_JOB, norm_key


def _cj(card_name):
    """(cliente, job) normalizados de "CLIENTE - JOB"; (None, None) sem o formato. Sem catálogo (varrer pastas a cada 2 s seria caro):
    o cliente/job em foco já são do catálogo, então igualdade com eles basta."""
    cl, jb = split_name(card_name)
    return (norm_key(cl), norm_key(jb)) if cl is not None else (None, None)

ASK_AFTER_S = 120
LATER_S = 30 * 60
STEP_MAX_S = 10          # um intervalo maior entre leituras não conta como tempo contínuo
MAX_CANDS = 3
FILE_MAX = 60
STAGES = ("running", "todo")
MD = "m:"               # prefixo do id de item do monday dentro de "short" (nunca colide com os 8 caracteres do Trello)


def qid_of(client, job):
    return hashlib.sha1(f"{client.casefold()}\t{job.casefold()}".encode("utf-8")).hexdigest()[:12]


class AutoLink:
    def __init__(self, work, cards_fn, clock=time.time):
        self.work, self.cards_fn, self.clock = work, cards_fn, clock
        self.key = None          # (cliente, job) casefold em foco
        self.secs = 0.0
        self.last = None
        self.pending = None      # {id, client, job, file, cards[{short,name}]}

    # -- leitura do foco (work_loop, a cada ~2 s, fila de disco) --------------------------
    def observe(self, info, now=None):
        """Devolve None ou {"kind": autolink|ask|clear, ...} (o servidor avisa os aparelhos)."""
        now = self.clock() if now is None else now
        w = self.work
        client, job = info.get("client"), info.get("job")
        ok = bool(info.get("app") and info.get("identified") and client and job and job != NO_JOB)
        key = (client.casefold(), job.casefold()) if ok else None
        out = None
        if key != self.key:
            self.key, self.secs = key, 0.0
            if self.pending:
                self.pending, out = None, {"kind": "clear"}
        elif key and self.last is not None and info.get("counting"):
            self.secs += min(max(now - self.last, 0.0), STEP_MAX_S)
        self.last = now
        if not key:
            return out
        if any(w._jkey(x["client"], x["job"]) == key for x in list(w.links) + list(w.mlinks)):
            if self.pending:
                self.pending, out = None, {"kind": "clear"}
            return out
        if self._blocked(client, job, now):
            return out
        cards = self.cards_fn() or []
        if not cards:
            return out
        linked = {x["card"] for x in w.links} | {MD + x["item"] for x in w.mlinks}
        exact = []
        for c in cards:
            if c.get("cat") not in STAGES:
                continue
            if _cj(c["name"]) == (norm_key(client), norm_key(job)):
                exact.append(c)
        if len(exact) == 1 and exact[0]["short"] not in linked:
            c = exact[0]
            if c["short"].startswith(MD):
                r = w.mlink_set(client, job, c["short"][len(MD):], c["name"], auto=True)
            else:
                r = w.link_set(client, job, c["short"], c["name"], auto=True)
            self.pending = None
            return {"kind": "autolink", "id": r["link"]["id"], "client": r["link"]["client"], "job": r["link"]["job"],
                    "card_name": c["name"], "src": "md" if c["short"].startswith(MD) else "tr", "clear": True}
        if self.pending or self.secs < ASK_AFTER_S:
            return out
        cands = []
        for stage in STAGES:
            for c in cards:
                if c.get("cat") == stage and c["short"] not in linked:
                    if _cj(c["name"])[0] == norm_key(client):
                        cands.append({"short": c["short"], "name": c["name"]})
        if not cands:
            return out
        f = str(info.get("file") or "")
        self.pending = {"id": qid_of(client, job), "client": client, "job": job,
                        "file": f if len(f) <= FILE_MAX else f[:FILE_MAX - 1] + "…", "cards": cands[:MAX_CANDS]}
        return {"kind": "ask", **self.public()}

    def _blocked(self, client, job, now):
        k = self.work._jkey(client, job)
        return any(self.work._jkey(x["client"], x["job"]) == k and (x.get("until") is None or x["until"] > now)
                   for x in self.work.nolink)

    # -- estado para os aparelhos --------------------------------------------------------
    def public(self):
        p = self.pending
        return {"id": p["id"], "client": p["client"], "file": p["file"], "cards": p["cards"]} if p else None

    # -- respostas (o celular manda só qid + card) ---------------------------------------
    def _take(self, qid):
        p = self.pending
        if not p or p["id"] != qid:
            raise ValueError("essa pergunta já foi respondida")
        return p

    def pick(self, qid, short, has_card):
        p = self._take(qid)
        c = next((x for x in p["cards"] if x["short"] == short), None)
        if not c or not has_card(short):
            raise ValueError("card inválido")
        if short.startswith(MD):
            r = self.work.mlink_set(p["client"], p["job"], short[len(MD):], c["name"])
        else:
            r = self.work.link_set(p["client"], p["job"], short, c["name"])
        self.pending = None
        return {"link": r["link"]}

    def none(self, qid):
        p = self._take(qid)
        self.work.nolink_set(p["client"], p["job"], None)
        self.pending = None
        return {"ok": True}

    def later(self, qid, now=None):
        p = self._take(qid)
        self.work.nolink_set(p["client"], p["job"], (self.clock() if now is None else now) + LATER_S)
        self.pending = None
        return {"ok": True}

    def undo(self, link_id):
        """Desfaz só ligação automática (a manual se apaga pelo painel)."""
        x = next((x for x in self.work.links if x["id"] == link_id and x.get("auto")), None)
        if x:
            return self.work.link_delete(link_id)
        if any(m["id"] == link_id and m.get("auto") for m in self.work.mlinks):
            return self.work.mlink_delete(link_id)
        raise ValueError("ligação não encontrada")
