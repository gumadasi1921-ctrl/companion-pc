"""Tempo de trabalho no Photoshop, Illustrator, Premiere, After Effects e CapCut, por cliente, job e arquivo.

Conta só com o app em foco e alguém mexendo no PC (último input há menos de idle_min).
Sem nenhum lançamento manual: cadastram-se só as pastas-raiz de clientes (painel do PC).
  raiz\\Cliente\\Job\\...\\arquivo  -> cliente = 1º nível, job = 2º nível
  raiz\\Cliente\\arquivo.ext        -> job = nome do arquivo sem a extensão
Atribuição, nesta ordem e nada além:
  1) título com caminho completo (Premiere, After) dentro de uma raiz -> níveis da pasta;
  2) Photoshop (título só com o nome): o nome achado em EXATAMENTE UM lugar das raízes -> mesma regra;
  3) regra manual do usuário (work_rules.json): nome do arquivo IGUAL (sem diferenciar maiúsc./minúsc.),
     nunca "parecido"; nomes genéricos ("sem título", "Untitled-1") só aceitam atribuição pontual de um período;
  4) qualquer outro caso -> "Não identificado" + nome do arquivo (ou "sem título").
O tempo é gravado no histórico sempre com a atribuição de 1) e 2); as regras do item 3 (e "Não é trabalho") são
aplicadas na LEITURA (get/week/csv): apagar a regra devolve o tempo a "Não identificado" e o total nunca muda.
Janela em foco sem o arquivo no título (barra flutuante, diálogo): vale o título da janela principal do
mesmo processo, se exatamente uma delas tem arquivo; senão "Não identificado".
Formatos de título vistos neste PC (Windows 11, versões 2026):
  Photoshop:     "arquivo.psd @ 33,3% (camada, RGB/8*)"        -> só o nome, sem pasta
  Premiere:      "Adobe Premiere - C:\\Usuários\\User\\Área de Trabalho\\...\\projeto.prproj"
                 (o caminho vem com os nomes de pasta traduzidos, como o Explorer mostra)
  After Effects: "Adobe After Effects 2026 - C:\\Users\\User\\Desktop\\...\\projeto.aep"
  Illustrator:   "arquivo.pdf @ 66,67 % (RGB/Visualizar)"       -> só o nome (.ai/.eps/.pdf/.svg); Illustrator.exe
  CapCut:        "CapCut" (a barra com o nome é desenhada pelo próprio app: o título do Windows nunca traz o projeto).
                 Projeto = nome da única pasta de .../com.lveditor.draft/<pasta>/ entre os arquivos abertos do
                 processo (draft_project/capcut_project); sem isso -> "CapCut · sem projeto". CapCut.exe.
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

APPS = {"photoshop.exe": "Photoshop", "adobe premiere pro.exe": "Premiere", "afterfx.exe": "After Effects",
        "illustrator.exe": "Illustrator", "capcut.exe": "CapCut"}
CAPCUT_NO_PROJECT = "CapCut · sem projeto"
CAPCUT_SEP = chr(1)   # título interno "CapCut<SEP><projeto>" montado por foreground() (o do Windows só diz "CapCut")
CAPCUT_DRAFT = re.compile(r"com\.lveditor\.draft[\\/]+([^\\/]+)[\\/]", re.I)
CAPCUT_EVERY_S = 10
AI_EXT = {".ai", ".eps", ".pdf", ".svg"}
GENERIC_RX = re.compile(r"^(sem t[ií]tulo|untitled)(-\d+)?\*?$", re.I)
MAX_RULES, NAME_MAX = 500, 60
UNIDENTIFIED = "Não identificado"
NO_JOB = "—"           # job dos "Não identificado"
UNTITLED = "sem título"
MAX_ROOTS, FOLDER_MAX = 20, 260
GAP_S = 15             # intervalo maior que isso entre leituras (suspensão, travada) não conta
SAVE_EVERY_S = 30
RUN_GAP_S = 300        # trabalho contínuo (mascote): fora dos apps de trabalho ou parado por mais que isso encerra a sequência
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
NOTE_MAX = 500
MONTH_RX = re.compile(r"^(20\d\d)-(0[1-9]|1[0-2])$")
NOTE_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")   # tudo menos \n e \t


def valid_month(ym, today=None):
    """"AAAA-MM" (2000-2099); None = mês de hoje."""
    if ym is None:
        return (today or date.today()).strftime("%Y-%m")
    if not isinstance(ym, str) or not MONTH_RX.match(ym):
        raise ValueError("mês inválido")
    return ym


def month_bounds(ym):
    y, m = int(ym[:4]), int(ym[5:])
    first = date(y, m, 1)
    return first, (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:])
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


# --------------------------------------------------------------------------
# Título da janela -> (arquivo, caminho)
# --------------------------------------------------------------------------
def parse_title(exe, title):
    """(nome do arquivo ou None, caminho completo ou None). Nada além do que está no título."""
    title = (title or "").strip()
    if exe == "capcut.exe":
        if title == "CapCut":
            return CAPCUT_NO_PROJECT, None
        proj = title[len("CapCut" + CAPCUT_SEP):] if title.startswith("CapCut" + CAPCUT_SEP) else ""
        return (proj, None) if proj and not CTRL.search(proj) else (None, None)
    if exe == "illustrator.exe":
        if " @ " not in title:
            return None, None
        v = title.split(" @ ", 1)[0].strip().rstrip("*").strip()  # "*" = alterações não salvas
        if ntpath.splitext(v)[1].lower() not in AI_EXT:
            return None, None
        if ABS_PATH.match(v):
            return (ntpath.basename(v) or None), v
        return v, None
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


def is_generic(f):
    """Nome que não identifica um arquivo ("sem título", "Untitled-3", vazio, CapCut sem projeto)."""
    f = (f or "").strip()
    return not f or f == UNTITLED or f == CAPCUT_NO_PROJECT or bool(GENERIC_RX.match(f))


def draft_project(paths):
    """Nome da pasta de projeto do CapCut (.../com.lveditor.draft/<pasta>/...) entre os arquivos abertos: só
    se houver exatamente UMA pasta distinta; zero ou várias = None (nada de adivinhar)."""
    names = set()
    for p in paths:
        m = CAPCUT_DRAFT.search(str(p))
        if m and m.group(1) and not m.group(1).startswith(".") and len(m.group(1)) <= FOLDER_MAX:
            names.add(m.group(1))
    return names.pop() if len(names) == 1 else None


_CAPCUT_CACHE = {}   # pid -> (hora, projeto)


def capcut_project(pid, now=None):
    """Projeto aberto no CapCut pelos arquivos abertos do processo e filhos (psutil); no máximo 1x/10 s por pid.
    O título da janela do Windows não traz o nome (o CapCut desenha a própria barra)."""
    now = time.time() if now is None else now
    hit = _CAPCUT_CACHE.get(pid)
    if hit and now - hit[0] < CAPCUT_EVERY_S:
        return hit[1]
    import psutil
    paths = []
    try:
        root = psutil.Process(pid)
        for pr in [root] + root.children(recursive=True):
            try:
                paths += [f.path for f in pr.open_files()]
            except (psutil.Error, OSError):
                continue
    except (psutil.Error, OSError):
        pass
    proj = draft_project(paths)
    _CAPCUT_CACHE.clear() if len(_CAPCUT_CACHE) > 20 else None
    _CAPCUT_CACHE[pid] = (now, proj)
    return proj


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


APP_PATHS = {}   # app (Photoshop...) -> caminho do exe visto em foco; só o servidor usa (vira um id opaco de ícone)


def foreground():
    """(exe em minúsculas, título) da janela em foco. Título com até 1024 caracteres (caminhos longos).
    Janela da Adobe sem o arquivo no título: usa a janela principal do mesmo processo (pick_title)."""
    import psutil
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        proc = psutil.Process(pid.value)
        exe = proc.name().lower()
    except (psutil.Error, OSError):
        return "", ""
    if exe not in APPS:
        return exe, ""
    try:
        APP_PATHS[APPS[exe]] = proc.exe()
    except (psutil.Error, OSError):
        pass
    if exe == "capcut.exe":
        try:
            proj = capcut_project(pid.value)
        except Exception:  # noqa: BLE001
            proj = None
        return exe, "CapCut" + (CAPCUT_SEP + proj if proj else "")
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
        self.rules_path = base / "work_rules.json"
        self.notes_path = base / "work-notes.json"
        try:
            self.idle_s = valid_idle_min(idle_min) * 60
        except ValueError:
            self.idle_s = 5 * 60
        self.run_start = self.run_last = self.run_app = None   # sequência de trabalho contínuo (só memória)
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
        nraw = _load_json(self.notes_path, lambda d: isinstance(d, dict) and isinstance(d.get("notes", {}), dict)
                          and all(isinstance(k, str) and isinstance(v, str) for k, v in d.get("notes", {}).items()))
        self.notes = dict((nraw or {}).get("notes", {}))   # {"AAAA-MM-DD": texto}, nunca vai ao log
        self.rules, self.once, self.ignored = [], [], []   # regras manuais (ver "Regras manuais" abaixo)
        self._load_rules()
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
            man = None if ok else self._manual(f, app, date.fromtimestamp(now))
            if man and man[0] == "assign":   # só a exibição: o histórico guarda "Não identificado" (regra lida na leitura)
                client, job, ok = man[1], man[2], True
            info = {"app": app, "file": f, "client": client, "job": job, "identified": ok, "counting": idle < self.idle_s,
                    "ignored": bool(man and man[0] == "ignore")}
            prev = self.prev
            if prev is not None and 0 < now - prev <= GAP_S:
                if li > prev:          # houve input dentro do intervalo: até ele, conta
                    self._add(prev, min(now, li), key)
                if now > max(prev, li):  # depois do último input: espera confirmação
                    self.pending.append((max(prev, li), now, key))
        if idle >= self.idle_s:
            self.pending = []          # passou do limite: a espera não conta
        if app and idle < self.idle_s:
            if self.run_start is None or now - self.run_last > RUN_GAP_S:
                self.run_start = now
            self.run_last, self.run_app = now, app
        self.prev = now
        self.now_info = info
        if self.dirty and now - self.saved_at >= SAVE_EVERY_S:
            self.save(now)
        return info

    def stream(self, now=None):
        """Minutos de trabalho contínuo (app de trabalho em foco com input recente; lacunas até RUN_GAP_S não quebram).
        Só memória; sem arquivo, sem cliente. Sem sequência = minutes 0 e app None."""
        now = time.time() if now is None else now
        if self.run_start is None or now - self.run_last > RUN_GAP_S:
            return {"minutes": 0, "app": None}
        return {"minutes": int((self.run_last - self.run_start) // 60), "app": self.run_app}

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

    # -- Ligações job ↔ card do Trello (em work_rules.json, "links") ---------------------------------
    # links [{id, client, job, card (shortLink), card_name, created_at}]  1 card por job e 1 job por card.
    # Só guardam a ligação; o tempo é sempre lido do histórico (hoje, semana, total), então apagar a ligação não muda nada.
    @staticmethod
    def _link_ok(x):
        return (isinstance(x, dict) and isinstance(x.get("id"), str) and isinstance(x.get("client"), str) and x["client"]
                and isinstance(x.get("job"), str) and isinstance(x.get("card"), str) and re.fullmatch(r"[A-Za-z0-9]{8}", x["card"])
                and isinstance(x.get("card_name", ""), str))

    @staticmethod
    def _jkey(client, job):
        return (client.casefold(), (job or NO_JOB).casefold())

    def link_set(self, client, job, card, card_name=""):
        """Liga o job ao card (troca a ligação anterior do job e a do card). Devolve {"link", "replaced"} para o desfazer."""
        if not isinstance(card, str) or not re.fullmatch(r"[A-Za-z0-9]{8}", card):
            raise ValueError("card inválido")
        client = self._clean_name(client, "cliente")
        job = self._clean_name(job, "job", optional=True) or NO_JOB
        client, job = self._canon(client, job)
        name = CTRL.sub("", str(card_name or ""))[:200]
        key = self._jkey(client, job)
        replaced = [x for x in self.links if self._jkey(x["client"], x["job"]) == key or x["card"] == card]
        keep = [x for x in self.links if x not in replaced]
        if len(keep) >= MAX_RULES:
            raise ValueError("limite de ligações atingido")
        entry = {"id": self._new_id(), "client": client, "job": job, "card": card, "card_name": name,
                 "created_at": datetime.now().isoformat(timespec="seconds")}
        old = self.links
        self.links = keep + [entry]
        try:
            self._save_rules()
        except OSError:
            self.links = old
            raise ValueError("não consegui gravar a ligação") from None
        return {"link": entry, "replaced": replaced}

    def link_delete(self, ids):
        ids = {ids} if isinstance(ids, str) else set(ids or [])
        gone = [x for x in self.links if x["id"] in ids]
        if gone:
            old = self.links
            self.links = [x for x in self.links if x["id"] not in ids]
            try:
                self._save_rules()
            except OSError:
                self.links = old
                raise ValueError("não consegui gravar") from None
        return {"removed": gone}

    def link_restore(self, entries):
        """Desfazer: devolve ligações apagadas/trocadas (mesmos ids), sem duplicar job nem card."""
        if not isinstance(entries, list) or len(entries) > 20:
            raise ValueError("nada para restaurar")
        add = [x for x in entries if self._link_ok(x)]
        for x in add:
            self.links = [y for y in self.links if y["id"] != x["id"] and y["card"] != x["card"] and self._jkey(y["client"], y["job"]) != self._jkey(x["client"], x["job"])]
            self.links.append({k: x[k] for k in ("id", "client", "job", "card", "card_name", "created_at") if k in x})
        self._save_rules()
        return {"restored": len(add)}

    def link_times(self, today=None):
        """{shortLink: {today_s, week_s, total_s}} dos jobs ligados, numa passada só pelo histórico (regras/ignorados valem)."""
        if not self.links:
            return {}
        today = today or date.today()
        mon = today - timedelta(days=today.weekday())
        by_job = {self._jkey(x["client"], x["job"]): x["card"] for x in self.links}
        out = {x["card"]: {"today_s": 0.0, "week_s": 0.0, "total_s": 0.0} for x in self.links}
        for d, client, job, _a, _f, s_ in self._rows(date(2000, 1, 1), today):
            card = by_job.get(self._jkey(client, job))
            if card:
                t = out[card]
                t["total_s"] += s_
                if d >= mon:
                    t["week_s"] += s_
                if d == today:
                    t["today_s"] += s_
        return {c: {k: int(v) for k, v in t.items()} for c, t in out.items()}

    def links_get(self, today=None):
        times = self.link_times(today)
        return [{**x, **times.get(x["card"], {"today_s": 0, "week_s": 0, "total_s": 0})} for x in self.links]

    # -- Ligações job ↔ item do monday (work_rules.json, "mlinks"): mesmo molde das do Trello, com o id numérico do item ----
    @staticmethod
    def _mlink_ok(x):
        return (isinstance(x, dict) and isinstance(x.get("id"), str) and isinstance(x.get("client"), str) and x["client"]
                and isinstance(x.get("job"), str) and isinstance(x.get("item"), str) and re.fullmatch(r"[0-9]{1,20}", x["item"])
                and isinstance(x.get("item_name", ""), str))

    def mlink_set(self, client, job, item, item_name=""):
        """Liga o job ao item (troca a ligação anterior do job e a do item). Devolve {"link", "replaced"} para o desfazer."""
        if not isinstance(item, str) or not re.fullmatch(r"[0-9]{1,20}", item):
            raise ValueError("item inválido")
        client = self._clean_name(client, "cliente")
        job = self._clean_name(job, "job", optional=True) or NO_JOB
        client, job = self._canon(client, job)
        key = self._jkey(client, job)
        replaced = [x for x in self.mlinks if self._jkey(x["client"], x["job"]) == key or x["item"] == item]
        keep = [x for x in self.mlinks if x not in replaced]
        if len(keep) >= MAX_RULES:
            raise ValueError("limite de ligações atingido")
        entry = {"id": self._new_id(), "client": client, "job": job, "item": item, "item_name": CTRL.sub("", str(item_name or ""))[:200],
                 "created_at": datetime.now().isoformat(timespec="seconds")}
        old = self.mlinks
        self.mlinks = keep + [entry]
        try:
            self._save_rules()
        except OSError:
            self.mlinks = old
            raise ValueError("não consegui gravar a ligação") from None
        return {"link": entry, "replaced": replaced}

    def mlink_delete(self, ids):
        ids = {ids} if isinstance(ids, str) else set(ids or [])
        gone = [x for x in self.mlinks if x["id"] in ids]
        if gone:
            old = self.mlinks
            self.mlinks = [x for x in self.mlinks if x["id"] not in ids]
            try:
                self._save_rules()
            except OSError:
                self.mlinks = old
                raise ValueError("não consegui gravar") from None
        return {"removed": gone}

    def mlink_restore(self, entries):
        if not isinstance(entries, list) or len(entries) > 20:
            raise ValueError("nada para restaurar")
        add = [x for x in entries if self._mlink_ok(x)]
        for x in add:
            self.mlinks = [y for y in self.mlinks if y["id"] != x["id"] and y["item"] != x["item"] and self._jkey(y["client"], y["job"]) != self._jkey(x["client"], x["job"])]
            self.mlinks.append({k: x[k] for k in ("id", "client", "job", "item", "item_name", "created_at") if k in x})
        self._save_rules()
        return {"restored": len(add)}

    def mlink_times(self, today=None):
        """{id do item: {today_s, week_s, total_s}} dos jobs ligados (lido do histórico; regras/ignorados valem)."""
        if not self.mlinks:
            return {}
        today = today or date.today()
        mon = today - timedelta(days=today.weekday())
        by_job = {self._jkey(x["client"], x["job"]): x["item"] for x in self.mlinks}
        out = {x["item"]: {"today_s": 0.0, "week_s": 0.0, "total_s": 0.0} for x in self.mlinks}
        for d, client, job, _a, _f, s_ in self._rows(date(2000, 1, 1), today):
            it = by_job.get(self._jkey(client, job))
            if it:
                t = out[it]
                t["total_s"] += s_
                if d >= mon:
                    t["week_s"] += s_
                if d == today:
                    t["today_s"] += s_
        return {c: {k: int(v) for k, v in t.items()} for c, t in out.items()}

    def mlinks_get(self, today=None):
        times = self.mlink_times(today)
        return [{**x, **times.get(x["item"], {"today_s": 0, "week_s": 0, "total_s": 0})} for x in self.mlinks]

    # -- Regras manuais (work_rules.json) ------------------------------------------
    # rules   [{id, file, app|None, client, job, created_at}]  vale sempre (nome de arquivo NÃO genérico)
    # once    [{id, file, app, client, job, from, to, created_at}]  atribuição pontual de um período (qualquer nome)
    # ignored [{id, file, app, created_at, from?, to?}]  "Não é trabalho"; nome genérico só com período
    # Só casam com linhas "Não identificado" do histórico, por IGUALDADE do nome (casefold), nunca por parecido.
    def _load_rules(self):
        def ok(d):
            return isinstance(d, dict) and all(isinstance(d.get(k, []), list) and all(isinstance(x, dict) for x in d.get(k, []))
                                               for k in ("rules", "once", "ignored", "links", "mlinks"))
        p = self.rules_path
        try:
            d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
            if d is not None and not ok(d):
                raise ValueError("estrutura inesperada")
        except (OSError, ValueError) as e:
            bad = p.with_name(p.name + ".bad-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
            try:
                p.replace(bad)
            except OSError:
                pass
            log.warning("work_rules.json inválido (%s): guardado como %s, começando vazio", type(e).__name__, bad.name)
            d = None
        d = d or {}
        self.rules, self.once, self.ignored = (list(d.get(k, [])) for k in ("rules", "once", "ignored"))
        self.links = [x for x in d.get("links", []) if self._link_ok(x)]   # ligações job ↔ card do Trello
        self.mlinks = [x for x in d.get("mlinks", []) if self._mlink_ok(x)]   # ligações job ↔ item do monday
        self._reindex_rules()

    def _reindex_rules(self):
        self._rule_idx = {}
        for r in self.rules:
            self._rule_idx[(str(r.get("file", "")).casefold(), (r.get("app") or "").casefold())] = r

    def _save_rules(self):
        _save_json(self.rules_path, {"version": 1, "rules": self.rules, "once": self.once, "ignored": self.ignored, "links": self.links,
                                      "mlinks": self.mlinks})
        self._reindex_rules()

    @staticmethod
    def _in_period(e, d):
        iso = d.isoformat() if isinstance(d, date) else str(d)
        return (not e.get("from") or iso >= e["from"]) and (not e.get("to") or iso <= e["to"])

    def _decide(self, f, app, d):
        """(tipo, entrada) do que vale para o item (arquivo, app) no dia d: ignorar > pontual > regra; ou None."""
        fk, ak = (f or "").casefold(), (app or "").casefold()
        for e in self.ignored:
            if str(e.get("file", "")).casefold() == fk and (not e.get("app") or e["app"].casefold() == ak) and self._in_period(e, d):
                return "ignore", e
        for e in self.once:
            if str(e.get("file", "")).casefold() == fk and (e.get("app") or "").casefold() == ak and self._in_period(e, d):
                return "assign", e
        r = self._rule_idx.get((fk, ak)) or self._rule_idx.get((fk, ""))
        if r and not is_generic(f):
            return "assign", r
        return None

    def _manual(self, f, app, d):
        """("ignore",) | ("assign", cliente, job) | None."""
        m = self._decide(f, app, d)
        if not m:
            return None
        return ("ignore",) if m[0] == "ignore" else ("assign", m[1]["client"], m[1]["job"])

    @staticmethod
    def _clean_name(v, what, optional=False):
        v = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
        if not v:
            if optional:
                return NO_JOB
            raise ValueError(f"{what}: informe um nome")
        if v == NO_JOB and optional:
            return NO_JOB
        if len(v) > NAME_MAX or CTRL.search(v):
            raise ValueError(f"{what}: de 1 a {NAME_MAX} caracteres, sem caracteres de controle")
        return v

    def _canon(self, client, job):
        """Se já existe cliente/job igual (sem diferenciar maiúsc./minúsc.), usa a grafia existente."""
        if client.casefold() == UNIDENTIFIED.casefold():
            raise ValueError("esse nome é reservado")
        for c, jobs in self.catalog().items():
            if c.casefold() == client.casefold():
                client = c
                for j in jobs:
                    if j.casefold() == job.casefold():
                        job = j
                break
        return client, job

    def catalog(self):
        """{cliente: [jobs]} dos que já existem: pastas das raízes (2 níveis), histórico e regras."""
        cat = {}

        def add(c, j=None):
            if c and c != UNIDENTIFIED:
                lst = cat.setdefault(c, [])
                if j and j != NO_JOB and j not in lst:
                    lst.append(j)
        for root in self.roots:
            try:
                with os.scandir(real_path(root)) as it:
                    for e in list(it)[:300]:
                        if not e.is_dir(follow_symlinks=False):
                            continue
                        add(e.name)
                        try:
                            with os.scandir(e.path) as it2:
                                for e2 in list(it2)[:300]:
                                    isd = e2.is_dir(follow_symlinks=False)
                                    add(e.name, e2.name if isd else ntpath.splitext(e2.name)[0])
                        except OSError:
                            pass
            except OSError:
                continue
        for items in self.days.values():
            for k in items:
                parts = k.split("\t")
                if len(parts) >= 2:
                    add(parts[0], parts[1])
        for e in self.rules + self.once:
            add(e["client"], e["job"])
        return {c: sorted(j, key=str.casefold) for c, j in sorted(cat.items(), key=lambda x: x[0].casefold())}

    @staticmethod
    def _items(items):
        if not isinstance(items, list) or not 1 <= len(items) <= 200:
            raise ValueError("escolha de 1 a 200 itens")
        out = []
        for it in items:
            f, a = (it.get("file"), it.get("app")) if isinstance(it, dict) else (None, None)
            if not isinstance(f, str) or not f or len(f) > FOLDER_MAX or CTRL.search(f) or a not in APPS.values():
                raise ValueError("item inválido")
            out.append((f, a))
        return out

    def _unident_sums(self, items, offset, today=None):
        """{(arquivo casefold, app casefold): [segundos da semana, segundos no total]} só de linhas "Não identificado"
        ainda sem regra, pontual ou ignorado."""
        mon, sun = self._week_bounds(offset, today)
        out = {(f.casefold(), a.casefold()): [0.0, 0.0] for f, a in items}
        for ds, day in self.days.items():
            try:
                d = date.fromisoformat(ds)
            except ValueError:
                continue
            for k, s in day.items():
                parts = k.split("\t")
                if len(parts) < 4 or parts[0] != UNIDENTIFIED:
                    continue
                w = (parts[3].casefold(), parts[2].casefold())
                if w in out and not self._manual(parts[3], parts[2], d):
                    out[w][1] += s
                    if mon <= d <= sun:
                        out[w][0] += s
        return out

    def assign_preview(self, items, offset=0, today=None):
        """O que mudaria: por item, tempo da semana e no total, e se o nome é genérico (só pontual). Não grava."""
        its = self._items(items)
        sums = self._unident_sums(its, offset, today)
        rows = []
        for f, a in its:
            w, t = sums[(f.casefold(), a.casefold())]
            rows.append({"file": f, "app": a, "generic": is_generic(f), "week_s": int(w), "total_s": int(t)})
        return {"items": rows, "week_s": sum(r["week_s"] for r in rows), "total_s": sum(r["total_s"] for r in rows)}

    @staticmethod
    def _new_id():
        import secrets
        return secrets.token_hex(4)

    def assign(self, items, client, job="", remember=True, offset=0, today=None):
        """Atribui os itens a cliente/job. Nome genérico (ou remember falso): só a semana mostrada (once).
        Senão: regra para sempre (rules). Devolve os ids criados para o desfazer."""
        its = self._items(items)
        client = self._clean_name(client, "cliente")
        job = self._clean_name(job, "job", optional=True)
        client, job = self._canon(client, job)
        mon, sun = self._week_bounds(offset, today)
        sums = self._unident_sums(its, offset, today)
        stamp = datetime.now().isoformat(timespec="seconds")
        new_rules, new_once, seen = [], [], set()
        for f, a in its:
            if remember and not is_generic(f):
                key = (f.casefold(), a.casefold())
                if key in self._rule_idx or key in seen:
                    raise ValueError("já existe uma regra para esse arquivo")
                seen.add(key)
                new_rules.append({"id": self._new_id(), "file": f, "app": a, "client": client, "job": job, "created_at": stamp})
            else:
                new_once.append({"id": self._new_id(), "file": f, "app": a, "client": client, "job": job,
                                 "from": mon.isoformat(), "to": sun.isoformat(), "created_at": stamp})
        if len(self.rules) + len(new_rules) > MAX_RULES or len(self.once) + len(new_once) > MAX_RULES:
            raise ValueError("limite de regras atingido: apague alguma")
        old = (list(self.rules), list(self.once))
        self.rules += new_rules
        self.once += new_once
        try:
            self._save_rules()
        except OSError as e:
            self.rules, self.once = old
            self._reindex_rules()
            raise ValueError("não consegui gravar as regras; nada foi mudado") from e
        moved = [sums[(f.casefold(), a.casefold())] for f, a in its]
        return {"ids": [e["id"] for e in new_rules + new_once], "rules": len(new_rules), "once": len(new_once),
                "client": client, "job": job, "week_s": int(sum(m[0] for m in moved)),
                "total_s": int(sum(m[1] for m in moved))}

    def ignore(self, items, offset=0, today=None):
        """"Não é trabalho": sai dos totais e do CSV, continua guardado. Nome genérico: só a semana mostrada."""
        its = self._items(items)
        mon, sun = self._week_bounds(offset, today)
        stamp = datetime.now().isoformat(timespec="seconds")
        new = []
        for f, a in its:
            e = {"id": self._new_id(), "file": f, "app": a, "created_at": stamp}
            if is_generic(f):
                e.update({"from": mon.isoformat(), "to": sun.isoformat()})
            new.append(e)
        if len(self.ignored) + len(new) > MAX_RULES:
            raise ValueError("limite de ignorados atingido: restaure algum")
        old = list(self.ignored)
        self.ignored += new
        try:
            self._save_rules()
        except OSError as e:
            self.ignored = old
            self._reindex_rules()
            raise ValueError("não consegui gravar; nada foi mudado") from e
        return {"ids": [e["id"] for e in new]}

    def _remove(self, names, ids):
        ids = [ids] if isinstance(ids, str) else ids
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError("id inválido")
        old = {a: list(getattr(self, a)) for a in ("rules", "once", "ignored")}
        n = 0
        for a in names:
            keep = [e for e in getattr(self, a) if e.get("id") not in ids]
            n += len(getattr(self, a)) - len(keep)
            setattr(self, a, keep)
        if not n:
            raise ValueError("não encontrado")
        try:
            self._save_rules()
        except OSError as e:
            for a, v in old.items():
                setattr(self, a, v)
            self._reindex_rules()
            raise ValueError("não consegui gravar; nada foi mudado") from e
        return {"removed": n}

    def rule_delete(self, ids):
        """Apaga regra ou atribuição pontual: o tempo volta a "Não identificado" (nada no histórico muda)."""
        return self._remove(("rules", "once"), ids)

    def unignore(self, ids):
        return self._remove(("ignored",), ids)

    def rules_get(self, today=None):
        """Regras, pontuais e ignorados com o tempo que cada um move (semana atual e total) + catálogo de clientes/jobs."""
        today = today or date.today()
        mon, sun = self._week_bounds(0, today)
        use = {}
        for ds, day in self.days.items():
            try:
                d = date.fromisoformat(ds)
            except ValueError:
                continue
            for k, s in day.items():
                parts = k.split("\t")
                if len(parts) >= 4 and parts[0] == UNIDENTIFIED:
                    m = self._decide(parts[3], parts[2], d)
                    if m:
                        u = use.setdefault(m[1]["id"], [0.0, 0.0])
                        u[1] += s
                        if mon <= d <= sun:
                            u[0] += s

        def row(e, kind):
            u = use.get(e["id"], [0, 0])
            r = {k: v for k, v in e.items() if k != "created_at"}
            r.update({"kind": kind, "generic": is_generic(e.get("file")), "week_s": int(u[0]), "total_s": int(u[1])})
            return r
        return {"rules": [row(e, "rule") for e in self.rules], "once": [row(e, "once") for e in self.once],
                "ignored": [row(e, "ignored") for e in self.ignored], "catalog": self.catalog()}

    # -- resumos --------------------------------------------------------------
    def _rows(self, d0, d1):
        """[(dia, cliente, job, app, arquivo, segundos)] de d0 a d1 (inclusive), com as regras manuais aplicadas;
        os ignorados ficam de fora (continuam no histórico)."""
        out = []
        for ds, items in self.days.items():
            try:
                d = date.fromisoformat(ds)
            except ValueError:
                continue
            if d0 <= d <= d1:
                for k, s in items.items():
                    client, job, app, f = (k.split("\t") + ["", "", "", ""])[:4]
                    if client == UNIDENTIFIED:
                        m = self._manual(f, app, d)
                        if m and m[0] == "ignore":
                            continue
                        if m:
                            client, job = m[1], m[2]
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
                                       "files": [{"app": a, "file": f, "s": int(s), "generic": is_generic(f)} for (a, f), s in by_s(j["files"])]}
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

    # -- histórico mensal e notas do dia (só leitura do histórico; nada é apagado) -----------------
    def _month_tree(self, ym):
        first, last = month_bounds(ym)
        per_day, tree = {}, {}
        for d, client, job, _app, _f, s_ in self._rows(first, last):
            per_day[d.isoformat()] = per_day.get(d.isoformat(), 0.0) + s_
            c = tree.setdefault(client, {"s": 0.0, "jobs": {}})
            c["s"] += s_
            c["jobs"][job] = c["jobs"].get(job, 0.0) + s_
        return first, last, per_day, tree

    def month(self, ym=None, today=None, notes=True):
        """Calendário e totais do mês (mesmas regras manuais/ignorados da semana) + comparação com o mês anterior."""
        ym = valid_month(ym, today)
        first, last, per_day, tree = self._month_tree(ym)
        pym = prev_month(ym)
        _f, _l, pdays, ptree = self._month_tree(pym) if pym >= "2000-01" else (None, None, {}, {})
        today = today or date.today()
        n = (last - first).days + 1
        days = []
        for i in range(n):
            d = first + timedelta(days=i)
            e = {"date": d.isoformat(), "wd": d.weekday(), "s": int(per_day.get(d.isoformat(), 0)), "today": d == today}
            if notes and d.isoformat() in self.notes:
                e["note"] = self.notes[d.isoformat()]
            days.append(e)
        clients = []
        for cn, c in sorted(tree.items(), key=lambda kv: -kv[1]["s"]):
            pc = ptree.get(cn, {"s": 0.0, "jobs": {}})
            clients.append({"client": cn, "s": int(c["s"]), "prev_s": int(pc["s"]), "unidentified": cn == UNIDENTIFIED,
                            "jobs": [{"job": jn, "s": int(js), "prev_s": int(pc["jobs"].get(jn, 0))}
                                     for jn, js in sorted(c["jobs"].items(), key=lambda kv: -kv[1])]})
        return {"month": ym, "start": first.isoformat(), "end": last.isoformat(), "prev": pym,
                "total_s": int(sum(per_day.values())), "prev_total_s": int(sum(pdays.values())),
                "days_worked": sum(1 for v in per_day.values() if v >= 60),
                "has_prev": any(k.startswith(pym) for k in self.days), "days": days, "clients": clients}

    @staticmethod
    def _note_text(v):
        if not isinstance(v, str):
            raise ValueError("nota inválida")
        v = NOTE_CTRL.sub("", v.replace("\r\n", "\n").replace("\r", "\n")).strip()
        if len(v) > NOTE_MAX:
            raise ValueError(f"a nota passa de {NOTE_MAX} caracteres")
        return v

    def note_set(self, day, text):
        """Nota do dia (até 500 caracteres); vazia apaga. Grava work-notes.json de forma atômica."""
        try:
            d = date.fromisoformat(day)
        except (TypeError, ValueError):
            raise ValueError("dia inválido") from None
        if not date(2000, 1, 1) <= d <= date(2099, 12, 31):
            raise ValueError("dia inválido")
        text = self._note_text(text)
        new = dict(self.notes)
        if text:
            new[d.isoformat()] = text
        else:
            new.pop(d.isoformat(), None)
        _save_json(self.notes_path, {"version": 1, "notes": new})
        self.notes = new
        return {"date": d.isoformat(), "note": text}

    def csv_month(self, ym=None, today=None):
        """(nome, bytes) do mês: data;cliente;job;app;arquivo;minutos;nota (a nota só na 1ª linha do dia; dia só com nota ganha uma linha)."""
        ym = valid_month(ym, today)
        first, last = month_bounds(ym)

        def cell(v):
            v = str(v)
            if v[:1] in ("=", "+", "-", "@", "\t", "\r"):
                v = "'" + v
            if any(c in v for c in ';"\r\n'):
                v = '"' + v.replace('"', '""') + '"'
            return v

        lines, noted = ["data;cliente;job;app;arquivo;minutos;nota"], set()
        rows = sorted(self._rows(first, last))
        for d, client, job, app, f, s_ in rows:
            nota = "" if d in noted else self.notes.get(d.isoformat(), "")
            noted.add(d)
            lines.append(";".join([d.strftime("%d/%m/%Y"), cell(client), cell(job), cell(app), cell(f),
                                   f"{s_ / 60:.1f}".replace(".", ","), cell(nota) if nota else ""]))
        for ds, text in sorted(self.notes.items()):
            d = date.fromisoformat(ds)
            if first <= d <= last and d not in noted:
                lines.append(";".join([d.strftime("%d/%m/%Y"), "", "", "", "", "0", cell(text)]))
        return f"trabalho-{ym}.csv", ("\ufeff" + "\r\n".join(lines) + "\r\n").encode("utf-8")
