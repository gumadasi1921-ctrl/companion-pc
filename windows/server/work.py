"""Tempo de trabalho no Photoshop, Premiere e After Effects, por cliente, job e arquivo.

Conta só com o app em foco e alguém mexendo no PC (último input há menos de idle_min).
Sem nenhum lançamento manual: cadastram-se só as pastas-raiz de clientes (painel do PC).
  raiz\\Cliente\\Job\\...\\arquivo  -> cliente = 1º nível, job = 2º nível
  raiz\\Cliente\\arquivo.ext        -> job = nome do arquivo sem a extensão
Atribuição, nesta ordem e nada além:
  1) título com caminho completo (Premiere, After) dentro de uma raiz -> níveis da pasta;
  2) Photoshop (título só com o nome): o nome achado em EXATAMENTE UM lugar das raízes -> mesma regra;
  3) qualquer outro caso -> "Não identificado" + nome do arquivo (ou "sem título").
Janela em foco sem o arquivo no título (barra flutuante, diálogo): vale o título da janela principal do
mesmo processo, se exatamente uma delas tem arquivo; senão "Não identificado".
Formatos de título vistos neste PC (Windows 11, versões 2026):
  Photoshop:     "arquivo.psd @ 33,3% (camada, RGB/8*)"        -> só o nome, sem pasta
  Premiere:      "Adobe Premiere - C:\\Usuários\\User\\Área de Trabalho\\...\\projeto.prproj"
                 (o caminho vem com os nomes de pasta traduzidos, como o Explorer mostra)
  After Effects: "Adobe After Effects 2026 - C:\\Users\\User\\Desktop\\...\\projeto.aep"
Nomes de arquivo, clientes e jobs não vão para o log.
"""
import ctypes
import json
import logging
import ntpath
import os
import re
import time
from ctypes import wintypes
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("companion")

APPS = {"photoshop.exe": "Photoshop", "adobe premiere pro.exe": "Premiere", "afterfx.exe": "After Effects"}
UNIDENTIFIED = "Não identificado"
NO_JOB = "—"           # job dos "Não identificado"
UNTITLED = "sem título"
MAX_ROOTS, FOLDER_MAX = 20, 260
GAP_S = 15             # intervalo maior que isso entre leituras (suspensão, travada) não conta
SAVE_EVERY_S = 30
# índice de nomes para o Photoshop (só nomes; nada é aberto, lido, movido ou alterado)
INDEX_EXT = {".psd", ".psb", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}
INDEX_MAX_DEPTH = 6        # níveis abaixo da raiz
INDEX_MAX_FILES = 50_000   # arquivos indexados somando todas as raízes
INDEX_EVERY_S = 600        # varredura em segundo plano: no máximo a cada 10 min
INDEX_RETRY_S = 60         # nome não achado: nova varredura no máximo 1 vez por minuto
TITLE_PATH = {"adobe premiere pro.exe": re.compile(r"^Adobe Premiere(?: Pro)?(?: \d{4})? - (.+)$"),
              "afterfx.exe": re.compile(r"^Adobe After Effects(?: \d{4})? - (.+)$")}
ABS_PATH = re.compile(r"^(?:[A-Za-z]:\\|\\\\[^\\])")
CTRL = re.compile(r"[\x00-\x1f\x7f]")
WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


# --------------------------------------------------------------------------
# Título da janela -> (arquivo, caminho)
# --------------------------------------------------------------------------
def parse_title(exe, title):
    """(nome do arquivo ou None, caminho completo ou None). Nada além do que está no título."""
    title = (title or "").strip()
    if exe == "photoshop.exe":
        if " @ " not in title:
            return None, None
        f = title.split(" @ ", 1)[0].strip()
        return (f or None), None
    rx = TITLE_PATH.get(exe)
    m = rx.match(title) if rx else None
    if not m:
        return None, None
    v = m.group(1).strip().rstrip("*").strip()  # "*" = alterações não salvas
    if not v:
        return None, None
    if ABS_PATH.match(v):
        return (ntpath.basename(v) or None), v
    return v, None


def norm_path(p):
    """Só para comparar: barras, maiúsculas/minúsculas e "..". Nunca abre nada."""
    return ntpath.normcase(ntpath.normpath(str(p).strip()))


# Pastas conhecidas do Windows: caminho real (SHGetKnownFolderPath) e como o Explorer o exibe
# (nome de cada parte pelo Shell, ex.: C:\Usuários\User\Área de Trabalho). O Premiere põe a forma
# exibida no título; aqui ela volta a ser a real. Só troca de prefixo, sem tabela de traduções.
KNOWN_FOLDERS = ["B4BFCC3A-DB2C-424C-B029-7FE99A87C641",  # Desktop
                 "FDD39AD0-238F-46AF-ADB4-6C85480369C7",  # Documentos
                 "374DE290-123F-4565-9164-39C4925E467B",  # Downloads
                 "33E28130-4E1E-4676-835A-98395C3BC3BB",  # Imagens
                 "18989B1D-99B5-455B-841C-AB7C74E4DDFC",  # Vídeos
                 "4BD8D571-6D19-48D3-BE97-422220080E43",  # Músicas
                 "5E6C858F-0E22-4760-9AFE-EA3317B67173",  # perfil do usuário
                 "0762D272-C50A-4BB0-A382-697DCD729B80",  # C:\Users
                 "DFDF76A2-C82A-4D63-906A-5644AC457385",  # Público
                 "905E63B6-C1BF-494E-B29C-65B732D3D21A",  # Arquivos de Programas
                 "7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E"]  # Arquivos de Programas (x86)
_DISPLAY_MAP = None  # [(exibido normalizado, real com maiúsculas)], o mais longo primeiro


def _shell_display_map():
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HANDLE), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]

    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    ole32.CoInitialize(None)
    out = {}
    for fid in KNOWN_FOLDERS:
        u = uuid.UUID(fid)
        g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        p = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) != 0 or not p.value:
            continue
        real = p.value
        ole32.CoTaskMemFree(p)
        parts = real.rstrip("\\").split("\\")
        disp = [parts[0]]  # a unidade fica como está ("C:")
        for i in range(1, len(parts)):
            info = SHFILEINFOW()
            ok = shell32.SHGetFileInfoW("\\".join(parts[: i + 1]), 0, ctypes.byref(info), ctypes.sizeof(info), 0x200)
            disp.append(info.szDisplayName if ok and info.szDisplayName else parts[i])  # 0x200 = SHGFI_DISPLAYNAME
        d = norm_path("\\".join(disp))
        if d != norm_path(real):
            out[d] = ntpath.normpath(real)
    return sorted(out.items(), key=lambda x: -len(x[0]))


def real_path(path):
    """Caminho com o prefixo exibido de uma pasta conhecida trocado pelo real. Mantém maiúsculas."""
    global _DISPLAY_MAP
    if _DISPLAY_MAP is None:
        try:
            _DISPLAY_MAP = _shell_display_map()
        except Exception as e:  # noqa: BLE001
            log.warning("pastas conhecidas do Windows: %s", type(e).__name__)
            _DISPLAY_MAP = []
    p = ntpath.normpath(str(path).strip())
    low = ntpath.normcase(p)  # mesmo comprimento que p: normcase só troca maiúsculas
    for disp, real in _DISPLAY_MAP:
        if low == disp or low.startswith(disp + "\\"):
            return real + p[len(disp):]
    return p


def to_real(path):
    """real_path normalizado (para comparar)."""
    return ntpath.normcase(real_path(path))


def client_job(path, roots):
    """(cliente, job) pelos níveis da pasta, ou None se o caminho não está dentro de uma raiz
    ou está direto na raiz (sem pasta de cliente)."""
    p = real_path(path)
    low = ntpath.normcase(p)
    for root in roots:
        r = to_real(root).rstrip("\\")
        if low.startswith(r + "\\"):
            parts = [x for x in p[len(r) + 1:].split("\\") if x]
            if len(parts) < 2:
                return None
            client = parts[0]
            job = ntpath.splitext(parts[1])[0] if len(parts) == 2 else parts[1]
            return client, (job or parts[1])
    return None


# --------------------------------------------------------------------------
# Programa em foco e ociosidade (Win32)
# --------------------------------------------------------------------------
class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds():
    li = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000


def pick_title(exe, focus_title, main_titles):
    """Título usado na atribuição. A janela em foco, se o título dela tem o arquivo; senão (barra flutuante,
    diálogo) o da janela PRINCIPAL do mesmo processo, só se houver exatamente um título com arquivo entre elas;
    senão o da janela em foco (vira "Não identificado")."""
    if parse_title(exe, focus_title)[0]:
        return focus_title
    found = {t for t in main_titles if parse_title(exe, t)[0]}
    return found.pop() if len(found) == 1 else focus_title


_U32 = None


def _main_window_titles(pid):
    """Títulos das janelas principais (visíveis, sem janela dona) do processo pid."""
    global _U32
    if _U32 is None:  # instância própria: argtypes daqui não mexem no user32 usado pelo resto do servidor
        u = ctypes.WinDLL("user32")
        u.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        u.GetWindow.argtypes, u.GetWindow.restype = [wintypes.HWND, wintypes.UINT], wintypes.HWND
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        _U32 = u
    u, out = _U32, []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h) and not u.GetWindow(h, 4):  # 4 = GW_OWNER
            p = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == pid:
                buf = ctypes.create_unicode_buffer(1024)
                u.GetWindowTextW(h, buf, 1024)
                if buf.value:
                    out.append(buf.value)
        return True

    u.EnumWindows(cb, 0)
    return out


def foreground():
    """(exe em minúsculas, título) da janela em foco. Título com até 1024 caracteres (caminhos longos).
    Janela da Adobe sem o arquivo no título: usa a janela principal do mesmo processo (pick_title)."""
    import psutil
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).name().lower()
    except (psutil.Error, OSError):
        return "", ""
    if exe not in APPS:
        return exe, ""
    buf = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 1024)
    title = buf.value
    if not parse_title(exe, title)[0]:
        try:
            title = pick_title(exe, title, _main_window_titles(pid.value))
        except OSError:
            pass
    return exe, title


# --------------------------------------------------------------------------
# Arquivos JSON (gravação atômica; corrompido vira .bak)
# --------------------------------------------------------------------------
def _load_json(path: Path, check):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if check(data):
            return data
        raise ValueError("estrutura inesperada")
    except (OSError, ValueError) as e:
        bak = path.with_name(path.name + ".bak")
        try:
            path.replace(bak)
            log.warning("%s inválido (%s): renomeado para %s, começando um novo", path.name, e, bak.name)
        except OSError as e2:
            log.warning("%s inválido (%s) e não consegui renomear: %s", path.name, e, e2)
        return None


def _save_json(path: Path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _day(ts):
    return datetime.fromtimestamp(ts).date().isoformat()


def _next_midnight(ts):
    d = datetime.fromtimestamp(ts).date() + timedelta(days=1)
    return datetime.combine(d, datetime.min.time()).timestamp()


def valid_idle_min(v):
    """Tempo de ausência em minutos (1 a 30): a mesma regra no config.json, no painel e na inicialização."""
    try:
        m = int(float(v))
    except (TypeError, ValueError):
        raise ValueError("tempo de ausência inválido") from None
    if not 1 <= m <= 30:
        raise ValueError("tempo de ausência: de 1 a 30 minutos")
    return m


def _ok_history(d):
    return isinstance(d, dict) and isinstance(d.get("days"), dict) and all(
        isinstance(v, dict) and all(isinstance(s, (int, float)) for s in v.values()) for v in d["days"].values())


def _ok_roots(d):
    return isinstance(d, dict) and isinstance(d.get("roots"), list) and all(isinstance(r, str) for r in d["roots"])


def migrate_v1(days):
    """Histórico antigo (job\\tapp\\tarquivo) -> cliente\\tjob\\tapp\\tarquivo, sem perder segundos.
    Job antigo vira cliente; "Sem job" vira "Não identificado"; "arquivo desconhecido" vira "sem título"."""
    out = {}
    for ds, items in days.items():
        nd = out.setdefault(ds, {})
        for k, s in items.items():
            parts = k.split("\t")
            if len(parts) >= 4:
                nk = k
            else:
                job, app, f = (parts + ["", "", ""])[:3]
                f = UNTITLED if f in ("", "arquivo desconhecido") else f
                nk = "\t".join([UNIDENTIFIED, NO_JOB, app, f] if job in ("", "Sem job")
                               else [job, "(antes das pastas de clientes)", app, f])
            nd[nk] = round(nd.get(nk, 0) + s, 1)
    return out


# --------------------------------------------------------------------------
# Monitor + histórico
# --------------------------------------------------------------------------
class WorkTracker:
    """Usado só pela fila de disco do servidor (uma thread): sem locks."""

    def __init__(self, base: Path, idle_min=5):
        self.hist_path = base / "work-history.json"
        self.roots_path = base / "work-roots.json"
        try:
            self.idle_s = valid_idle_min(idle_min) * 60
        except ValueError:
            self.idle_s = 5 * 60
        raw = _load_json(self.hist_path, _ok_history)
        self.days = (raw or {"days": {}})["days"]
        if raw is not None and raw.get("version") != 2:
            self.days = migrate_v1(self.days)
            try:
                bak = self.hist_path.with_name("work-history.v1.json.bak")
                if not bak.exists():
                    bak.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
                _save_json(self.hist_path, {"version": 2, "days": self.days})
                log.info("work-history.json: convertido para cliente/job (cópia antiga em %s)", bak.name)
            except OSError as e:
                log.warning("work-history.json: não consegui gravar a conversão: %s", e)
        self.roots = []
        try:
            self.roots = self._validate_roots((_load_json(self.roots_path, _ok_roots) or {}).get("roots", []))
        except ValueError as e:
            log.warning("work-roots.json: %s - ignorando", e)
        self.index, self.index_at, self.index_info = {}, 0.0, {"files": 0, "truncated": False}
        self.pending = []      # [(início, fim, chave)] depois do último input: conta só se vier outro input
        self.prev = None       # hora da leitura anterior
        self.last_input = None
        self.dirty, self.saved_at = False, 0.0
        self.now_info = {"app": None, "file": None, "client": None, "job": None, "identified": False, "counting": False}
        self.reclass_plan = None  # {(dia, chave antiga): chave nova}, do último resumo

    # -- raízes de clientes ---------------------------------------------------
    @staticmethod
    def _validate_roots(roots):
        if not isinstance(roots, list) or len(roots) > MAX_ROOTS:
            raise ValueError(f"no máximo {MAX_ROOTS} pastas de clientes")
        out = []
        for r in roots:
            r = str(r or "").strip()
            if not r or len(r) > FOLDER_MAX or CTRL.search(r) or not ABS_PATH.match(r):
                raise ValueError("a pasta precisa ser um caminho completo (ex.: C:\\Clientes)")
            if any(to_real(r) == to_real(o) for o in out):
                raise ValueError("essa pasta já está na lista")
            out.append(r)
        return out

    def _set_roots(self, roots):
        self.roots = self._validate_roots(roots)
        _save_json(self.roots_path, {"roots": self.roots})
        self.index, self.index_at = {}, 0.0  # reindexa na próxima leitura
        self.reclass_plan = None             # resumo gerado com as raízes antigas não vale mais
        return self.get()

    def add_root(self, folder):
        return self._set_roots(self.roots + [folder])

    def remove_root(self, folder):
        return self._set_roots([r for r in self.roots if r != folder])

    def set_idle_min(self, minutes):
        self.idle_s = valid_idle_min(minutes) * 60

    # -- índice de nomes (Photoshop) -----------------------------------------
    def build_index(self, now=None):
        """Só nomes de arquivo: os.scandir nas raízes, até INDEX_MAX_DEPTH níveis e INDEX_MAX_FILES arquivos.
        Pastas inacessíveis são puladas. Nada é aberto, lido, movido ou alterado."""
        idx, n, truncated = {}, 0, False
        for root in self.roots:
            stack = [(real_path(root), 0)]
            while stack and not truncated:
                folder, depth = stack.pop()
                try:
                    with os.scandir(folder) as it:
                        for e in it:
                            try:
                                if e.is_dir(follow_symlinks=False):
                                    if depth < INDEX_MAX_DEPTH:
                                        stack.append((e.path, depth + 1))
                                elif ntpath.splitext(e.name)[1].lower() in INDEX_EXT:
                                    idx.setdefault(e.name.lower(), []).append(e.path)
                                    n += 1
                                    if n >= INDEX_MAX_FILES:
                                        truncated = True
                                        break
                            except OSError:
                                continue
                except OSError:
                    continue
        self.index, self.index_at = idx, time.time() if now is None else now
        self.index_info = {"files": n, "truncated": truncated}

    def _find_unique(self, name, now):
        if not self.roots:
            return None
        if now - self.index_at >= INDEX_EVERY_S:
            self.build_index(now)
        hits = self.index.get(name.lower(), [])
        if not hits and now - self.index_at >= INDEX_RETRY_S:  # não achou: tenta de novo (1x por minuto)
            self.build_index(now)
            hits = self.index.get(name.lower(), [])
        return hits[0] if len(hits) == 1 else None

    # -- atribuição -----------------------------------------------------------
    def attribute(self, exe, title, now):
        """(cliente, job, arquivo, identificado)."""
        f, path = parse_title(exe, title)
        found = None
        if path:
            found = client_job(path, self.roots)
        elif f and exe == "photoshop.exe":
            hit = self._find_unique(f, now)
            found = client_job(hit, self.roots) if hit else None
        if found:
            return found[0], found[1], f, True
        return UNIDENTIFIED, NO_JOB, f or UNTITLED, False

    # -- contagem -------------------------------------------------------------
    def _add(self, a, b, key):
        """Soma [a, b) ao dia certo (divide na meia-noite local)."""
        while a < b:
            end = min(b, _next_midnight(a))
            day = self.days.setdefault(_day(a), {})
            k = "\t".join(key)
            day[k] = round(day.get(k, 0) + (end - a), 1)
            a = end
            self.dirty = True

    def poll(self, now=None, fg=None, idle=None):
        now = time.time() if now is None else now
        exe, title = foreground() if fg is None else fg
        idle = idle_seconds() if idle is None else idle
        li = now - idle
        # input novo desde a última leitura: o que estava pendente antes dele passa a valer
        if self.last_input is None or li > self.last_input:
            keep = []
            for a, b, key in self.pending:
                if li > a:
                    self._add(a, min(b, li), key)
                if b > li:
                    keep.append((max(a, li), b, key))
            self.pending = keep
        self.last_input = li
        app = APPS.get(exe)
        info = {"app": None, "file": None, "client": None, "job": None, "identified": False, "counting": False}
        if app:
            client, job, f, ok = self.attribute(exe, title, now)
            key = (client, job, app, f)
            path = parse_title(exe, title)[1]
            if not ok and path:  # Premiere/After: guarda o caminho para "Reclassificar" achar o cliente depois
                key += (CTRL.sub("", path),)
            info = {"app": app, "file": f, "client": client, "job": job, "identified": ok, "counting": idle < self.idle_s}
            prev = self.prev
            if prev is not None and 0 < now - prev <= GAP_S:
                if li > prev:          # houve input dentro do intervalo: até ele, conta
                    self._add(prev, min(now, li), key)
                if now > max(prev, li):  # depois do último input: espera confirmação
                    self.pending.append((max(prev, li), now, key))
        if idle >= self.idle_s:
            self.pending = []          # passou do limite: a espera não conta
        self.prev = now
        self.now_info = info
        if self.dirty and now - self.saved_at >= SAVE_EVERY_S:
            self.save(now)
        return info

    def save(self, now=None):
        if not self.dirty:
            return
        try:
            _save_json(self.hist_path, {"version": 2, "days": self.days})
            self.dirty, self.saved_at = False, time.time() if now is None else now
        except OSError as e:
            log.warning("work-history.json: %s", e)

    # -- reclassificar "Não identificado" (só pelo painel, com confirmação) ------
    def reclass_preview(self):
        """Resumo do que mudaria, com as mesmas regras da atribuição e nada além:
        Photoshop pelo nome achado em exatamente um lugar do índice atual; Premiere/After pelo caminho
        gravado no registro (registros antigos sem caminho ficam). "sem título" nunca muda. Não grava nada."""
        self.build_index()
        plan, agg = {}, {}
        for ds, items in self.days.items():
            for k, s in items.items():
                parts = k.split("\t")
                if len(parts) < 4 or parts[0] != UNIDENTIFIED or parts[3] in ("", UNTITLED):
                    continue
                app, f = parts[2], parts[3]
                found = None
                if app == APPS["photoshop.exe"]:
                    hits = self.index.get(f.lower(), [])
                    found = client_job(hits[0], self.roots) if len(hits) == 1 else None
                elif len(parts) >= 5 and parts[4]:
                    found = client_job(parts[4], self.roots)
                if found:
                    plan[(ds, k)] = "\t".join([found[0], found[1], app, f])
                    a = (f, app, found[0], found[1])
                    agg[a] = agg.get(a, 0) + s
        self.reclass_plan = plan
        return {"count": len(plan), "s": int(sum(agg.values())),
                "items": [{"file": f, "app": a, "client": c, "job": j, "s": int(s)}
                          for (f, a, c, j), s in sorted(agg.items(), key=lambda x: -x[1])]}

    def reclass_apply(self, now=None):
        """Aplica o último resumo: backup do work-history.json antes, total de segundos igual antes e depois."""
        plan = self.reclass_plan
        if not plan:
            raise ValueError("gere o resumo de novo antes de aplicar")
        self.reclass_plan = None
        self.dirty = True
        self.save(now)
        if self.dirty:
            raise ValueError("não consegui gravar o histórico antes do backup; nada foi mudado")
        bak = self.hist_path.with_name(self.hist_path.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        try:
            bak.write_bytes(self.hist_path.read_bytes())
        except OSError as e:
            raise ValueError(f"não consegui fazer o backup ({e}); nada foi mudado") from None
        before = sum(s for v in self.days.values() for s in v.values())
        old = {ds: dict(v) for ds, v in self.days.items()}
        moved_s, moved = 0.0, 0
        for (ds, k), nk in plan.items():
            day = self.days.get(ds, {})
            if k in day:
                s = day.pop(k)
                day[nk] = round(day.get(nk, 0) + s, 1)
                moved_s, moved = moved_s + s, moved + 1
        after = sum(s for v in self.days.values() for s in v.values())
        if abs(after - before) > 0.05 * max(1, moved):  # só arredondamento de 0,1 s por registro
            self.days.clear()
            self.days.update(old)
            raise ValueError("o total mudaria ao reclassificar; nada foi mudado")
        self.dirty = True
        self.save(now)
        return {"moved": moved, "s": int(moved_s), "total_before_s": round(before, 1), "total_after_s": round(after, 1),
                "backup": bak.name}

    # -- resumos --------------------------------------------------------------
    def _rows(self, d0, d1):
        """[(dia, cliente, job, app, arquivo, segundos)] de d0 a d1 (inclusive)."""
        out = []
        for ds, items in self.days.items():
            try:
                d = date.fromisoformat(ds)
            except ValueError:
                continue
            if d0 <= d <= d1:
                for k, s in items.items():
                    client, job, app, f = (k.split("\t") + ["", "", "", ""])[:4]
                    out.append((d, client, job, app, f, s))
        return out

    def get(self, today=None):
        today = today or date.today()
        per = {}
        for _d, client, _j, _a, _f, s in self._rows(today, today):
            per[client] = per.get(client, 0) + s
        return {"roots": self.roots, "idle_min": round(self.idle_s / 60), "now": self.now_info,
                "index": self.index_info, "today_s": int(sum(per.values())),
                "today": [{"client": c, "s": int(s), "unidentified": c == UNIDENTIFIED}
                          for c, s in sorted(per.items(), key=lambda x: -x[1])]}

    @staticmethod
    def _week_bounds(offset, today=None):
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            raise ValueError("semana inválida") from None
        if not -104 <= offset <= 0:
            raise ValueError("semana inválida")
        today = today or date.today()
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        return monday, monday + timedelta(days=6)

    def week(self, offset=0, today=None):
        today = today or date.today()
        mon, sun = self._week_bounds(offset, today)
        per_day = [0.0] * 7
        tree = {}
        for d, client, job, app, f, s in self._rows(mon, sun):
            per_day[(d - mon).days] += s
            c = tree.setdefault(client, {"s": 0.0, "jobs": {}})
            c["s"] += s
            j = c["jobs"].setdefault(job, {"s": 0.0, "files": {}})
            j["s"] += s
            j["files"][(app, f)] = j["files"].get((app, f), 0) + s

        def by_s(d):
            return sorted(d.items(), key=lambda x: -(x[1]["s"] if isinstance(x[1], dict) else x[1]))

        return {"offset": int(offset), "start": mon.isoformat(), "end": sun.isoformat(), "total_s": int(sum(per_day)),
                "days": [{"date": (mon + timedelta(days=i)).isoformat(), "d": WEEKDAYS[i], "s": int(v),
                          "today": mon + timedelta(days=i) == today} for i, v in enumerate(per_day)],
                "clients": [{"client": cn, "s": int(c["s"]), "unidentified": cn == UNIDENTIFIED,
                             "jobs": [{"job": jn, "s": int(j["s"]),
                                       "files": [{"app": a, "file": f, "s": int(s)} for (a, f), s in by_s(j["files"])]}
                                      for jn, j in by_s(c["jobs"])]}
                            for cn, c in by_s(tree)]}

    def csv(self, offset=0, today=None):
        """(nome do arquivo, bytes) da semana: data;cliente;job;app;arquivo;minutos, UTF-8 com BOM (Excel BR)."""
        mon, sun = self._week_bounds(offset, today)

        def cell(v):
            v = str(v)
            if v[:1] in ("=", "+", "-", "@", "\t", "\r"):  # Excel não trata o texto como fórmula
                v = "'" + v
            if any(c in v for c in ';"\r\n'):
                v = '"' + v.replace('"', '""') + '"'
            return v

        lines = ["data;cliente;job;app;arquivo;minutos"]
        for d, client, job, app, f, s in sorted(self._rows(mon, sun)):
            lines.append(";".join([d.strftime("%d/%m/%Y"), cell(client), cell(job), cell(app), cell(f),
                                   f"{s / 60:.1f}".replace(".", ",")]))
        name = f"trabalho-{mon.isoformat()}_a_{sun.isoformat()}.csv"
        return name, ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8")
