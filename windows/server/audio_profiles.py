"""Perfis de áudio do Mixer (audio_profiles.json, ao lado do config.json).

Cada perfil guarda o que o PC tinha ao salvar: saída padrão, volume/mudo geral, microfone (volume/mudo)
e volume/mudo dos apps que estavam tocando. Só guarda e valida; quem lê e aplica no Windows é o pc_info.
Gravação atômica; arquivo corrompido vira audio_profiles.json.bad-<data-hora>. O nome do perfil é texto do
usuário: nunca vai para o log.
"""
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

log = logging.getLogger("companion.audio_profiles")

MAX_PROFILES = 20
MAX_NAME = 40
MAX_APPS = 40
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ID = re.compile(r"^[0-9a-f]{8}$")


def _pct(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return max(0, min(100, int(round(v))))


def _dev(d):
    """{level, muted} válido ou None (campo ausente = não aplica, nada é inventado)."""
    if not isinstance(d, dict):
        return None
    out = {}
    lv = _pct(d.get("level"))
    if lv is not None:
        out["level"] = lv
    if isinstance(d.get("muted"), bool):
        out["muted"] = d["muted"]
    return out or None


def clean_snapshot(s):
    """Valida o que o pc_info.profile_snapshot devolveu. ValueError se não houver nada para guardar."""
    if not isinstance(s, dict):
        raise ValueError("não consegui ler o áudio do PC")
    out = {}
    o = s.get("output")
    if isinstance(o, dict) and isinstance(o.get("id"), str) and o["id"] and len(o["id"]) <= 200:
        out["output"] = {"id": o["id"], "name": str(o.get("name") or "")[:80]}
    for k in ("master", "mic"):
        d = _dev(s.get(k))
        if d:
            out[k] = d
    apps = {}
    for key, a in (s.get("apps") or {}).items() if isinstance(s.get("apps"), dict) else []:
        d = _dev(a)
        if d and isinstance(key, str) and 0 < len(key) <= 80 and len(apps) < MAX_APPS:
            d["name"] = str(a.get("name") or key)[:60]
            apps[key] = d
    if apps:
        out["apps"] = apps
    if not out:
        raise ValueError("não consegui ler o áudio do PC")
    return out


def clean_name(name):
    n = re.sub(r" {2,}", " ", _CTRL.sub(" ", str(name if name is not None else ""))).strip()
    if not n:
        raise ValueError("Dê um nome ao perfil.")
    if len(n) > MAX_NAME:
        raise ValueError(f"Nome longo demais (máximo {MAX_NAME} caracteres).")
    return n


class AudioProfiles:
    def __init__(self, base):
        self.path = Path(base) / "audio_profiles.json"
        self.items = []
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not (isinstance(raw, dict) and raw.get("v") == 1 and isinstance(raw.get("items"), list)):
                raise ValueError("formato")
            items = []
            for i in raw["items"]:
                if not (isinstance(i, dict) and isinstance(i.get("id"), str) and _ID.match(i["id"])):
                    raise ValueError("formato")
                items.append({"id": i["id"], "name": clean_name(i.get("name")), "data": clean_snapshot(i.get("data"))})
            self.items = items
        except (OSError, ValueError, UnicodeDecodeError) as e:
            bad = self.path.with_name("audio_profiles.json.bad-" + time.strftime("%Y%m%d-%H%M%S"))
            try:
                os.replace(self.path, bad)
                log.warning("audio_profiles.json inválido (%s): renomeado para %s", type(e).__name__, bad.name)
            except OSError as e2:
                log.warning("audio_profiles.json inválido (%s) e não consegui renomear: %s", type(e).__name__, type(e2).__name__)

    def _save(self):
        tmp = self.path.with_name("audio_profiles.json.tmp")
        tmp.write_text(json.dumps({"v": 1, "items": self.items}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def list(self):
        """Para o aparelho: id, nome e um resumo do que o perfil guarda (sem ids de dispositivo)."""
        out = []
        for i in self.items:
            d = i["data"]
            out.append({"id": i["id"], "name": i["name"],
                        "output": (d.get("output") or {}).get("name") or None,
                        "apps": len(d.get("apps") or {}), "mic": "mic" in d})
        return out

    def get(self, pid):
        p = next((i for i in self.items if i["id"] == pid), None)
        if not p:
            raise ValueError("perfil não encontrado")
        return p

    def save(self, name, snapshot, pid=None):
        """Novo perfil, ou (com pid) sobrescreve o conteúdo de um existente mantendo o nome se name vier vazio."""
        snap = clean_snapshot(snapshot)
        if pid:
            p = self.get(pid)
            if name not in (None, ""):
                p["name"] = clean_name(name)
            p["data"] = snap
        else:
            if len(self.items) >= MAX_PROFILES:
                raise ValueError(f"Máximo de {MAX_PROFILES} perfis.")
            n = clean_name(name)
            if any(i["name"].casefold() == n.casefold() for i in self.items):
                raise ValueError("Já existe um perfil com esse nome.")
            self.items.append({"id": secrets.token_hex(4), "name": n, "data": snap})
        self._save()
        return self.list()

    def delete(self, pid):
        self.get(pid)
        self.items = [i for i in self.items if i["id"] != pid]
        self._save()
        return self.list()
