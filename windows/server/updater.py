"""Atualizar com um clique (só pelo painel do PC): git fetch -> lista de commits novos -> git pull --ff-only ->
install-server.ps1 -NoRestart -> reinício seguro pela bandeja (o supervisor religa; ninguém encerra o servidor por fora).

Regras: nunca merge/rebase/force. Mudanças locais (arquivos rastreados), divergência, sem upstream ou qualquer erro = PARA e
levanta UpdateError com a mensagem pronta. Guarda a cópia anterior em server.prev (volta com rollback()). Log só com
resultado e hashes. O comando externo vem de `run` (injetável nos testes): run(argv, cwd, timeout) -> (código, saída).
"""
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("companion")

VERSION_FILE = "installed-version.json"
PREV_DIR = "server.prev"
MAX_LIST = 30


class UpdateError(Exception):
    """Mensagem já pronta para mostrar no painel."""


def run_cmd(argv, cwd, timeout):
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except FileNotFoundError:
        return 127, "programa não encontrado"
    except subprocess.TimeoutExpired:
        return 124, "demorou demais"
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


class Updater:
    def __init__(self, base, get_repo, version="", run=run_cmd, clock=time.time, installed_dir=None):
        self.base, self.get_repo, self.version, self.run, self.clock = Path(base), get_repo, version, run, clock
        home = Path(os.environ.get("USERPROFILE", "~")).expanduser()
        self.installed_dir = Path(installed_dir) if installed_dir else home / "companion-pc" / "server"
        self.lock = threading.RLock()
        self.path = self.base / VERSION_FILE
        self.last = None

    # -- estado ---------------------------------------------------------------
    def installed(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def info(self):
        d = self.installed()
        return {"version": self.version, "hash": str(d.get("hash", ""))[:12], "at": d.get("at"), "subject": d.get("subject", ""),
                "repo": self.get_repo() or "", "has_prev": (self.base / PREV_DIR).is_dir(), "last": self.last}

    def _done(self, ok, msg, **extra):
        self.last = {"ok": ok, "msg": msg, "at": int(self.clock())}
        log.info("atualizar: %s %s", "ok" if ok else "parou", extra.get("log", ""))   # nunca texto livre do git: só hashes
        return self.last

    # -- git -------------------------------------------------------------------
    def _git(self, repo, *args, timeout=60):
        return self.run(["git", "-C", str(repo), *args], str(repo), timeout)

    def _repo(self):
        r = (self.get_repo() or "").strip()
        if not r:
            raise UpdateError("Escolha a pasta do repositório em Ajustes › Sobre.")
        repo = Path(r)
        if not repo.is_dir():
            raise UpdateError("A pasta do repositório não existe mais.")
        code, out = self._git(repo, "rev-parse", "--is-inside-work-tree")
        if code != 0 or out.strip() != "true":
            raise UpdateError("Essa pasta não é um repositório git.")
        return repo

    def check(self):
        """Só leitura (fetch). Devolve {state: uptodate|behind|ahead, behind, ahead, head, commits[{hash,subject}]}."""
        with self.lock:
            repo = self._repo()
            code, out = self._git(repo, "status", "--porcelain", "-uno")
            if code != 0:
                raise UpdateError("Não consegui ler o estado do git.")
            if out.strip():
                raise UpdateError("Há mudanças locais não commitadas no repositório. Guarde-as antes de atualizar.")
            code, out = self._git(repo, "fetch", timeout=120)
            if code != 0:
                raise UpdateError("Não consegui buscar no GitHub (rede ou login). Nada foi alterado.")
            code, out = self._git(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}")
            if code != 0:
                raise UpdateError("A branch atual não tem upstream configurado.")
            try:
                ahead, behind = (int(x) for x in out.split())
            except ValueError:
                raise UpdateError("Resposta inesperada do git.") from None
            if ahead and behind:
                raise UpdateError(f"A branch divergiu do GitHub ({ahead} local, {behind} remoto). Resolva à mão; nada foi alterado.")
            code, head = self._git(repo, "rev-parse", "--short=12", "HEAD")
            res = {"state": "behind" if behind else "ahead" if ahead else "uptodate", "behind": behind, "ahead": ahead,
                   "head": head.strip() if code == 0 else "", "commits": []}
            if behind:
                code, out = self._git(repo, "log", "--format=%h%x09%s", f"-{MAX_LIST}", "HEAD..@{u}")
                for line in out.splitlines() if code == 0 else []:
                    h, _, s = line.partition("\t")
                    res["commits"].append({"hash": h, "subject": s[:120]})
            return res

    # -- aplicar ---------------------------------------------------------------
    def _snapshot(self):
        prev = self.base / PREV_DIR
        shutil.rmtree(prev, ignore_errors=True)
        prev.mkdir()
        for f in self.base.glob("*.py"):
            shutil.copy2(f, prev / f.name)
        if (self.base / "web").is_dir():
            shutil.copytree(self.base / "web", prev / "web")

    def _restore(self, src):
        for f in src.glob("*.py"):
            shutil.copy2(f, self.base / f.name)
        if (src / "web").is_dir():
            shutil.copytree(src / "web", self.base / "web", dirs_exist_ok=True)

    def apply(self):
        """git pull --ff-only + instalação. Devolve {ok, msg, at, restart}; quem chama reinicia só se restart=True."""
        with self.lock:
            repo = self._repo()
            try:
                same = self.base.resolve() == self.installed_dir.resolve()
            except OSError:
                same = False
            if not same:
                raise UpdateError("Este servidor não está na pasta instalada (companion-pc\\server); atualização recusada.")
            chk = self.check()
            if chk["state"] != "behind":
                msg = "Já está na versão mais recente." if chk["state"] == "uptodate" else "O repositório local tem commits à frente do GitHub; nada a baixar."
                return {**self._done(True, msg), "restart": False}
            old = chk["head"]
            code, _ = self._git(repo, "pull", "--ff-only", timeout=180)
            if code != 0:
                raise UpdateError("O git pull não conseguiu avançar sem merge. Nada foi instalado.")
            code, new = self._git(repo, "rev-parse", "--short=12", "HEAD")
            new = new.strip() if code == 0 else ""
            ps1 = repo / "windows" / "install-server.ps1"
            if not ps1.is_file():
                raise UpdateError("O install-server.ps1 não está no repositório; a cópia não foi feita.")
            try:
                self._snapshot()
            except OSError:
                raise UpdateError("Não consegui guardar a cópia anterior; nada foi instalado.") from None
            code, _ = self.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), "-NoRestart"], str(repo), 600)
            if code != 0:
                try:
                    self._restore(self.base / PREV_DIR)
                except OSError:
                    pass
                raise UpdateError("A instalação falhou; a versão anterior foi restaurada. O repositório já está em " + new + ".")
            code, subj = self._git(repo, "log", "-1", "--format=%s")
            self.path.write_text(json.dumps({"hash": new, "subject": subj.strip()[:120] if code == 0 else "", "at": int(self.clock())}), encoding="utf-8")
            return {**self._done(True, f"Atualizado de {old} para {new}. Reiniciando o servidor…", log=f"{old}->{new}"), "restart": True}

    def rollback(self):
        with self.lock:
            prev = self.base / PREV_DIR
            if not prev.is_dir():
                raise UpdateError("Não há versão anterior guardada.")
            try:
                self._restore(prev)
            except OSError:
                raise UpdateError("Não consegui restaurar a versão anterior.") from None
            try:
                self.path.unlink()
            except OSError:
                pass
            return {**self._done(True, "Versão anterior restaurada. Reiniciando o servidor…", log="rollback"), "restart": True}
