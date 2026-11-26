"""Backup do estado do app (localStorage) de cada aparelho, guardado no PC.

Por aparelho: <base>/device-backups/<pasta>/v-<epoch-ms>.json, as 5 últimas versões, gravação atômica, ≤256 KB.
A pasta é um hash do id do aparelho (id de 128 bits que só o próprio aparelho conhece); a listagem mostra só a
`ref` (início do hash), que serve para LER o backup de outro aparelho (copiar para o iPad) mas nunca para gravar.
Só chaves da lista fechada KEYS entram: nunca token, endereços de PCs ou credenciais. Nome do aparelho e valores
nunca vão para o log.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

KEEP = 5
COALESCE_S = 3600   # mudanças seguidas do mesmo aparelho (mesma linhagem) dentro de 1 h trocam a versão mais nova em vez de gastar uma das 5
MAX_BYTES = 256 * 1024
MAX_DEVICES = 20
SUBDIR = "device-backups"
ID_RX = re.compile(r"^[0-9a-f]{32}$")
REF_RX = re.compile(r"^[0-9a-f]{12}$")
FILE_RX = re.compile(r"^v-(\d{10,16})\.json$")

KEYS = ("theme", "variant", "mode", "accent", "crt", "home", "homepg", "tab", "views", "nav", "layouts", "mascot",
        "alerts", "snd", "vib", "wake", "dock", "idleMode", "land", "sens", "scrFast", "micFab", "micfab", "lumepos",
        "dotaOpen", "deckAuto", "pcnotif", "wmode", "tkmode")


class BackupError(ValueError):
    pass


def _folder(dev_id):
    return hashlib.sha256(("companion-devbk-v1:" + dev_id).encode()).hexdigest()[:32]


def clean_name(name):
    s = " ".join(str(name or "").split())[:40]
    return s or "Aparelho"


class DeviceBackups:
    def __init__(self, base, clock=time.time):
        self.root = Path(base) / SUBDIR
        self.clock = clock

    # -- caminhos -------------------------------------------------------------
    def _versions(self, folder):
        """[(epoch_ms, Path)] da mais nova para a mais antiga."""
        out = []
        try:
            for p in (self.root / folder).iterdir():
                m = FILE_RX.match(p.name)
                if m:
                    out.append((int(m.group(1)), p))
        except OSError:
            return []
        return sorted(out, key=lambda x: -x[0])

    def _read(self, path):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) and isinstance(d.get("data"), dict) else None
        except (OSError, ValueError):
            return None

    def _folders(self):
        try:
            return [p.name for p in self.root.iterdir() if p.is_dir() and re.match(r"^[0-9a-f]{32}$", p.name)]
        except OSError:
            return []

    def _check_id(self, dev_id):
        dev_id = str(dev_id or "")
        if not ID_RX.match(dev_id):
            raise BackupError("aparelho inválido")
        return dev_id

    # -- gravar ---------------------------------------------------------------
    def save(self, dev_id, name, data, prev_at=None):
        """Grava uma versão nova (a menos que seja igual à última). `prev_at` = a data que o aparelho recebeu na última
        gravação/restauração: se ainda é a versão mais nova e tem < 1 h, ela é substituída (senão uma sessão de ajustes
        gastaria as 5 versões e o backup bom sairia da fila). Devolve {"saved", "at", "n"}."""
        dev_id = self._check_id(dev_id)
        if not isinstance(data, dict):
            raise BackupError("dados inválidos")
        clean = {k: data[k] for k in KEYS if k in data and data[k] is not None}
        if not clean:
            raise BackupError("nada para guardar")   # estado vazio nunca sobrescreve um backup bom
        folder = _folder(dev_id)
        vers = self._versions(folder)
        if not vers and len(self._folders()) >= MAX_DEVICES:
            raise BackupError("limite de aparelhos")
        try:
            blob = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError):
            raise BackupError("dados inválidos")
        if len(blob.encode("utf-8")) > MAX_BYTES:
            raise BackupError("backup grande demais")
        if vers:
            last = self._read(vers[0][1])
            if last and json.dumps(last["data"], ensure_ascii=False, separators=(",", ":"), sort_keys=True) == blob:
                nm = clean_name(name)
                if last.get("name") != nm:   # só o nome mudou: atualiza o nome na versão mais nova
                    last["name"] = nm
                    self._write(self.root / folder / vers[0][1].name, last)
                return {"saved": False, "at": vers[0][0] / 1000.0, "n": len(vers)}
        at_ms = int(self.clock() * 1000)
        if vers and at_ms <= vers[0][0]:
            at_ms = vers[0][0] + 1
        rec = {"v": 1, "name": clean_name(name), "at": at_ms / 1000.0, "data": json.loads(blob)}
        d = self.root / folder
        d.mkdir(parents=True, exist_ok=True)
        old = None
        if vers and prev_at is not None:
            try:
                same = abs(vers[0][0] - round(float(prev_at) * 1000)) <= 1
            except (TypeError, ValueError):
                same = False
            if same and at_ms - vers[0][0] < COALESCE_S * 1000:
                old = vers[0][1]
        self._write(d / f"v-{at_ms}.json", rec)
        if old is not None:
            try:
                old.unlink()
            except OSError:
                pass
            vers = vers[1:]
        for _, p in self._versions(folder)[KEEP:]:
            try:
                p.unlink()
            except OSError:
                pass
        return {"saved": True, "at": at_ms / 1000.0, "n": min(KEEP, len(vers) + 1)}

    def _write(self, path, rec):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)

    # -- ler ------------------------------------------------------------------
    def _summary(self, vers, own_name=None):
        return [{"at": ms / 1000.0, "size": p.stat().st_size if p.exists() else 0} for ms, p in vers]

    def own(self, dev_id):
        """Versões do próprio aparelho (mais nova primeiro)."""
        dev_id = self._check_id(dev_id)
        return self._summary(self._versions(_folder(dev_id)))

    def others(self, dev_id=None):
        """Backups de outros aparelhos (e de um aparelho recriado): [{ref, name, at, n}], mais recente primeiro."""
        mine = _folder(self._check_id(dev_id)) if dev_id else None
        out = []
        for f in self._folders():
            if f == mine:
                continue
            vers = self._versions(f)
            if not vers:
                continue
            rec = self._read(vers[0][1])
            if rec:
                out.append({"ref": f[:12], "name": clean_name(rec.get("name")), "at": vers[0][0] / 1000.0, "n": len(vers)})
        return sorted(out, key=lambda x: -x["at"])

    def get(self, dev_id=None, ref=None, at=None):
        """Dados de uma versão. Do próprio aparelho (dev_id) ou de outro (ref); `at` = epoch da versão (None = a mais nova)."""
        if ref:
            if not REF_RX.match(str(ref)):
                raise BackupError("aparelho inválido")
            folder = next((f for f in self._folders() if f.startswith(str(ref))), None)
            if not folder:
                raise BackupError("backup não encontrado")
        else:
            folder = _folder(self._check_id(dev_id))
        vers = self._versions(folder)
        if at is not None:
            try:
                want = round(float(at) * 1000)
            except (TypeError, ValueError):
                raise BackupError("versão inválida")
            vers = [v for v in vers if abs(v[0] - want) <= 1]
        if not vers:
            raise BackupError("backup não encontrado")
        rec = self._read(vers[0][1])
        if not rec:
            raise BackupError("backup ilegível")
        return {"at": vers[0][0] / 1000.0, "name": clean_name(rec.get("name")), "data": rec["data"]}

    # -- painel do PC ---------------------------------------------------------
    def list_all(self):
        out = []
        for f in self._folders():
            vers = self._versions(f)
            rec = self._read(vers[0][1]) if vers else None
            if rec:
                out.append({"ref": f[:12], "name": clean_name(rec.get("name")), "at": vers[0][0] / 1000.0,
                            "versions": self._summary(vers)})
        return sorted(out, key=lambda x: -x["at"])

    def delete(self, ref, at=None):
        """Apaga um aparelho inteiro ou uma versão. Só o painel chama."""
        if not REF_RX.match(str(ref or "")):
            raise BackupError("aparelho inválido")
        folder = next((f for f in self._folders() if f.startswith(str(ref))), None)
        if not folder:
            return 0
        n = 0
        for ms, p in self._versions(folder):
            if at is None or abs(ms - round(float(at) * 1000)) <= 1:
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        try:
            (self.root / folder).rmdir()   # só some se vazia
        except OSError:
            pass
        return n
