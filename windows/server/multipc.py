"""Vários PCs, um perfil: peças do servidor que não dependem do resto do Companion (testáveis sozinhas).
- pc_id: id aleatório e estável deste PC (config.json), nada pessoal; o app confere a cada conexão para nunca misturar PCs.
- pc_name: nome que o dono dá ao PC (ex.: "Casa"); vazio = o app mostra o nome da máquina.
- Origin: o app (casca) pode estar em outro endereço que o PC; só origens da lista `allowed_origins` (ou a própria) conectam.
- CORS: só para essas origens, nunca "*".
- X-Forwarded-For: atrás do `tailscale serve` todo acesso chega do loopback; o IP real vem do cabeçalho, mas só quando o par é o loopback.
Nada daqui registra token, caminho ou nome de arquivo."""
import ipaddress
import re
import secrets
from urllib.parse import quote

PROTO = 1              # versão do protocolo app <-> servidor; o app recusa PC com proto menor que o que ele exige
LOOPBACK = ("127.0.0.1", "::1")
MAX_ORIGINS = 5
_ORIGIN = re.compile(r"^https?://[a-z0-9]([a-z0-9.-]{0,98}[a-z0-9])?(:\d{1,5})?$")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def new_pc_id():
    return secrets.token_hex(8)


def valid_pc_id(v):
    return isinstance(v, str) and re.fullmatch(r"[0-9a-f]{16}", v) is not None


def clean_pc_name(v):
    if not isinstance(v, str) or _CTRL.search(v) or len(v.strip()) > 30:
        raise ValueError("nome do PC: até 30 caracteres")
    return v.strip()


def clean_origins(v):
    """Lista de origens (esquema + host [+ porta], sem caminho). Barra final é aceita e removida."""
    if not isinstance(v, list) or len(v) > MAX_ORIGINS:
        raise ValueError(f"até {MAX_ORIGINS} endereços permitidos")
    out = []
    for o in v:
        if not isinstance(o, str):
            raise ValueError("endereço inválido")
        o = o.strip().lower().rstrip("/")
        if not _ORIGIN.match(o):
            raise ValueError("endereço permitido: https://nome.exemplo (sem caminho)")
        if o not in out:
            out.append(o)
    return out


def _host_of(origin):
    return origin.split("://", 1)[-1].lower()


def origin_ok(origin, host_header, allowed):
    """Sem Origin (programas, testes) passa; mesma origem passa; senão só a lista."""
    if not origin:
        return True
    if origin.lower() == "null":
        return False
    if host_header and _host_of(origin) == host_header.strip().lower():
        return True
    return origin.strip().lower().rstrip("/") in (allowed or [])


def cors_headers(origin, host_header, allowed):
    """Cabeçalhos CORS só para origem de OUTRO endereço que esteja na lista (nunca '*')."""
    if not origin:
        return {}
    o = origin.strip().lower().rstrip("/")
    if host_header and _host_of(o) == host_header.strip().lower():
        return {}
    if o in (allowed or []):
        return {"Access-Control-Allow-Origin": o, "Vary": "Origin"}
    return {}


def client_ip(peer, xff):
    """IP do aparelho. O X-Forwarded-For só vale quando o par é o loopback (o proxy do Tailscale roda no próprio PC);
    vale o ÚLTIMO item, o que o proxy mais próximo acrescentou. Cabeçalho inválido = ignora."""
    if peer not in LOOPBACK or not xff:
        return peer
    last = xff.split(",")[-1].strip()
    try:
        return str(ipaddress.ip_address(last))
    except ValueError:
        return peer


def is_proxied(peer, xff):
    """Conexão que veio por proxy no próprio PC (loopback COM X-Forwarded-For): não é cabo USB nem o painel."""
    return peer in LOOPBACK and bool(xff)


def clean_remote_base(v):
    """Endereço deste PC pelo Tailscale: uma origem https (ou vazio = nenhum)."""
    if v == "":
        return ""
    o = clean_origins([v])[0] if isinstance(v, str) else None
    if not o or not o.startswith("https://"):
        raise ValueError("endereço do PC pelo Tailscale: https://nome.exemplo")
    return o


def remote_link(app_origin, remote_base, code):
    """Link de pareamento remoto: <app>/#add=<endereço do PC>~<código>. Só leva o código de 6 dígitos (120 s, uso único): nunca o token."""
    if not (remote_base and isinstance(code, str) and re.fullmatch(r"\d{6}", code)):
        return None
    return f"{app_origin or remote_base}/#add={quote(remote_base, safe='')}~{code}"


def with_build_meta(body: bytes, build: int) -> bytes:
    """Põe <meta name="build" content="N"> logo depois do <head> do HTML servido (só em memória, uma vez).
    N é só o número da versão da página (web_build), nunca token nem caminho."""
    if b'<meta name="build" ' in body:
        return body
    i = body.find(b"<head>")
    if i < 0:
        return body
    i += len(b"<head>")
    return body[:i] + b'\n<meta name="build" content="%d">' % int(build) + body[i:]
