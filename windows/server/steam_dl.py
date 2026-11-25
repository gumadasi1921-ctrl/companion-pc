"""Downloads da Steam: progresso e aviso de término, só lendo os arquivos da própria Steam.

Fonte: steamapps/appmanifest_<id>.acf de cada biblioteca (os campos StateFlags, BytesToDownload,
BytesDownloaded, BytesToStage e BytesStaged). Sem chave, sem conta, sem rede. Roda na fila de disco do servidor.

Regras (nada é inventado):
- Um jogo aparece como "em download" só quando a Steam já começou ou pausou a atualização (bits 256, 512 ou 1024
  do StateFlags) E há bytes a baixar (BytesToDownload > 0). Só na fila (bit 2 sozinho) não aparece.
- O percentual é bytes baixados / bytes a baixar, como a Steam gravou. Sem velocidade nem tempo restante: o
  manifesto só é regravado de tempos em tempos e qualquer conta em cima dele seria chute.
- Aviso de término: só de um jogo que este módulo viu baixando e que depois ficou instalado (bit 4, sem bits de
  atualização, sem bytes pendentes), visto baixando há no máximo 10 min. Manifesto que sumiu = cancelado/desinstalado,
  sem aviso.
- Steam ausente ou ilegível = state() com available False (o app mostra "indisponível").
O nome do jogo nunca vai para o log.
"""
import glob
import logging
import os
import time
from pathlib import Path

import games

log = logging.getLogger("companion.steam_dl")

F_INSTALLED = 4
F_RUNNING, F_PAUSED, F_STARTED = 256, 512, 1024
F_UPDATING = F_RUNNING | F_PAUSED | F_STARTED | 2
SEEN_MAX_AGE_S = 600


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def read_manifest(text):
    """Texto de um appmanifest -> dict com o que interessa, ou None se não for um manifesto válido."""
    st = games._ci(games.parse_vdf(text), "AppState")
    if not isinstance(st, dict):
        return None
    appid, flags = _int(st.get("appid")), _int(st.get("StateFlags"))
    if appid is None or flags is None:
        return None
    return {"appid": str(appid), "name": st.get("name") if isinstance(st.get("name"), str) else None, "flags": flags,
            "to_dl": _int(st.get("BytesToDownload")) or 0, "dl": _int(st.get("BytesDownloaded")) or 0,
            "to_stage": _int(st.get("BytesToStage")) or 0, "staged": _int(st.get("BytesStaged")) or 0}


def describe(m):
    """Manifesto -> item de download ativo ({appid,name,pct,done,total,phase}) ou None se não está baixando."""
    if not (m["flags"] & (F_RUNNING | F_PAUSED | F_STARTED)) or m["to_dl"] <= 0:
        return None
    dl, total = min(m["dl"], m["to_dl"]), m["to_dl"]
    phase, done, tot = "download", dl, total
    if m["flags"] & F_PAUSED:
        phase = "paused"
    elif dl >= total and m["to_stage"] > 0:
        phase, done, tot = "stage", min(m["staged"], m["to_stage"]), m["to_stage"]   # baixou tudo; falta gravar os arquivos
    item = {"appid": m["appid"], "pct": round(done * 100 / tot, 1) if tot else 0.0, "done": done, "total": tot, "phase": phase}
    if m["name"]:
        item["name"] = m["name"]
    return item


def finished(m):
    """Instalado, sem bits de atualização e sem bytes pendentes."""
    return bool(m["flags"] & F_INSTALLED) and not (m["flags"] & F_UPDATING) and m["dl"] >= m["to_dl"]


class SteamDownloads:
    def __init__(self, steam_path_fn=None, clock=time.time):
        self.steam_path_fn = steam_path_fn or (
            lambda: games._reg(games.winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"))
        self.clock = clock
        self.seen = {}          # appid -> (epoch da última leitura baixando, nome)
        self.items = []
        self.available = False
        self.polled = False     # ainda não leu nada: o aparelho não deve ver "indisponível" por isso
        self._last = None

    def _manifests(self):
        sp = self.steam_path_fn()
        if not (sp and os.path.isdir(sp)):
            return None
        out = []
        for lib in games.steam_libraries(sp):
            for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
                try:
                    m = read_manifest(Path(acf).read_text(encoding="utf-8", errors="ignore"))
                except OSError:
                    continue   # a Steam estava gravando: tenta de novo na próxima leitura
                if m:
                    out.append(m)
        return out

    def poll(self):
        """Lê os manifestos. Devolve (mudou, avisos_de_término[{appid,name}])."""
        try:
            ms = self._manifests()
        except Exception as e:  # noqa: BLE001
            log.warning("downloads da Steam: erro (%s)", type(e).__name__)
            ms = None
        now = self.clock()
        self.polled = True
        if ms is None:
            self.available, self.items = False, []
            changed = self._last != (False, ())
            self._last = (False, ())
            return changed, []
        items, done = [], []
        by_id = {m["appid"]: m for m in ms}
        for m in ms:
            it = describe(m)
            if it:
                items.append(it)
                self.seen[m["appid"]] = (now, m["name"])
        active = {i["appid"] for i in items}
        for appid, (t, name) in list(self.seen.items()):
            if appid in active:
                continue
            m = by_id.get(appid)
            if m and finished(m) and now - t <= SEEN_MAX_AGE_S:
                done.append({"appid": appid, "name": m["name"] or name})
            if not m or finished(m) or now - t > SEEN_MAX_AGE_S:
                del self.seen[appid]      # concluiu, sumiu ou ficou velho demais para valer um aviso
        items.sort(key=lambda i: (i["phase"] == "paused", i.get("name") or ""))
        self.available, self.items = True, items
        key = (True, tuple((i["appid"], i["phase"], i["pct"]) for i in items))
        changed = key != self._last
        self._last = key
        return changed, done

    def state(self):
        return {"available": self.available, "items": list(self.items)}
