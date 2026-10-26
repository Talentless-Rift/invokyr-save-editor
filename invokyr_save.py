"""
invokyr_save.py - read/write Invokyr (.sav) save files.

Format (verified on save v15):
    <file>.sav = ZIP (deflate, no encryption)
        meta        MessagePack map  - save-slot header (round, rank, playtime, score, steam ids)
        save        MessagePack map  - current run state
        endless     MessagePack map  - endless-mode run state
        collection  MessagePack map  - persistent meta progression

No checksum, no encryption, no signature.

Usage:
    python invokyr_save.py dump  <sav> [-o dump.json]
    python invokyr_save.py check <sav>                 # byte-exact roundtrip test
    python invokyr_save.py set   <sav> -o <out.sav> path=value [path=value ...]
    python invokyr_save.py apply <sav> <patch.json> -o <out.sav>

    <sav> may be the name of a zip entry (meta/save/endless/collection) prefixed to the
    path, or omitted when the field exists in only one entry.

Path syntax: map.key / list[3] / nested lists work, e.g.
    bank.currentTotalScore=999999
    gameRoomTools.itemStringIds=["dice_heal","dice_heal"]
    playerInventories[2].itemBatteryLevels[0]=-1

Values: parsed as JSON when possible, else as a string. Type width of the
original field is preserved so the game's deserializer still accepts it.
"""
import json
import os
import re
import struct
import sys
import zipfile

# ---------------------------------------------------------------- msgpack read

class Node:
    __slots__ = ("kind", "value", "code")

    def __init__(self, kind, value=None, code=None):
        self.kind = kind      # 'map' | 'arr' | 'str' | 'bin' | 'int' | 'float' | 'bool' | 'nil'
        self.value = value    # map: list[(knode, vnode)] ; arr: list[node] ; scalar: python value
        self.code = code      # original format byte for scalars


class Reader:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def _u(self, n):
        v = int.from_bytes(self.d[self.i:self.i + n], "big")
        self.i += n
        return v

    def _raw(self, n):
        v = self.d[self.i:self.i + n]
        self.i += n
        return v

    def read(self):
        code = self.d[self.i]
        self.i += 1
        if code <= 0x7F:
            return Node("int", code, FIXPOS)
        if code >= 0xE0:
            return Node("int", code - 0x100, FIXNEG)
        if 0x80 <= code <= 0x8F:
            return Node("map", [(self.read(), self.read()) for _ in range(code & 0x0F)], code)
        if 0x90 <= code <= 0x9F:
            return Node("arr", [self.read() for _ in range(code & 0x0F)], code)
        if 0xA0 <= code <= 0xBF:
            return Node("str", self._raw(code & 0x1F).decode("utf-8"), code)
        if code == 0xC0:
            return Node("nil", None, code)
        if code == 0xC2:
            return Node("bool", False, code)
        if code == 0xC3:
            return Node("bool", True, code)
        if code == 0xC4:
            return Node("bin", self._raw(self._u(1)), code)
        if code == 0xC5:
            return Node("bin", self._raw(self._u(2)), code)
        if code == 0xC6:
            return Node("bin", self._raw(self._u(4)), code)
        if code == 0xCA:
            return Node("float", struct.unpack(">f", self._raw(4))[0], code)
        if code == 0xCB:
            return Node("float", struct.unpack(">d", self._raw(8))[0], code)
        if code in (0xCC, 0xCD, 0xCE, 0xCF):
            return Node("int", self._u({0xCC: 1, 0xCD: 2, 0xCE: 4, 0xCF: 8}[code]), code)
        if code in (0xD0, 0xD1, 0xD2, 0xD3):
            n = {0xD0: 1, 0xD1: 2, 0xD2: 4, 0xD3: 8}[code]
            return Node("int", int.from_bytes(self._raw(n), "big", signed=True), code)
        if code == 0xD9:
            return Node("str", self._raw(self._u(1)).decode("utf-8"), code)
        if code == 0xDA:
            return Node("str", self._raw(self._u(2)).decode("utf-8"), code)
        if code == 0xDB:
            return Node("str", self._raw(self._u(4)).decode("utf-8"), code)
        if code == 0xDC:
            return Node("arr", [self.read() for _ in range(self._u(2))], code)
        if code == 0xDD:
            return Node("arr", [self.read() for _ in range(self._u(4))], code)
        if code == 0xDE:
            return Node("map", [(self.read(), self.read()) for _ in range(self._u(2))], code)
        if code == 0xDF:
            return Node("map", [(self.read(), self.read()) for _ in range(self._u(4))], code)
        raise ValueError(f"unknown msgpack byte 0x{code:02x} at offset {self.i - 1}")


# --------------------------------------------------------------- msgpack write

# family preserved from the original byte, width widened only if the value needs it.
# fixint / negative-fixint are pseudo-codes: the byte is the value itself.
FIXPOS = -1
FIXNEG = -2
INT_FAMILY = {0xCC: ("u", 1), 0xCD: ("u", 2),
              0xCE: ("u", 4), 0xCF: ("u", 8), 0xD0: ("s", 1), 0xD1: ("s", 2),
              0xD2: ("s", 4), 0xD3: ("s", 8)}
INT_CODE = {("u", 1): 0xCC, ("u", 2): 0xCD, ("u", 4): 0xCE, ("u", 8): 0xCF,
            ("s", 1): 0xD0, ("s", 2): 0xD1, ("s", 4): 0xD2, ("s", 8): 0xD3}
INT_RANGE = {1: (-128, 127), 2: (-32768, 32767), 4: (-2147483648, 2147483647),
             8: (-9223372036854775808, 9223372036854775807)}
UINT_MAX = {1: 255, 2: 65535, 4: 4294967295, 8: 18446744073709551615}


def enc_int(out, v, orig):
    if 0 <= v <= 0x7F and orig in (None, FIXPOS):
        out.append(v)
        return
    if -32 <= v <= -1 and orig in (None, FIXNEG):
        out.append(0x100 + v)
        return
    if orig in (None, FIXPOS, FIXNEG):
        # widen out of fixint: unsigned for non-negatives (ulong fields), signed otherwise
        fam, width = ("u" if v >= 0 else "s"), 1
    else:
        fam, width = INT_FAMILY[orig]
    if v < 0:
        fam = "s"
    while True:
        if fam == "u":
            if 0 <= v <= UINT_MAX[width]:
                break
        else:
            lo, hi = INT_RANGE[width]
            if lo <= v <= hi:
                break
        width *= 2
        if width > 8:
            raise ValueError(f"integer {v} does not fit msgpack")
    if fam == "u" and width == 8 and v > INT_RANGE[8][1]:
        out.append(0xCF)
        out += v.to_bytes(8, "big")
        return
    out.append(INT_CODE[(fam, width)])
    out += v.to_bytes(width, "big", signed=(fam == "s"))


def enc_str(out, s):
    b = s.encode("utf-8")
    n = len(b)
    if n <= 31:
        out.append(0xA0 | n)
    elif n <= 0xFF:
        out += bytes([0xD9, n])
    elif n <= 0xFFFF:
        out.append(0xDA)
        out += struct.pack(">H", n)
    else:
        out.append(0xDB)
        out += struct.pack(">I", n)
    out += b


def encode(node, out):
    k = node.kind
    if k == "nil":
        out.append(0xC0)
    elif k == "bool":
        out.append(0xC3 if node.value else 0xC2)
    elif k == "int":
        enc_int(out, node.value, node.code)
    elif k == "float":
        if node.code == 0xCA:
            out.append(0xCA)
            out += struct.pack(">f", node.value)
        else:
            out.append(0xCB)
            out += struct.pack(">d", node.value)
    elif k == "str":
        enc_str(out, node.value)
    elif k == "bin":
        b = node.value
        if len(b) <= 0xFF:
            out += bytes([0xC4, len(b)])
        elif len(b) <= 0xFFFF:
            out.append(0xC5)
            out += struct.pack(">H", len(b))
        else:
            out.append(0xC6)
            out += struct.pack(">I", len(b))
        out += b
    elif k == "arr":
        n = len(node.value)
        if n <= 15:
            out.append(0x90 | n)
        elif n <= 0xFFFF:
            out.append(0xDC)
            out += struct.pack(">H", n)
        else:
            out.append(0xDD)
            out += struct.pack(">I", n)
        for v in node.value:
            encode(v, out)
    elif k == "map":
        n = len(node.value)
        if n <= 15:
            out.append(0x80 | n)
        elif n <= 0xFFFF:
            out.append(0xDE)
            out += struct.pack(">H", n)
        else:
            out.append(0xDF)
            out += struct.pack(">I", n)
        for kk, vv in node.value:
            encode(kk, out)
            encode(vv, out)
    else:
        raise ValueError(k)


def pack(node):
    out = bytearray()
    encode(node, out)
    return bytes(out)


# -------------------------------------------------------------------- helpers

def plain(node):
    """Node -> plain python (keys as python objects)."""
    if node.kind == "map":
        d = {}
        for kk, vv in node.value:
            k = plain(kk)
            d[k if isinstance(k, (str, int, float, bool)) else str(k)] = plain(vv)
        return d
    if node.kind == "arr":
        return [plain(v) for v in node.value]
    if node.kind == "bin":
        return {"$bin": node.value.hex()}
    return node.value


def coerce(value, old):
    """Turn a parsed literal into a Node value matching the target field kind."""
    if old is not None:
        if old.kind in ("int", "float") and isinstance(value, bool):
            value = int(value)
        if old.kind == "int" and isinstance(value, float):
            value = int(value)
        if old.kind == "float" and isinstance(value, int):
            value = float(value)
        if old.kind == "arr" and not isinstance(value, list):
            raise ValueError(f"field is a list, got {type(value).__name__}")
        if old.kind == "map" and not isinstance(value, dict):
            raise ValueError(f"field is a map, got {type(value).__name__}")
    return value


def node_from(value, old=None):
    """Build a fresh Node for a python value, reusing old type widths where possible."""
    if value is None:
        return Node("nil")
    if isinstance(value, bool):
        return Node("bool", value, old.code if old and old.kind == "bool" else None)
    if isinstance(value, int):
        return Node("int", value, old.code if old and old.kind == "int" else 0xD3)
    if isinstance(value, float):
        code = old.code if old and old.kind == "float" else 0xCA
        return Node("float", value, code)
    if isinstance(value, str):
        return Node("str", value)
    if isinstance(value, bytes):
        return Node("bin", value)
    if isinstance(value, list):
        if old is not None and old.kind == "arr":
            return Node("arr", [node_from(v, old.value[i] if i < len(old.value) else None)
                                for i, v in enumerate(value)], old.code)
        return Node("arr", [node_from(v) for v in value])
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            sub = None
            if old is not None and old.kind == "map":
                for ok, ov in old.value:
                    if ok.value == k:
                        sub = ov
                        break
            items.append((Node("str", k), node_from(v, sub)))
        return Node("map", items, old.code if old and old.kind == "map" else None)
    raise ValueError(f"unsupported type {type(value).__name__}")


PATH_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def walk(root, path):
    """Return (parent, key, node) for a dotted/bracketed path."""
    toks = []
    for m in PATH_RE.finditer(path):
        toks.append(int(m.group(2)) if m.group(2) is not None else m.group(1))
    if not toks:
        raise ValueError("empty path")
    cur = root
    for t in toks[:-1]:
        cur = child(cur, t)
    return cur, toks[-1]


def child(node, token):
    if isinstance(token, int):
        if node.kind != "arr":
            raise KeyError(f"[{token}] used on non-list")
        return node.value[token]
    if node.kind != "map":
        raise KeyError(f".{token} used on non-map")
    for k, v in node.value:
        if k.kind == "str" and k.value == token:
            return v
    raise KeyError(f"no key {token!r}")


def set_path(root, path, value):
    parent, key = walk(root, path)
    if isinstance(key, int):
        if parent.kind != "arr":
            raise KeyError(f"[{key}] used on non-list")
        old = parent.value[key]
        parent.value[key] = node_from(coerce(value, old), old)
    else:
        if parent.kind != "map":
            raise KeyError(f".{key} used on non-map")
        old = None
        for k, v in parent.value:
            if k.kind == "str" and k.value == key:
                old = v
                break
        new = node_from(coerce(value, old), old)
        if old is None:
            parent.value.append((Node("str", key), new))
        else:
            for i, (k, v) in enumerate(parent.value):
                if k.kind == "str" and k.value == key:
                    parent.value[i] = (k, new)
                    break
    return root


# ----------------------------------------------------------------------- zip io

def read_sav(path):
    with zipfile.ZipFile(path) as z:
        return [(i.filename, z.read(i.filename)) for i in z.infolist()]


def write_sav(path, entries):
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            z.writestr(info, data)
    if os.path.exists(path):
        os.remove(path)
    os.replace(tmp, path)


# ------------------------------------------------------------------------ main

USAGE = __doc__


def cmd_dump(args):
    src = args[0]
    out = None
    if "-o" in args:
        out = args[args.index("-o") + 1]
    doc = {}
    for name, data in read_sav(src):
        r = Reader(data)
        node = r.read()
        if r.i != len(data):
            print(f"!! {name}: {len(data) - r.i} trailing bytes", file=sys.stderr)
        doc[name] = plain(node)
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {out}")
    else:
        print(text)


def cmd_check(args):
    src = args[0]
    ok = True
    for name, data in read_sav(src):
        node = Reader(data).read()
        again = pack(node)
        same = again == data
        ok &= same
        print(f"{name:12s} {len(data):5d} bytes  roundtrip {'IDENTICAL' if same else 'MISMATCH'}")
        if not same:
            for i in range(min(len(data), len(again))):
                if data[i] != again[i]:
                    print(f"  first diff at {i}: {data[i]:02x} != {again[i]:02x}")
                    print(f"  orig: {data[max(0,i-16):i+16].hex(' ')}")
                    print(f"  mine: {again[max(0,i-16):i+16].hex(' ')}")
                    break
    sys.exit(0 if ok else 1)


def parse_value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def exists(node, path):
    try:
        walk(node, path)
        return True
    except (KeyError, IndexError):
        return False


def read_patch(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        doc = json.load(f)
    if isinstance(doc, dict) and "patch" in doc and isinstance(doc["patch"], dict):
        doc = doc["patch"]
    if not isinstance(doc, dict):
        raise SystemExit("patch file must be a JSON object of {\"entry.path\": value}")
    return list(doc.items())


def cmd_apply(args):
    """apply <sav> <patch.json> -o <out.sav>"""
    if len(args) < 2:
        print("usage: apply <sav> <patch.json> -o <out.sav>", file=sys.stderr)
        return 2
    src, patch = args[0], args[1]
    out = args[args.index("-o") + 1] if "-o" in args else None
    return apply_pairs(src, read_patch(patch), out)


def cmd_set(args):
    src = args[0]
    rest = args[1:]
    out = None
    if "-o" in rest:
        i = rest.index("-o")
        out = rest[i + 1]
        del rest[i:i + 2]
    pairs = []
    for a in rest:
        if "=" not in a:
            print(f"bad assignment {a!r}", file=sys.stderr)
            sys.exit(2)
        path, _, raw = a.partition("=")
        pairs.append((path.strip(), parse_value(raw)))
    return apply_pairs(src, pairs, out)


def apply_pairs(src, pairs, out):
    if not pairs:
        print("nothing to set", file=sys.stderr)
        return 2

    entries = read_sav(src)
    nodes = {n: Reader(d).read() for n, d in entries}
    order = [n for n, _ in entries]

    for path, value in pairs:
        head = path.split(".", 1)[0].split("[", 1)[0]
        if head in nodes and path != head:
            target, sub = head, path[len(head) + 1:]
        else:
            hits = [n for n in order if exists(nodes[n], path)]
            if len(hits) == 1:
                target, sub = hits[0], path
                print(f"  [auto] {path} lives in entry {target!r}")
            elif not hits:
                print(f"no entry has field {path!r} "
                      f"(entries: {', '.join(order)})", file=sys.stderr)
                return 2
            else:
                print(f"field {path!r} exists in {hits}; prefix it with the entry name, "
                      f"e.g. {hits[0]}.{path}", file=sys.stderr)
                return 2
        set_path(nodes[target], sub, value)
        new = pack(nodes[target])
        Reader(new).read()  # re-parse to prove it is valid msgpack
        nodes[target] = Reader(new).read()
        print(f"{target}.{sub} := {value!r}  ({len(new)} bytes)")

    data = {n: pack(nodes[n]) for n in order}
    if not out:
        print("refusing to overwrite: pass -o <new.sav>", file=sys.stderr)
        return 2
    write_sav(out, [(n, data[n]) for n in order])
    print(f"wrote {out}")
    for n in order:
        print(f"  {n}: {len(data[n])} bytes")
    return 0


def main(argv):
    if len(argv) < 2 or argv[1] not in ("dump", "check", "set", "apply"):
        print(USAGE)
        return 2
    cmd = {"dump": cmd_dump, "check": cmd_check, "set": cmd_set, "apply": cmd_apply}[argv[1]]
    return cmd(argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv) or 0)
