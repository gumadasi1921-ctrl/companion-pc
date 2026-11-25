"""Ícones reais dos aplicativos: extrai do .exe/.lnk no PC, converte para PNG 64 px e serve por uma rota autenticada.

- O aparelho só conhece um ID opaco (12 hex = hash do caminho). O mapa ID -> caminho fica só na memória do
  servidor e é preenchido pelo próprio servidor (processos, programas do deck, jogos). Nenhum caminho, nome de
  exe ou nome de usuário vai ao celular, e a rota nunca recebe caminho.
- Rota: GET /icon/<id>?k=<chave>. A chave é um HMAC do token (rótulo próprio); comparação em tempo constante.
- Cache em disco (icon-cache/, um PNG por exe, nome <id>-<data>-<tamanho>.png: se o exe mudou, o antigo é apagado),
  com limite de arquivos e de bytes (apaga os menos usados). Falha de extração fica só em memória por 10 min.
- Sem ícone ou erro = 404 sem corpo: o app mantém a letra/emoji de antes. Nada é baixado da internet.
- Log: nunca caminho, nome de exe nem ID; só o tipo do erro.
"""
import hashlib
import hmac
import logging
import os
import re
import threading
import time
import urllib.parse
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger("companion.icons")

SIZE = 64
KEY_LABEL = b"app-icon-v1"
MAX_FILES = 300
MAX_BYTES = 8 * 1024 * 1024
MAX_REGISTRY = 3000
NEG_TTL_S = 600
BROWSER_MAX_AGE = 86400
PNG = b"\x89PNG\r\n\x1a\n"
_ID = re.compile(r"[0-9a-f]{12}")
EXTS = (".exe", ".lnk")


def icon_key(token):
    return hmac.new(str(token).encode("utf-8"), KEY_LABEL, hashlib.sha256).hexdigest()[:24]


def icon_id(path):
    return hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8", "surrogatepass")).hexdigest()[:12]


def _reply(status, body=b"", ctype="text/plain; charset=utf-8", cache="no-store"):
    return status, ctype, body, {"Cache-Control": cache, "X-Content-Type-Options": "nosniff"}


# --------------------------------------------------------------------------
# Extração no Windows (só ctypes + PIL)
# --------------------------------------------------------------------------
def _hicon_to_image(hicon):
    import ctypes
    from ctypes import wintypes
    from PIL import Image

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                    ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                    ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                    ("bmBits", ctypes.c_void_p)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    gdi32.GetObjectW.argtypes = [wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    user32.GetDC.restype = wintypes.HDC
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    ii = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(ii)):
        return None
    hdc = user32.GetDC(None)
    try:
        bm = BITMAP()
        if not gdi32.GetObjectW(ii.hbmColor or ii.hbmMask, ctypes.sizeof(bm), ctypes.byref(bm)):
            return None
        w, h = bm.bmWidth, abs(bm.bmHeight)
        if not ii.hbmColor:      # ícone só de máscara (monocromático): não vale a pena
            return None
        if ii.hbmMask:           # numa máscara de ícone a altura vale 2x
            pass

        def bits(hbm):
            bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            if gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bi), 0) != h:
                return None
            return buf.raw

        color = bits(ii.hbmColor)
        if color is None:
            return None
        img = Image.frombuffer("RGBA", (w, h), color, "raw", "BGRA", 0, 1)
        if not any(color[3::4]):                 # sem canal alfa (ícone antigo): usa a máscara
            mask = bits(ii.hbmMask) if ii.hbmMask else None
            if mask is not None and len(mask) == w * h * 4:
                alpha = bytes(0 if mask[i] else 255 for i in range(0, len(mask), 4))
            else:
                alpha = b"\xff" * (w * h)
            img.putalpha(Image.frombuffer("L", (w, h), alpha, "raw", "L", 0, 1))
        return img
    finally:
        user32.ReleaseDC(None, hdc)
        if ii.hbmColor:
            gdi32.DeleteObject(ii.hbmColor)
        if ii.hbmMask:
            gdi32.DeleteObject(ii.hbmMask)


def _png(img):
    import io
    from PIL import Image
    if img.size != (SIZE, SIZE):
        side = max(img.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = canvas.resize((SIZE, SIZE), Image.LANCZOS)
    if img.getchannel("A").getbbox() is None:
        return None                          # totalmente transparente: sem ícone de verdade
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def _shell_icon(path, attrs_only=False):
    """Ícone (HICON) pelo Shell: usado para .lnk e outros que não têm recurso de ícone próprio."""
    import ctypes
    from ctypes import wintypes

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]
    shell = ctypes.windll.shell32
    shell.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
    shell.SHGetFileInfoW.restype = ctypes.c_size_t
    fi = SHFILEINFOW()
    flags = 0x100 | 0x0           # SHGFI_ICON | grande (32 px)
    if attrs_only:
        flags |= 0x10            # SHGFI_USEFILEATTRIBUTES
    return fi.hIcon if shell.SHGetFileInfoW(path, 0x80 if attrs_only else 0, ctypes.byref(fi), ctypes.sizeof(fi), flags) and fi.hIcon else None


_GENERIC = {}


def _generic_png(ext):
    """PNG do ícone genérico do Windows para essa extensão (para reconhecer e descartar)."""
    if ext not in _GENERIC:
        import ctypes
        h = _shell_icon("x" + ext, attrs_only=True)
        png = None
        if h:
            try:
                img = _hicon_to_image(h)
                png = _png(img) if img else None
            finally:
                ctypes.windll.user32.DestroyIcon(h)
        _GENERIC[ext] = png
    return _GENERIC[ext]


def extract_png(path):
    """PNG 64 px do ícone do arquivo, ou None (sem ícone, genérico, arquivo ausente ou erro)."""
    import ctypes
    from ctypes import wintypes
    if not os.path.isfile(path):
        return None
    user32 = ctypes.windll.user32
    ext = os.path.splitext(path)[1].lower()
    hicon = None
    try:
        if ext == ".exe":
            user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                                    ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
                                                    wintypes.UINT, wintypes.UINT]
            hi, ids = wintypes.HICON(), wintypes.UINT()
            if user32.PrivateExtractIconsW(path, 0, SIZE, SIZE, ctypes.byref(hi), ctypes.byref(ids), 1, 0) >= 1 and hi.value:
                hicon = hi
        else:
            hicon = _shell_icon(path)
        if not hicon:
            return None
        img = _hicon_to_image(hicon)
        png = _png(img) if img else None
        if png and ext != ".exe" and png == _generic_png(ext):
            return None                      # o Windows só devolveu o ícone genérico da extensão
        return png
    except Exception as e:  # noqa: BLE001
        log.warning("ícone: erro (%s)", type(e).__name__)
        return None
    finally:
        if hicon:
            user32.DestroyIcon(hicon)


# --------------------------------------------------------------------------
class Icons:
    def __init__(self, cache_dir, token, extractor=None, clock=time.monotonic, max_files=MAX_FILES, max_bytes=MAX_BYTES):
        self.cache_dir = Path(cache_dir)
        self._token = token              # função: devolve o token atual
        self.extractor = extractor or extract_png
        self.clock = clock
        self.max_files, self.max_bytes = max_files, max_bytes
        self.paths = OrderedDict()       # id -> caminho (só memória)
        self.neg = {}                    # id -> até quando não tenta de novo
        self.lock = threading.Lock()

    def key(self):
        return icon_key(self._token())

    def register(self, path):
        """ID opaco para um .exe/.lnk (ou None). Não toca o disco: pode ser chamado ao montar um payload."""
        if not isinstance(path, str) or not path or len(path) > 520 or "\x00" in path:
            return None
        if os.path.splitext(path)[1].lower() not in EXTS:
            return None
        i = icon_id(path)
        with self.lock:
            self.paths[i] = path
            self.paths.move_to_end(i)
            while len(self.paths) > MAX_REGISTRY:
                self.paths.popitem(last=False)
        return i

    # ---------------------------------------------------------------- pedido
    def handle(self, target):
        """(status, content-type, corpo, cabeçalhos) para /icon/<id>?k=<chave>."""
        path, _, query = target.partition("?")
        parts = path.split("/")
        if len(parts) != 3 or parts[:2] != ["", "icon"]:
            return _reply(404)
        try:
            q = urllib.parse.parse_qs(query, keep_blank_values=True, max_num_fields=4)
        except ValueError:
            return _reply(400)
        keys = q.get("k", [])
        if len(keys) != 1 or not hmac.compare_digest(keys[0].encode("utf-8"), self.key().encode("utf-8")):
            return _reply(403)
        ident = parts[2]
        if not _ID.fullmatch(ident):
            return _reply(404)
        body = self._get(ident)
        if body is None:
            return _reply(404)
        return 200, "image/png", body, {"Cache-Control": f"private, max-age={BROWSER_MAX_AGE}", "X-Content-Type-Options": "nosniff"}

    def _get(self, ident):
        with self.lock:
            src = self.paths.get(ident)
        stamp = None
        if src:
            try:
                st = os.stat(src)
                stamp = f"{st.st_mtime_ns:x}-{st.st_size:x}"
            except OSError:
                return None                  # o exe sumiu: sem ícone (o cache antigo não vale para um arquivo que não existe)
        cached = self._read(ident, stamp)
        if cached is not None:
            return cached
        if not src or self.neg.get(ident, 0) > self.clock():
            return None
        png = self.extractor(src)
        if not png:
            self.neg[ident] = self.clock() + NEG_TTL_S
            return None
        self.neg.pop(ident, None)
        self._store(ident, stamp, png)
        return png

    # ---------------------------------------------------------------- disco
    def _files(self, ident):
        try:
            return sorted(self.cache_dir.glob(f"{ident}-*.png"))
        except OSError:
            return []

    def _read(self, ident, stamp):
        files = self._files(ident)
        for f in files:
            if stamp is not None and f.name != f"{ident}-{stamp}.png":
                self._drop(f)                # exe mudou: o ícone antigo sai
                continue
            try:
                body = f.read_bytes()
            except OSError:
                continue
            if not body.startswith(PNG):
                self._drop(f)
                continue
            try:
                os.utime(f)                  # usado agora: não é o primeiro a sair
            except OSError:
                pass
            return body
        return None

    @staticmethod
    def _drop(f):
        try:
            f.unlink()
        except OSError:
            pass

    def _store(self, ident, stamp, png):
        with self.lock:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                name = f"{ident}-{stamp}.png"
                tmp = self.cache_dir / f".{name}.tmp"
                tmp.write_bytes(png)
                os.replace(tmp, self.cache_dir / name)
            except OSError as e:
                log.warning("ícone: não consegui gravar no cache (%s)", type(e).__name__)
                return
            self._evict()

    def _evict(self):
        try:
            files = [(f, f.stat()) for f in self.cache_dir.glob("*-*.png")]
        except OSError:
            return
        files.sort(key=lambda x: x[1].st_mtime)
        total = sum(s.st_size for _, s in files)
        while files and (len(files) > self.max_files or total > self.max_bytes):
            f, s = files.pop(0)
            total -= s.st_size
            self._drop(f)
