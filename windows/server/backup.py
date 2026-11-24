"""Backup diário dos dados pessoais para uma pasta escolhida no painel: <pasta>\\Companion-backup\\AAAA-MM-DD\\.

Copia histórico de trabalho, regras, notas, raízes, tarefas, pomodoro e deck (nunca config.json: tem tokens).
Guarda os 30 backups mais recentes; só apaga pastas AAAA-MM-DD DENTRO de Companion-backup. Roda na fila de disco
do servidor, nunca levanta erro para quem chama (o resultado vai em backup-status.json e no painel). Sem nomes no log.
"""
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger("companion")

SUBDIR = "Companion-backup"
KEEP_DAYS = 30
FILES = ["work-history.json", "work_rules.json", "work-notes.json", "work-roots.json", "tasks.json", "pomodoro.json", "deck.json"]
DAY_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RETRY_S = 3600


class Backup:
    def __init__(self, base: Path, clock=time.time):
        self.base, self.clock = Path(base), clock
        self.status_path = self.base / "backup-status.json"

    # -- estado ---------------------------------------------------------------
    def status(self):
        try:
            d = json.loads(self.status_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_status(self, d):
        try:
            tmp = self.status_path.with_name(self.status_path.name + ".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.status_path)
        except OSError:
            pass

    def due(self, dest):
        """Falta o backup de hoje (ou o último erro tem mais de 1 h)? Sem pasta escolhida = nunca."""
        if not dest:
            return False
        st = self.status()
        if st.get("dest") != str(dest):
            return True
        if st.get("ok"):
            return st.get("date") != time.strftime("%Y-%m-%d", time.localtime(self.clock()))
        return self.clock() - st.get("at", 0) >= RETRY_S

    # -- execução -------------------------------------------------------------
    def run(self, dest):
        """Copia e poda. Devolve o status gravado ({"ok", "date", "at", "files", "kept", "error"?})."""
        now = self.clock()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        st = {"at": now, "date": day, "dest": str(dest), "ok": False}
        try:
            if not dest or not os.path.isabs(str(dest)):
                raise ValueError("pasta de backup não escolhida")
            root = Path(dest) / SUBDIR
            target = root / day
            target.mkdir(parents=True, exist_ok=True)
            n = 0
            for name in FILES:
                src = self.base / name
                if src.is_file():
                    tmp = target / (name + ".tmp")
                    shutil.copy2(src, tmp)
                    tmp.replace(target / name)
                    n += 1
            st.update(ok=True, files=n, kept=self._prune(root))
        except Exception as e:  # noqa: BLE001
            st["error"] = type(e).__name__   # só o tipo: a mensagem pode ter caminho
            log.warning("backup falhou (%s)", type(e).__name__)
        self._write_status(st)
        return st

    @staticmethod
    def _prune(root: Path):
        days = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and DAY_RX.match(p.name))
        for old in days[:-KEEP_DAYS]:
            try:
                if old.resolve().parent == root.resolve():   # só filhos diretos de Companion-backup
                    shutil.rmtree(old)
            except OSError:
                pass
        return min(len(days), KEEP_DAYS)
