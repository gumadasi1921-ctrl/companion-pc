"""Bateria de fones e mouses Bluetooth, lida do que o PRÓPRIO WINDOWS informa (nada é estimado, nenhuma marca é consultada).
Fonte: propriedade de bateria do dispositivo (DEVPKEY {104EA319-6EE2-4701-BD47-8DDBF425BBE5} pid 2, um byte 0..100) nos dispositivos
Bluetooth presentes (BTHENUM = clássico/HFP, BTHLE e BTHLEDEVICE = BLE com Battery Service), via CfgMgr32 + ctypes (sem biblioteca nova).
Sem a propriedade = o aparelho NÃO aparece. Mouse com receptor USB (Logitech, Razer, genérico) normalmente não expõe nada: fica fora.
O celular recebe só {id opaco, nome que o Windows dá, pct}; nunca endereço Bluetooth nem caminho. O nome nunca vai ao log."""
import hashlib
import re
import sys
import uuid

LOW_PCT = 15       # abaixo disso = aviso
REARM_PCT = 20     # só avisa de novo depois de passar daqui (ou do aparelho sumir)
MAX_ITEMS = 8

KEY_BATTERY = ("{104EA319-6EE2-4701-BD47-8DDBF425BBE5}", 2)
KEY_CONNECTED = ("{83DA6326-97A6-4088-9453-A1923F573B29}", 15)    # DEVPKEY_Device_IsConnected
KEY_NAME = ("{A45C254E-DF1C-4EFD-8020-67D146A850E0}", 14)         # DEVPKEY_Device_FriendlyName
KEY_DESC = ("{A45C254E-DF1C-4EFD-8020-67D146A850E0}", 2)          # DEVPKEY_Device_DeviceDesc
ENUMERATORS = ("BTHENUM", "BTHLE", "BTHLEDEVICE")
_ADDR = re.compile(r"DEV_([0-9A-Fa-f]{12})")


def valid_pct(v):
    """Só inteiro 0..100 (bool não vale). Qualquer outra coisa é descartada, nunca arredondada nem adivinhada."""
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100 else None


def addr_of(instance_id):
    m = _ADDR.search(instance_id or "")
    return m.group(1).upper() if m else None


def opaque_id(key):
    return hashlib.sha256(("companion-battery|" + str(key)).encode()).hexdigest()[:12]


def clean_name(name):
    n = " ".join(str(name or "").split())[:60]
    return n or "Dispositivo Bluetooth"


# ---- leitura no Windows (CfgMgr32) --------------------------------------------------------------------------------------
def read_windows():
    """[{key, name, pct}] dos dispositivos Bluetooth presentes que informam bateria. Falha = []."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    class PKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.ULONG)]

    cm = ctypes.WinDLL("cfgmgr32")

    def pkey(spec):
        g = GUID.from_buffer_copy(uuid.UUID(spec[0]).bytes_le)
        return PKEY(g, spec[1])

    def ids(enum):
        n = wintypes.ULONG(0)
        flags = 0x1 | 0x100    # por enumerador + só presentes
        if cm.CM_Get_Device_ID_List_SizeW(ctypes.byref(n), enum, flags) != 0 or n.value < 2:
            return []
        buf = ctypes.create_unicode_buffer(n.value)
        if cm.CM_Get_Device_ID_ListW(enum, buf, n.value, flags) != 0:
            return []
        return [s for s in buf[:].split("\0") if s]

    def prop(devinst, spec):
        k, typ, size = pkey(spec), wintypes.ULONG(0), wintypes.ULONG(0)
        cm.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(k), ctypes.byref(typ), None, ctypes.byref(size), 0)
        if not 0 < size.value <= 512:
            return None
        buf = (ctypes.c_ubyte * size.value)()
        if cm.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(k), ctypes.byref(typ), buf, ctypes.byref(size), 0) != 0:
            return None
        raw = bytes(buf)[:size.value]
        if typ.value == 0x12:       # DEVPROP_TYPE_STRING
            return raw.decode("utf-16-le", "ignore").rstrip("\0")
        if typ.value == 0x11:       # BOOLEAN (VARIANT_BOOL / byte)
            return any(raw)
        return raw[0] if raw else None   # BYTE (bateria)

    names, hits = {}, []
    for enum in ENUMERATORS:
        for iid in ids(enum):
            dn = wintypes.DWORD(0)
            if cm.CM_Locate_DevNodeW(ctypes.byref(dn), iid, 0) != 0:
                continue
            addr = addr_of(iid)
            nm = prop(dn.value, KEY_NAME) or prop(dn.value, KEY_DESC)
            if addr and nm and "\\" in iid and iid.split("\\")[1].upper().startswith("DEV_"):
                names.setdefault(addr, nm)          # nó "raiz" do aparelho: é o nome que o Windows mostra
            pct = prop(dn.value, KEY_BATTERY)
            if pct is None:
                continue
            if prop(dn.value, KEY_CONNECTED) is False:
                continue
            hits.append((addr or iid, nm, pct))
    out, seen = [], set()
    for key, nm, pct in hits:
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "name": names.get(key) or nm, "pct": pct})
    return out


class Battery:
    """Estado das baterias + regra do aviso. read_fn() -> [{key, name, pct}] (injetável: o teste usa uma leitura falsa)."""

    def __init__(self, read_fn=read_windows):
        self.read_fn = read_fn
        self.items = []
        self.polled = False
        self.low = set()      # ids já avisados e ainda abaixo de REARM_PCT

    def poll(self):
        """Lê o Windows. Devolve (mudou, avisos[{id,name,pct}]). Erro de leitura = mantém o que havia e não inventa."""
        try:
            raw = self.read_fn() or []
        except Exception:  # noqa: BLE001
            return False, []
        items = []
        for r in raw:
            pct = valid_pct(r.get("pct")) if isinstance(r, dict) else None
            if pct is None:
                continue
            items.append({"id": opaque_id(r.get("key")), "name": clean_name(r.get("name")), "pct": pct})
        items = sorted(items, key=lambda i: (i["pct"], i["name"]))[:MAX_ITEMS]
        alerts = []
        here = {i["id"]: i for i in items}
        self.low &= set(here)                                   # aparelho que sumiu: rearma
        for i in items:
            if i["pct"] < LOW_PCT and i["id"] not in self.low:
                self.low.add(i["id"])
                alerts.append(dict(i))
            elif i["pct"] >= REARM_PCT:
                self.low.discard(i["id"])
        changed = (items != self.items) or not self.polled
        self.items, self.polled = items, True
        return changed, alerts

    def state(self):
        return {"items": [dict(i) for i in self.items], "low_pct": LOW_PCT}


def message(a):
    """(título, corpo) do aviso. O corpo leva o nome que o Windows dá ao aparelho e a porcentagem."""
    return "Bateria baixa", f"{a['name']}: {a['pct']}%"
