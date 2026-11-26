"""Proxy de imagens da ponte do DotaCopilot (docs/dota-bridge.md, "Imagens").

GET /dota/img/hero/<id>?kind=icon|portrait|loadout&k=<chave> e GET /dota/img/item/<id>?k=<chave>.
Só ids numéricos e kinds da lista chegam à busca: nunca URL nem caminho livre. A imagem vem da ponte local
(http://127.0.0.1:<porta>/companion/v1/img/..., urllib, timeout 2 s, sem proxy do sistema) e fica em disco
(dota-img/ ao lado do servidor, limite de 50 MB: apaga as mais antigas); as próximas vezes saem do disco.
404 da ponte = 404 aqui e NADA é guardado (a imagem pode ser extraída depois); ponte fora do ar = 502, e por 10 s
nem tenta de novo (a fila de disco do servidor é uma só).

Autenticação: o <img> do navegador não manda cabeçalho, então a URL leva uma chave derivada do token do
aparelho (HMAC-SHA256 do token com um rótulo fixo, 24 hex), entregue ao app dentro da mensagem {t:dota}. O token
em si nunca vai para a URL. Sem a chave certa = 403.

Roda na fila de disco do servidor (IO_POOL). No log nunca entram imagem, id nem nome de arquivo."""
import hashlib
import hmac
import http.client
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KINDS = ("icon", "portrait", "loadout")
DEFAULT_KIND = "portrait"          # o mesmo padrão da ponte
CACHE_MAX_BYTES = 50 * 1024 * 1024
MAX_IMAGE_BYTES = 1024 * 1024      # a maior (loadout 448x678) tem ~270 KB
TIMEOUT_S = 2.0
DOWN_FOR_S = 10
BROWSER_MAX_AGE = 604800           # 7 dias
CDN_HOST = "https://cdn.cloudflare.steamstatic.com"
PNG = b"\x89PNG\r\n\x1a\n"
KEY_LABEL = b"dota-img-v1"
_ID = re.compile(r"[0-9]{1,10}")
_NAME = re.compile(r"(hero-[0-9]+-(icon|portrait|loadout)|item-[0-9]+|rank-[0-8]|star-[1-5])\.png|avatar-[0-9a-f]{40}\.jpg")
JPG = b"\xff\xd8\xff"
_EXT = {"rank": re.compile(r"[0-8]"), "star": re.compile(r"[1-5]"), "avatar": re.compile(r"[0-9a-f]{40}")}


def image_key(token):
    """Chave de URL derivada do token do aparelho (o token nunca vai para a URL)."""
    return hmac.new(str(token).encode("utf-8"), KEY_LABEL, hashlib.sha256).hexdigest()[:24]


def _reply(status, body=b"", ctype="text/plain; charset=utf-8", cache="no-store"):
    return status, ctype, body, {"Cache-Control": cache, "X-Content-Type-Options": "nosniff"}


class ImageProxy:
    def __init__(self, cache_dir, token, bridge_url, opener=None, clock=time.monotonic, cache_max=CACHE_MAX_BYTES,
                 cdn_path=None, cdn_opener=None, ext_url=None):
        self.cache_dir = Path(cache_dir)
        self._token = token            # função: devolve o token atual
        self._bridge_url = bridge_url  # função: devolve a URL do estado da ponte (config "dota.bridge")
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
        self.clock = clock
        self.cache_max = cache_max
        self.down_until = 0.0
        # sem o Copilot: imagem estática do CDN da Valve, só para id JÁ conhecido (cdn_path(kind, id) = caminho validado do
        # OpenDota/constants, ou None) e só https do host fixo; nada de conta ou Steam ID vai junto
        self.cdn_path = cdn_path
        # medalha/estrela (host do OpenDota) e avatar do perfil (steamstatic): ext_url(kind, id) só devolve URL de id conhecido
        self.ext_url = ext_url
        self.cdn_opener = cdn_opener or urllib.request.build_opener().open
        self.cdn_down_until = 0.0
        self.lock = threading.Lock()

    def key(self):
        return image_key(self._token())

    # ---------------------------------------------------------------- pedido
    def parse(self, target):
        """(arquivo_no_cache, caminho_na_ponte) ou (None, resposta_de_erro)."""
        path, _, query = target.partition("?")
        parts = path.split("/")
        if len(parts) != 5 or parts[:3] != ["", "dota", "img"] or parts[3] not in ("hero", "item", "rank", "star", "avatar"):
            return None, _reply(404, b"nao encontrado\n")
        try:
            q = urllib.parse.parse_qs(query, keep_blank_values=True, max_num_fields=8)
        except ValueError:
            return None, _reply(400, b"pedido invalido\n")
        keys = q.get("k", [])
        if len(keys) != 1 or not hmac.compare_digest(keys[0].encode("utf-8"), self.key().encode("utf-8")):
            return None, _reply(403, b"recusado\n")
        if parts[3] in _EXT:
            if not _EXT[parts[3]].fullmatch(parts[4]):
                return None, _reply(400, b"id invalido\n")
            ext = "jpg" if parts[3] == "avatar" else "png"
            return (f"{parts[3]}-{parts[4]}.{ext}", ("ext", parts[3], parts[4])), None
        if not _ID.fullmatch(parts[4]) or int(parts[4]) <= 0:
            return None, _reply(400, b"id invalido\n")
        ident = int(parts[4])
        if parts[3] == "item":
            return (f"item-{ident}.png", f"item/{ident}"), None
        kinds = q.get("kind", [DEFAULT_KIND])
        if len(kinds) != 1 or kinds[0] not in KINDS:
            return None, _reply(400, b"kind invalido\n")
        return (f"hero-{ident}-{kinds[0]}.png", f"hero/{ident}?kind={kinds[0]}"), None

    def handle(self, target):
        """(status, content-type, corpo, cabeçalhos) para o caminho pedido (com a query)."""
        job, error = self.parse(target)
        if error:
            return error
        name, remote = job
        cached = self._read_cache(name)
        if cached is not None:
            return self._ok(cached, "image/jpeg" if name.endswith(".jpg") else "image/png")
        if isinstance(remote, tuple):      # imagem externa de id conhecido
            body = self._ext(remote[1], remote[2], name)
            if body is None:
                return _reply(404, b"imagem indisponivel\n")
            self._store(name, body)
            return self._ok(body, "image/jpeg" if name.endswith(".jpg") else "image/png")
        fetched = self._fetch(remote)
        if isinstance(fetched, tuple):   # erro: 404 da ponte (não guarda) ou 502
            cdn = self._cdn(name)
            if cdn is None:
                return fetched
            fetched = cdn
        self._store(name, fetched)
        return self._ok(fetched)

    @staticmethod
    def _ok(body, ctype="image/png"):
        return 200, ctype, body, {"Cache-Control": f"max-age={BROWSER_MAX_AGE}", "X-Content-Type-Options": "nosniff"}

    def _ext(self, kind, ident, name):
        """Corpo (PNG ou JPEG conforme o nome) de uma imagem externa de id conhecido, ou None."""
        if self.ext_url is None or self.clock() < self.cdn_down_until:
            return None
        arg = int(ident) if kind in ("rank", "star") else ident
        url = self.ext_url(kind, arg)
        if not url or not url.startswith(("https://www.opendota.com/assets/images/dota2/rank_icons/", "https://avatars.steamstatic.com/")):
            return None
        try:
            with self.cdn_opener(urllib.request.Request(url, headers={"Accept": "image/*"}), timeout=5) as resp:
                body = resp.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError:
            return None
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, TimeoutError):
            self.cdn_down_until = self.clock() + DOWN_FOR_S
            return None
        sig = JPG if name.endswith(".jpg") else PNG
        return body if len(body) <= MAX_IMAGE_BYTES and body.startswith(sig) else None   # HTML de erro/página não vira imagem

    def _cdn(self, name):
        """PNG do CDN da Valve para o arquivo pedido, ou None (sem fonte/erro: vale a resposta de erro da ponte)."""
        if self.cdn_path is None or self.clock() < self.cdn_down_until:
            return None
        m = re.fullmatch(r"hero-([0-9]+)-(icon|portrait|loadout)\.png|item-([0-9]+)\.png", name)
        if not m:
            return None
        path = self.cdn_path("item", int(m.group(3))) if m.group(3) else self.cdn_path(m.group(2), int(m.group(1)))
        if not path or not path.startswith("/apps/dota2/images/") or ".." in path or "?" in path:
            return None
        try:
            with self.cdn_opener(urllib.request.Request(CDN_HOST + path, headers={"Accept": "image/png"}), timeout=4) as resp:
                body = resp.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError:
            return None
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, TimeoutError):
            self.cdn_down_until = self.clock() + DOWN_FOR_S
            return None
        return body if len(body) <= MAX_IMAGE_BYTES and body.startswith(PNG) else None

    # ---------------------------------------------------------------- disco
    def _read_cache(self, name):
        f = self.cache_dir / name
        try:
            body = f.read_bytes()
        except OSError:
            return None
        if not (body.startswith(PNG) or (name.endswith(".jpg") and body.startswith(JPG))):  # arquivo estragado: descarta e busca de novo
            self._drop(f)
            return None
        try:
            os.utime(f)               # usada agora: não é a primeira a sair quando passar do limite
        except OSError:
            pass
        return body

    @staticmethod
    def _drop(f):
        try:
            f.unlink()
        except OSError:
            pass

    def _store(self, name, body):
        with self.lock:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = self.cache_dir / f".{name}.tmp"
                tmp.write_bytes(body)
                os.replace(tmp, self.cache_dir / name)
                self.prune()
            except OSError:
                pass  # sem disco: a imagem ainda é servida desta vez

    def prune(self):
        """Passou do limite: apaga as mais antigas (pela última vez usada) até caber."""
        files = []
        try:
            for f in self.cache_dir.iterdir():
                if _NAME.fullmatch(f.name):
                    st = f.stat()
                    files.append((st.st_mtime, st.st_size, f))
        except OSError:
            return
        total = sum(size for _, size, _ in files)
        for _, size, f in sorted(files, key=lambda x: x[0]):
            if total <= self.cache_max:
                break
            self._drop(f)
            total -= size

    # ---------------------------------------------------------------- ponte
    def _base(self):
        url = str(self._bridge_url() or "")
        if not url.startswith(("http://127.0.0.1:", "http://localhost:")) or "/companion/v1/" not in url:
            return None                  # só a ponte local; nunca um endereço da rede
        return url.split("/companion/v1/", 1)[0] + "/companion/v1/img/"

    def _fetch(self, remote):
        """Corpo PNG da ponte, ou a resposta de erro (tupla)."""
        base = self._base()
        if base is None or self.clock() < self.down_until:
            return _reply(502, b"ponte indisponivel\n")
        try:
            with self.opener(urllib.request.Request(base + remote, headers={"Accept": "image/png"}), timeout=TIMEOUT_S) as resp:
                ctype = resp.headers.get_content_type()
                body = resp.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:            # ainda não extraída (ou id desconhecido): a ponte pode ter depois
                return _reply(404, b"imagem indisponivel\n")
            return _reply(502, b"ponte indisponivel\n")
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, TimeoutError):
            self.down_until = self.clock() + DOWN_FOR_S
            return _reply(502, b"ponte indisponivel\n")
        if ctype != "image/png" or len(body) > MAX_IMAGE_BYTES or not body.startswith(PNG):
            return _reply(502, b"resposta invalida\n")
        return body
