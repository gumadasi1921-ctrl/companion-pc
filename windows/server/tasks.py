"""Lista de tarefas (tasks.json, ao lado do config.json).

Tudo é por id (o celular e o PC podem mexer ao mesmo tempo). Roda sempre na fila de disco
do servidor (IO_POOL), então não há locks aqui. O texto das tarefas nunca vai para o log.
"""
import json
import logging
import re
import secrets
import time
from pathlib import Path

log = logging.getLogger("companion.tasks")

MAX_TEXT = 200
MAX_ITEMS = 300
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


_ID = re.compile(r"^[0-9a-f]{8}$")  # secrets.token_hex(4)


def _clean(text):
    t = _CTRL.sub(" ", str(text if text is not None else "")).strip()
    t = re.sub(r" {2,}", " ", t)
    if not t:
        raise ValueError("Escreva o texto da tarefa.")
    if len(t) > MAX_TEXT:
        raise ValueError(f"A tarefa é longa demais (máximo {MAX_TEXT} caracteres).")
    return t


def _valid(data):
    return (isinstance(data, dict) and data.get("v") == 1 and isinstance(data.get("items"), list)
            and all(isinstance(i, dict) and isinstance(i.get("id"), str) and isinstance(i.get("text"), str)
                    and isinstance(i.get("done"), bool) for i in data["items"]))


class TaskList:
    def __init__(self, base: Path):
        self.path = Path(base) / "tasks.json"
        self.items = self._load()

    # -------------------------------------------------------------- disco
    def _load(self):
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not _valid(data):
                raise ValueError("estrutura inesperada")
            return [{"id": i["id"], "text": i["text"], "done": i["done"], "done_at": i.get("done_at")}
                    for i in data["items"]]
        except (OSError, ValueError) as e:
            bad = self.path.with_name(f"{self.path.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}")
            try:
                self.path.replace(bad)
                log.warning("tasks.json inválido (%s): renomeado para %s, começando vazio", type(e).__name__, bad.name)
            except OSError as e2:
                log.warning("tasks.json inválido (%s) e não consegui renomear: %s", type(e).__name__, type(e2).__name__)
            return []

    def _save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"v": 1, "items": self.items}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def _find(self, tid):
        for i, it in enumerate(self.items):
            if it["id"] == tid:
                return i
        raise ValueError("Essa tarefa não existe mais (talvez tenha sido removida em outro lugar).")

    # -------------------------------------------------------------- operações
    def get(self):
        pend = [i for i in self.items if not i["done"]]
        done = [i for i in self.items if i["done"]]
        return {"items": [dict(i) for i in pend + done], "total": len(self.items), "done": len(done)}

    def add(self, text):
        t = _clean(text)
        if len(self.items) >= MAX_ITEMS:
            raise ValueError(f"Limite de {MAX_ITEMS} tarefas atingido. Remova ou limpe as feitas.")
        ids = {i["id"] for i in self.items}
        tid = secrets.token_hex(4)
        while tid in ids:
            tid = secrets.token_hex(4)
        self.items.append({"id": tid, "text": t, "done": False, "done_at": None})
        self._save()
        return self.get()

    def restore(self, tid, text, done, pos):
        """Desfazer remoção: devolve a tarefa com o mesmo id, texto e estado, na posição `pos` da lista
        como o get() a mostra (pendentes e depois feitas). Mesmas validações do add."""
        if not isinstance(tid, str) or not _ID.match(tid):
            raise ValueError("Tarefa inválida.")
        t = _clean(text)
        if not isinstance(done, bool):
            raise ValueError("Tarefa inválida.")
        if isinstance(pos, bool) or not isinstance(pos, int):
            raise ValueError("Posição inválida.")
        if len(self.items) >= MAX_ITEMS:
            raise ValueError(f"Limite de {MAX_ITEMS} tarefas atingido. Remova ou limpe as feitas.")
        if any(i["id"] == tid for i in self.items):
            raise ValueError("Essa tarefa já está na lista.")
        ordered = [i for i in self.items if not i["done"]] + [i for i in self.items if i["done"]]
        ordered.insert(max(0, min(pos, len(ordered))),
                       {"id": tid, "text": t, "done": done, "done_at": time.time() if done else None})
        self.items = [i for i in ordered if not i["done"]] + [i for i in ordered if i["done"]]
        self._save()
        return self.get()

    def edit(self, tid, text):
        t = _clean(text)
        self.items[self._find(tid)]["text"] = t
        self._save()
        return self.get()

    def remove(self, tid):
        del self.items[self._find(tid)]
        self._save()
        return self.get()

    def toggle(self, tid):
        it = self.items[self._find(tid)]
        it["done"] = not it["done"]
        it["done_at"] = time.time() if it["done"] else None
        self._save()
        return self.get()

    def move(self, tid, delta):
        """Sobe (delta<0) ou desce (delta>0) entre as tarefas do mesmo grupo (pendentes ou feitas)."""
        i = self._find(tid)
        step = -1 if delta < 0 else 1
        j = i + step
        while 0 <= j < len(self.items) and self.items[j]["done"] != self.items[i]["done"]:
            j += step
        if 0 <= j < len(self.items):
            self.items[i], self.items[j] = self.items[j], self.items[i]
            self._save()
        return self.get()

    def clear_done(self):
        self.items = [i for i in self.items if not i["done"]]
        self._save()
        return self.get()
