#!/usr/bin/env python3
"""iblis-check: the Project Iblis checker. Everything happens on this machine, against YOUR copy of Game.exe.

  python iblis_check.py setup --game PATH --fetch               verify the game, download and verify the pinned compiler
  python iblis_check.py setup --game PATH --cl PATH             or use a Visual C++ 2005 SP1 cl.exe you already have
  python iblis_check.py selftest                                compile one verified function; must print MATCH
  python iblis_check.py brief VA                                work/<VA>/BRIEF.md and starter.cpp from packet.json
  python iblis_check.py check VA                                compile work/<VA>/candidate.cpp and compare
  python iblis_check.py sxs VA [--all]                          aligned side-by-side of the last check (terminal only)
  python iblis_check.py best VA [--restore]                     your closest checks so far; --restore puts the best back
  python iblis_check.py sweep VA                                check every work/<VA>/variants/*.cpp, ranked by closeness
  python iblis_check.py decl VA [VA ...]                        the declaration verified sources use for an address
  python iblis_check.py fields STRUCT [OFFSET]                  members verified sources place at each offset
  python iblis_check.py layout STRUCT OFFSET [OFFSET ...]       a padded struct with just those members, ready to paste
  python iblis_check.py callers VA                              who calls VA and how they set up its arguments (terminal only)

Nothing this tool reads from the game is ever written anywhere but work/, and work/ is never submitted. Submit only
candidate.cpp and your notes, through the service.
"""
import difflib, glob, hashlib, json, os, re, shutil, signal, struct, subprocess, sys, threading, time

KIT = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(KIT, "kit.json"))) if os.path.exists(os.path.join(KIT, "kit.json")) else {}
HOME = os.path.join(os.path.expanduser("~"), ".iblis")
SETTINGS = os.path.join(HOME, "settings.json")
WORK = os.path.join(os.getcwd(), "work")


def die(msg, code=2):
    print("error:", msg, file=sys.stderr)
    sys.exit(code)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def norm_va(v):
    m = re.match(r"^(?:0[xX])?([0-9A-Fa-f]{1,8})$", str(v).strip())
    if not m:
        die("an address looks like 0x00401220")
    return "0x%08X" % int(m.group(1), 16)


def settings():
    if not os.path.exists(SETTINGS):
        die("run setup first: python iblis_check.py setup --game PATH --fetch")
    s = json.load(open(SETTINGS))
    if sha256_file(s["image"]) != CFG["image_sha256"]:
        die("Game.exe no longer hashes to the 1.14d value; run setup again")
    return s


class PE:
    """Minimal PE32 reader: VA -> bytes, sections, import table."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = d = f.read()
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        opt = pe + 24
        self.base = struct.unpack_from("<I", d, opt + 28)[0]
        optsz = struct.unpack_from("<H", d, pe + 20)[0]
        self.opt = opt
        self.sections = []
        for i in range(struct.unpack_from("<H", d, pe + 6)[0]):
            o = opt + optsz + 40 * i
            vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append(dict(name=d[o:o + 8].rstrip(b"\0").decode("latin-1"), vsize=vsize, va=va, rawsize=rawsize, rawptr=rawptr))

    def sec(self, va):
        rva = va - self.base
        return next((s for s in self.sections if s["va"] <= rva < s["va"] + max(s["vsize"], s["rawsize"])), None)

    def read(self, va, n):
        s = self.sec(va)
        if not s or n < 0:
            return None
        relative = va - self.base - s["va"]
        if relative + n > s["rawsize"]:
            return None
        o = s["rawptr"] + relative
        return self.data[o:o + n]

    def in_text(self, va):
        s = self.sec(va)
        return bool(s and s["name"] == ".text")

    def iat(self):
        d = self.data
        rva = struct.unpack_from("<I", d, self.opt + 104)[0]
        out = {}
        if not rva:
            return out
        off = self.sec(self.base + rva)
        off = off["rawptr"] + rva - off["va"]
        def cstr(r):
            s = self.sec(self.base + r); o = s["rawptr"] + r - s["va"]
            return d[o:d.index(b"\0", o)].decode("latin-1")
        while True:
            oft, _, _, name_rva, ft = struct.unpack_from("<IIIII", d, off)
            if not ft:
                break
            dll = cstr(name_rva)
            t = self.sec(self.base + (oft or ft)); to = t["rawptr"] + (oft or ft) - t["va"]; k = 0
            while True:
                e = struct.unpack_from("<I", d, to + 4 * k)[0]
                if not e:
                    break
                out[self.base + ft + 4 * k] = "%s!%s" % (dll, "#%d" % (e & 0xFFFF) if e & 0x80000000 else cstr(e + 2))
                k += 1
            off += 20
        return out


# ---------------------------------------------------------------- setup

def cmd_setup(args):
    game = args[args.index("--game") + 1] if "--game" in args else die("--game PATH is required")
    if not os.path.isfile(game):
        die("no file at %s" % game)
    h = sha256_file(game)
    if h != CFG["image_sha256"]:
        die("this Game.exe hashes to %s; Project Iblis needs Diablo II 1.14d (%s...)" % (h[:16], CFG["image_sha256"][:16]))
    print("game: 1.14d verified")
    tc = CFG["toolchain"]
    if "--cl" in args:
        cl = os.path.abspath(args[args.index("--cl") + 1])
        root = os.path.dirname(os.path.dirname(cl))
    elif "--fetch" in args:
        root = os.path.join(KIT, "toolchain")
        if not os.path.exists(os.path.join(root, "bin", "cl.exe")):
            print("fetching the compiler from %s, a third-party mirror that Project Iblis does not run.\n"
                  "Visual C++ 2005 SP1 is Microsoft software; Project Iblis does not host it." % tc["repo"])
            if os.path.exists(root):
                shutil.rmtree(root)
            for step in (["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", tc["repo"], root],
                         ["git", "-C", root, "checkout", "--quiet", tc["commit"], "--", "bin"]):
                r = subprocess.run(step, capture_output=True, text=True)
                if r.returncode:
                    die("fetching the compiler failed:\n" + r.stderr[-800:])
        cl = os.path.join(root, "bin", "cl.exe")
    else:
        die("give --fetch (download the pinned compiler) or --cl PATH (a Visual C++ 2005 SP1 cl.exe you already have)")
    bad = [rel for rel, want in tc["files"].items() if not os.path.exists(os.path.join(root, *rel.split("/")))
           or sha256_file(os.path.join(root, *rel.split("/"))) != want]
    if bad:
        die("compiler files do not match the pinned hashes: %s" % ", ".join(bad))
    print("compiler: %d files verified (cl.exe %s)" % (len(tc["files"]), tc["cl_version"]))
    runner = "native" if os.name == "nt" else "wine"
    if runner == "wine" and not shutil.which("wine"):
        die("install Wine 9 first (the compiler is a Windows program), then run setup again")
    try:
        import capstone  # noqa: F401
    except ImportError:
        die("pip install capstone==5.0.7 (used for briefs and side-by-sides)")
    os.makedirs(HOME, exist_ok=True)
    json.dump(dict(image=os.path.abspath(game), cl=cl, runner=runner), open(SETTINGS, "w"), indent=1)
    print("settings written to", SETTINGS, "\nnext: python iblis_check.py selftest")


# ---------------------------------------------------------------- context (declarations and layouts from verified C++)

_CTX = None
POINTER = 4
TYPE_SIZES = {"char": 1, "signed char": 1, "unsigned char": 1, "bool": 1, "short": 2, "unsigned short": 2, "int": 4,
              "unsigned int": 4, "unsigned": 4, "long": 4, "unsigned long": 4, "float": 4, "double": 8, "__int64": 8,
              "unsigned __int64": 8, "wchar_t": 2, "BYTE": 1, "WORD": 2, "DWORD": 4, "BOOL": 4}
KEYWORDS = {"void", "char", "short", "int", "long", "float", "double", "bool", "signed", "unsigned", "const", "volatile",
            "struct", "class", "union", "enum", "__int8", "__int16", "__int32", "__int64", "wchar_t", "static", "extern",
            "inline", "__forceinline", "__declspec", "dllimport", "noreturn", "__cdecl", "__stdcall", "__fastcall",
            "__thiscall", "virtual", "operator", "C", "__w64", "__unaligned", "__restrict"}


def context(path=None):
    """kit/context.json: what verified sources declare at each address, and the struct members they use."""
    global _CTX
    if path:
        return json.load(open(path, encoding="utf-8"))
    if _CTX is None:
        p = os.path.join(KIT, "context.json")
        _CTX = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else dict(decls={}, fields={}, sizes={}, typedefs={}, enums={})
    return _CTX


def decl_of(ctx, va):
    return ctx["decls"].get(norm_va(va) if isinstance(va, str) else "0x%08X" % va)


def type_closure(ctx, texts):
    """Typedef/enum lines and forward declarations so the given declarations compile on their own."""
    seen, typedefs, enums, forward = set(), [], [], []
    todo = [w for t in texts for w in re.findall(r"\b[A-Za-z_]\w*\b", t)]
    while todo:
        w = todo.pop(0)
        if w in seen or w in KEYWORDS or w in TYPE_SIZES and w not in ctx["typedefs"]:
            continue
        seen.add(w)
        if w in ctx["typedefs"]:
            line = ctx["typedefs"][w]
            typedefs.append(line); todo += re.findall(r"\b[A-Za-z_]\w*\b", line)
        elif w in ctx["enums"]:
            enums.append(ctx["enums"][w])
        elif w in ctx["fields"] or w in ctx["sizes"] or re.match(r"^D2\w+(Strc|Txt|Tbl|Rec)$", w):
            forward.append(w)
    for t in texts:
        # anything in a type position: `X*`, `X&`, `X name` inside a parameter list, `struct X`
        params = t[t.find("("):] if "(" in t else ""
        cands = re.findall(r"\b([A-Za-z_]\w*)\s*[*&]", t)
        cands += re.findall(r"[(,]\s*(?:const\s+)?([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*(?:\[[^\]]*\])?\s*(?=[,)])", params)
        cands += re.findall(r"[(,]\s*(?:const\s+)?([A-Za-z_]\w*)\s*(?=[,)])", params)  # unnamed parameters
        cands += re.findall(r"\b(?:struct|class|union)\s+([A-Za-z_]\w*)", t)
        m = re.match(r"^\s*(?:extern\s+(?:\"C\"\s+)?)?(?:const\s+)?([A-Za-z_]\w*)\s+", t)
        if m:
            cands.append(m.group(1))
        for w in cands:
            if w not in KEYWORDS and w not in TYPE_SIZES and w not in ctx["typedefs"] and w not in ctx["enums"] and w not in forward:
                forward.append(w)
    return typedefs, enums, sorted(set(forward) - set(ctx["typedefs"]) - set(ctx["enums"]))


def by_value_unknowns(ctx, text, forward):
    """Forward-declared names a definition takes or returns by value: a struct forward declaration cannot serve there."""
    out = []
    for w in forward:
        if w in ctx["fields"] or w in ctx.get("sizes", {}):
            continue
        if re.search(r"\b%s\b(?!\s*[*&])" % re.escape(w), text) and not re.search(r"\b(?:struct|class|union)\s+%s\b" % re.escape(w), text):
            out.append(w)
    return out


def decl_name(text):
    """The declared name: the first identifier before '(' that is not a keyword, else the last identifier."""
    fn = [w for w in re.findall(r"([A-Za-z_~]\w*)\s*\(", text) if w not in KEYWORDS]
    if fn:
        return fn[0]
    ids = re.findall(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*;?\s*$", text)
    return ids[-1] if ids else None


def starter_source(va, pk, calls, dref, ctx, hints):
    """A first candidate: every callee and global declared from verified sources, the target's signature when a
    verified caller declares it, and TODO lines for the rest. It is a scaffold, not a match."""
    known, members, todo, used, taken = [], {}, [], [], {}
    tdecl = decl_of(ctx, va)
    if tdecl:
        if decl_name(tdecl["decl"]):
            taken[decl_name(tdecl["decl"])] = int(va, 16)
    for a in sorted(set(calls) | set(k for k, v in dref.items() if not str(v).startswith("import "))):
        if a == int(va, 16):
            continue
        d = decl_of(ctx, a)
        if not d:
            label = calls.get(a) or dref.get(a)
            todo.append("// TODO declare %s at 0x%08X (no verified declaration yet)" % (label, a)); continue
        text = re.sub(r"\s*//.*$", "", d["decl"])
        fname = decl_name(text)
        if fname and fname in taken:
            todo.append("// TODO 0x%08X is declared as %s, a name already used above for 0x%08X; rename one" % (a, fname, taken[fname]))
            continue
        if fname:
            taken[fname] = a
        if d.get("struct"):
            members.setdefault(d["struct"], []).append("    %s // 0x%08X" % (text, a))
        else:
            known.append("%s // 0x%08X" % (text, a))
        used.append(text)
    target = decl_of(ctx, va)
    if target and not target.get("struct"):
        head = target["decl"].rstrip().rstrip(";")
        head = re.sub(r"^\s*extern\s+(\"C\"\s+)?", "", head)
    else:
        conv = "__fastcall" if any("ECX/EDX" in h for h in hints) else "__cdecl"
        head = "int %s %s()" % (conv, pk.get("name") or "sub_%s_Todo" % va[2:])
    used.append(head)
    typedefs, enums, forward = type_closure(ctx, used + [s for s in members])
    out = ["// starter for %s, generated by `iblis_check.py brief`: declarations come from verified sources." % va,
           "// It compiles as a scaffold, not a match. Check each declaration against your brief before relying on it.", ""]
    opaque = by_value_unknowns(ctx, head, forward)
    out += ["struct %s;" % s for s in forward if s not in members and s not in opaque]
    out += ["typedef void* %s; // TODO: unknown type, used by value; replace with its real definition" % s for s in opaque]
    out += typedefs + enums
    for s, ms in sorted(members.items()):
        out += ["struct %s" % s, "{"] + ms + ["};"]
    out += [""] + known + todo + ["", "// %s" % va, head, "{",
            "    // TODO: the body. Start from previous.cpp when the packet has an earlier attempt."]
    if not re.match(r"^(?:static\s+)?void\b", head):
        out.append("    return 0;")
    out += ["}", ""]
    return "\n".join(out)


def field_rows(ctx, struct):
    f = ctx["fields"].get(struct)
    if f is None:
        close = difflib.get_close_matches(struct, list(ctx["fields"]), n=5)
        die("no verified layout for %s%s" % (struct, ("; did you mean " + ", ".join(close)) if close else ""))
    return sorted(((int(k[1:], 16), v) for k, v in f.items()), key=lambda x: x[0])


def size_of(ctx, ty):
    ty = ty.strip()
    if ty.endswith("*"):
        return POINTER
    base = re.sub(r"^const\s+", "", ty)
    if base in TYPE_SIZES:
        return TYPE_SIZES[base]
    if base in ctx["enums"]:
        return 4
    if base in ctx["typedefs"]:
        m = re.match(r"typedef\s+(.+?)\s+[A-Za-z_]\w*;$", ctx["typedefs"][base])
        return size_of(ctx, m.group(1)) if m else None
    if base in ctx.get("sizes", {}):
        return int(ctx["sizes"][base], 16)
    return None


def layout_source(ctx, struct, offsets):
    """A struct with the most-used member at each requested offset and char padding between them."""
    rows = dict(field_rows(ctx, struct))
    lines, at, need = ["struct %s" % struct, "{"], 0, set()
    for off in sorted(set(offsets)):
        if off < at:
            lines.append("    // +0x%X overlaps the member before it; pick one" % off); continue
        if off > at:
            lines.append("    char _pad_0x%X[0x%X];%s// +0x%X" % (at, off - at, " " * 8, at))
        if off not in rows:
            lines.append("    int _unknown_0x%X;%s// +0x%X  (no verified member here; check the size)" % (off, " " * 8, off))
            at = off + 4; continue
        ty, nm, n = rows[off][0]
        m = re.match(r"^([A-Za-z_]\w*)((?:\[[^\]]+\])*)$", nm)
        base, arr = (m.group(1), m.group(2)) if m else (nm, "")
        size = size_of(ctx, ty)
        count = 1
        for a in re.findall(r"\[([^\]]+)\]", arr):
            try:
                count *= int(a, 0)
            except ValueError:
                count = None
        lines.append("    %s %s%s;%s// +0x%X  (%d verified sources)" % (ty, base, arr, " " * 8, off, n))
        need.update(w for w in re.findall(r"\b[A-Za-z_]\w*\b", ty) if w not in KEYWORDS)
        if size is None or count is None:
            lines.append("    // size of %s unknown: the next padding assumes it ends at the next requested offset" % ty)
            nxt = [o for o in sorted(set(offsets)) if o > off]
            at = nxt[0] if nxt else off + 4
        else:
            at = off + size * count
    lines.append("};")
    fwd = ["struct %s;" % w for w in sorted(need) if w not in TYPE_SIZES and w not in ctx["typedefs"] and w not in ctx["enums"] and w != struct]
    return "\n".join(fwd + lines) + "\n"


def cmd_decl(vas):
    ctx = context()
    for v in vas:
        va = norm_va(v); d = decl_of(ctx, va)
        if not d:
            print("%s  no verified declaration (name: %s)" % (va, json.load(open(os.path.join(KIT, "names.json"))).get(va, "unknown")))
            continue
        print("%s  %s // %s\n    %s, %d source(s)%s" % (va, d["decl"], va, d["how"], d["sources"], (", member of " + d["struct"]) if d.get("struct") else ""))
        for alt in d.get("alternatives", []):
            print("    also seen: %s" % alt)


def cmd_fields(args):
    ctx = context(); struct = args[0]
    want = int(args[1], 16) if len(args) > 1 else None
    size = ctx.get("sizes", {}).get(struct)
    print("%s%s" % (struct, ("  (largest complete layout seen: %s B)" % size) if size else ""))
    for off, cands in field_rows(ctx, struct):
        if want is None or off == want:
            print("  +0x%-5X %s" % (off, "   |   ".join("%s %s (%d)" % (t, n, c) for t, n, c in cands)))


def cmd_layout(args):
    if len(args) < 2:
        die("usage: layout STRUCT OFFSET [OFFSET ...]   e.g. layout D2UnitStrc 0x0 0x14 0x60")
    print(layout_source(context(), args[0], [int(a, 16) for a in args[1:]]), end="")


# ---------------------------------------------------------------- brief

def load_packet(va):
    p = os.path.join(WORK, va, "packet.json")
    if not os.path.exists(p):
        die("save the packet from `next` as %s first" % p)
    pk = json.load(open(p))
    if norm_va(pk["va"]) != va:
        die("packet.json is for %s, not %s" % (pk["va"], va))
    return pk


def cmd_brief(va):
    import capstone
    va = norm_va(va); s = settings(); pk = load_packet(va)
    pe = PE(s["image"]); iat = pe.iat(); start = int(va, 16); n = pk["length"]
    names = json.load(open(os.path.join(KIT, "names.json")))
    name = lambda a, dflt: names.get("0x%08X" % a, dflt)
    body = pe.read(start, n)
    if pk.get("body_sha256") and hashlib.sha256(body).hexdigest() != pk["body_sha256"]:
        die("the bytes at %s in your Game.exe do not match the packet; is this the right file?" % va)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    lines, calls, dref, strings, floats, tables = [], {}, {}, {}, {}, []

    def cstr(a):
        b = pe.read(a, 128) or b""
        t = b[:b.find(b"\0")] if b"\0" in b else b
        if len(t) >= 2 and all(32 <= c < 127 for c in t):
            return t.decode()
        w = pe.read(a, 256) or b""
        if len(w) >= 16 and all(w[i + 1] == 0 and 32 <= w[i] < 127 for i in range(0, 16, 2)):
            return "L" + w.decode("utf-16le", "ignore").split("\0")[0]
        return None

    for ins in md.disasm(body, start):
        own = [t for t in tables if start <= t < start + n]
        if own and ins.address >= min(own):
            lines.append("%08X  ... %d B of switch data to the end of the function" % (min(own), start + n - min(own)))
            break
        note = ""
        for op in ins.operands:
            tgt = None
            if op.type == capstone.x86.X86_OP_IMM and ins.mnemonic.startswith(("call", "j")):
                if pe.in_text(op.imm) and not start <= op.imm < start + n:
                    calls[op.imm] = name(op.imm, "sub_%08X" % op.imm); note = "  ; " + calls[op.imm]
            elif op.type == capstone.x86.X86_OP_IMM and ins.mnemonic in ("push", "mov", "cmp", "lea") and pe.sec(op.imm):
                tgt = op.imm
            elif op.type == capstone.x86.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0 and pe.sec(op.mem.disp):
                tgt = op.mem.disp
            elif op.type == capstone.x86.X86_OP_MEM and op.mem.scale == 4 and op.mem.disp and pe.in_text(op.mem.disp):
                tables.append(op.mem.disp); note = "  ; jump table at 0x%08X" % op.mem.disp
            if tgt and not pe.in_text(tgt):
                sec = pe.sec(tgt)["name"]
                if tgt in iat:
                    dref[tgt] = "import " + iat[tgt]; note = "  ; " + dref[tgt]; continue
                st = cstr(tgt)
                if st:
                    strings[tgt] = st; note = "  ; %s %r" % (sec, st)
                elif sec == ".rdata" and op.type == capstone.x86.X86_OP_MEM and ins.mnemonic.startswith("f"):
                    raw = pe.read(tgt, 8); floats[tgt] = (struct.unpack("<f", raw[:4])[0], struct.unpack("<d", raw)[0])
                    note = "  ; float %r / double %r" % floats[tgt]
                else:
                    dref[tgt] = name(tgt, "g%s_%08X" % (sec.strip("."), tgt)); note = "  ; %s %s" % (sec, dref[tgt])
            elif tgt and pe.in_text(tgt) and ins.mnemonic in ("push", "mov"):
                calls[tgt] = name(tgt, "sub_%08X" % tgt); note = "  ; &%s (function pointer)" % calls[tgt]
        lines.append("%08X  %-28s %s %s%s" % (ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str, note))

    conv = []
    if any(re.search(r"\b(ecx|edx)\b", l.split("  ", 2)[-1]) for l in lines[:6]):
        conv.append("reads ECX/EDX early: __thiscall or __fastcall")
    if any(re.search(r" ret (0x)?\d", l) for l in lines):
        conv.append("ret n: the callee cleans the stack (__stdcall / __fastcall / __thiscall)")
    else:
        conv.append("plain ret: __cdecl, or no stack arguments")
    if re.search(r"\b(ebx|esi|edi)\b", " ".join(l.split("  ", 2)[-1] for l in lines[:3])) and not lines[0].split()[2].startswith("push"):
        conv.append("may read EBX/ESI/EDI at entry: write it as a file static with a small caller giving its real call site")
    out = os.path.join(WORK, va, "BRIEF.md")
    with open(out, "w") as f:
        f.write("# %s  (%d B, %s)\n\n" % (va, n, pk.get("module", "?")))
        if pk.get("name"):
            f.write("Name: %s\n\n" % pk["name"])
        f.write("Flags: `%s`\n\n" % pk.get("flags", ""))
        if pk.get("reference"):
            f.write("Reference: %s\n\n" % pk["reference"])
        if pk.get("previous_attempt"):
            f.write("## Previous attempt\n%s\n\nIts closest code is in packet.json (previous_attempt.closest_source).\n\n" % pk["previous_attempt"]["note"])
        f.write("## Convention hints\n" + "".join("- %s\n" % c for c in conv) + "\n")
        for title, d in (("Calls and function pointers", calls), ("Globals and imports", dref)):
            f.write("## %s\n" % title + "".join("- 0x%08X %s\n" % kv for kv in sorted(d.items())) + "\n")
        f.write("## Strings\n" + "".join("- 0x%08X %r\n" % kv for kv in sorted(strings.items())) + "\n")
        f.write("## Float constants\n" + "".join("- 0x%08X %r\n" % kv for kv in sorted(floats.items())) + "\n")
        f.write("## Disassembly (from your Game.exe; never paste it anywhere)\n```\n" + "\n".join(lines) + "\n```\n\n")
        ctx = context(); known = 0
        f.write("## Known declarations (what verified sources declare at these addresses)\n")
        for a in sorted(set(calls) | set(dref)):
            if str(dref.get(a, "")).startswith("import "):
                continue
            d = decl_of(ctx, a)
            known += bool(d)
            f.write("- 0x%08X %s\n" % (a, ("`%s` (%s, %d source(s))" % (d["decl"], d["how"], d["sources"])) if d else "no verified declaration yet"))
        tdecl = decl_of(ctx, va)
        f.write("\nThis function, as its verified callers declare it: %s\n\n" % (("`%s`" % tdecl["decl"]) if tdecl else "not declared by any verified source"))
        f.write("## Declarations\nDeclare every callee and global above with a trailing `// 0x...` marker, e.g.\n"
                "`int __fastcall SOME_Func(int a); // 0x0052E510` and `extern int gnGlobal_007A285C; // 0x007A285C`.\n"
                "Put `// %s` on its own line directly above your function's definition.\n"
                "`starter.cpp` has every known declaration filled in. Struct members: `python iblis_check.py fields STRUCT`\n"
                "and `python iblis_check.py layout STRUCT 0x14 0x60` (the offsets your disassembly uses).\n" % va)
    wd = os.path.join(WORK, va)
    with open(os.path.join(wd, "starter.cpp"), "w", encoding="latin-1", errors="replace") as f:
        f.write(starter_source(va, pk, calls, dref, ctx, conv))
    made = ["starter.cpp"]
    prev = (pk.get("previous_attempt") or {}).get("closest_source")
    if prev:
        with open(os.path.join(wd, "previous.cpp"), "w", encoding="latin-1", errors="replace") as f:
            f.write(prev)
        made.append("previous.cpp")
    cand = os.path.join(wd, "candidate.cpp")
    if not os.path.exists(cand):
        shutil.copyfile(os.path.join(wd, "previous.cpp" if prev else "starter.cpp"), cand)
        made.append("candidate.cpp (copied from %s)" % ("previous.cpp" if prev else "starter.cpp"))
    print("wrote %s: %d calls, %d globals, %d strings; %d of %d declarations known" % (
        out, len(calls), len(dref), len(strings), known, len([a for a in set(calls) | set(dref) if not str(dref.get(a, "")).startswith("import ")])))
    print("also wrote: " + ", ".join(made))


# ---------------------------------------------------------------- check

FORBIDDEN = [r"\b__asm\b", r"\basm\b", r"\b_emit\b", r"\bnaked\b", r"#pragma\s+(optimize|code_seg|check_stack|function)"]


def parse_markers(src):
    """Markers in the source: defs (the `// <VA>` line above a definition) and decls (a trailing `// 0x...` on a
    declaration). A declaration inside `struct S { ... };` is recorded as `S::name`, so a member function's scoped
    symbol resolves to its own marker and never borrows an unqualified one."""
    defs, decls, pending = {}, {}, None
    scopes, pending_scope = [], None  # one entry per open brace: a struct/class name, or None for any other block
    for ln in src.splitlines():
        t = ln.strip()
        m = re.match(r"//\s*(0x[0-9A-Fa-f]{8})\s*$", t)
        if m:
            pending = int(m.group(1), 16); continue
        code = t.split("//")[0]
        m2 = re.search(r"//\s*(0x[0-9A-Fa-f]{8})\s*$", t)
        sm = re.match(r"^(?:typedef\s+)?(?:struct|class|union)\s+([A-Za-z_]\w*)\b(?![^{]*;)", code)
        if sm:
            pending_scope = sm.group(1)
        if m2 and code.strip():
            nm = decl_name(code)
            if nm:
                names = [x for x in scopes if x]
                key = "::".join(names + [nm]) if names else nm
                addr = int(m2.group(1), 16)
                decls[key] = addr if key not in decls or decls[key] == addr else None
        if pending is not None and code.strip():
            fn = [w for w in re.findall(r"([A-Za-z_~]\w*)\s*\(", code) if w not in KEYWORDS]
            if fn:
                defs[fn[0]] = pending
            pending = None
        for ch in code:
            if ch == "{":
                scopes.append(pending_scope); pending_scope = None
            elif ch == "}" and scopes:
                scopes.pop()
    return defs, decls


def undec(n):
    m = re.match(r"^\?\?([01])([A-Za-z_0-9]+)@", n)
    if m:
        return ("~" if m.group(1) == "1" else "") + m.group(2)
    if n.startswith("@"):
        return n[1:].split("@")[0]
    if n.startswith("?"):
        return re.sub(r"^\?([A-Za-z_~0-9]+)@.*$", r"\1", n)
    return (n[1:] if n.startswith("_") else n).split("@")[0]


def external_marker_name(symbol):
    """The declaration a relocation symbol must carry a marker on, or None when it cannot be named soundly.
    Imports (`__imp__Name@4`, `__imp_Name`) map to the dllimport declaration of Name; scoped C++ symbols
    (`?Leaf@Class@Outer@@...`) map to `Outer::Class::Leaf`, which only a declaration inside those structs provides.
    Special members, operators and templates (`??...`, `?$...`) still need a full declaration parser.
    """
    m = re.fullmatch(r"__imp_(_?[A-Za-z_]\w*?)(?:@\d+)?", symbol)
    if m:
        return external_marker_name(m.group(1)) if m.group(1).startswith("_") else m.group(1)
    if symbol.startswith("?"):
        m = re.match(r"^\?([A-Za-z_]\w*)@((?:[A-Za-z_]\w*@)*)@", symbol)
        if not m:
            return None
        scope = [x for x in m.group(2).split("@") if x]
        return "::".join(list(reversed(scope)) + [m.group(1)])
    if re.fullmatch(r"_[A-Za-z_]\w*(?:@\d+)?|@[A-Za-z_]\w*@\d+", symbol):
        return undec(symbol)
    return None


def read_coff(path):
    with open(path, "rb") as f:
        d = f.read()
    if len(d) < 20 or struct.unpack_from("<H", d)[0] != 0x14c or struct.unpack_from("<H", d, 16)[0]:
        raise ValueError("expected an i386 COFF object")
    nsec, symptr, nsym = struct.unpack_from("<H", d, 2)[0], struct.unpack_from("<I", d, 8)[0], struct.unpack_from("<I", d, 12)[0]
    strtab = symptr + 18 * nsym
    if not nsec or 20 + 40 * nsec > len(d) or symptr < 20 + 40 * nsec or strtab + 4 > len(d):
        raise ValueError("truncated COFF headers or symbol table")
    strlen = struct.unpack_from("<I", d, strtab)[0]
    if strlen < 4 or strtab + strlen > len(d):
        raise ValueError("invalid COFF string table")

    def symname(o):
        raw = d[o:o + 8]
        if raw[:4] == b"\0\0\0\0":
            z = strtab + struct.unpack_from("<I", raw, 4)[0]
            if not strtab + 4 <= z < strtab + strlen:
                raise ValueError("invalid COFF symbol name")
            return d[z:d.index(b"\0", z, strtab + strlen)].decode("latin-1")
        return raw.rstrip(b"\0").decode("latin-1")
    secs = []
    for i in range(nsec):
        name, _, _, rsz, rptr, relptr, _, nrel, _, _ = struct.unpack_from("<8sIIIIIIHHI", d, 20 + 40 * i)
        if rptr + rsz > len(d) or relptr + 10 * nrel > len(d) or nrel == 0xffff:
            raise ValueError("truncated or unsupported COFF section/relocations")
        secs.append(dict(name=name.rstrip(b"\0").decode(), raw=d[rptr:rptr + rsz], relptr=relptr, nrel=nrel))
    syms, i = [], 0
    while i < nsym:
        o = symptr + 18 * i
        val, scn, _, cls, naux = struct.unpack_from("<IhHBB", d, o + 8)
        if scn > nsec or i + naux >= nsym:
            raise ValueError("invalid COFF symbol section or auxiliaries")
        syms.append(dict(name=symname(o), value=val, scn=scn, cls=cls, idx=i)); i += 1 + naux
    return d, secs, syms


COMPILE_TIMEOUT_SECONDS = 60
COMPILE_OUTPUT_BYTES = 65536


def run_compiler(cmd, wd, inc):
    """Bound local compilation. This is resource hygiene, NOT a security sandbox.

    Public submissions must additionally run in the host's isolated runner.
    Only runtime plumbing is inherited; CL/_CL_, credentials, and Python hooks
    must never enter the compiler environment.
    """
    env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "SystemDrive") if k in os.environ}
    paths = [os.path.dirname(os.path.abspath(cmd[0]))]
    if os.name == "nt":
        paths.append(os.path.join(env.get("SystemRoot", r"C:\Windows"), "System32"))
    else:
        paths.extend(["/usr/bin", "/bin"])
        env["HOME"] = os.path.expanduser("~")  # local Wine prefix, not host credentials
        if os.environ.get("WINEPREFIX"):
            env["WINEPREFIX"] = os.environ["WINEPREFIX"]  # the isolated runner's image sets one
    # TEMP/TMP are what the Windows compiler reads. TMPDIR is a Unix variable: Wine 9 (Ubuntu's wine 9.0) crashes at
    # start-up with it set, so it is left to the runtime.
    env.update(PATH=os.pathsep.join(paths), INCLUDE=inc, WINEDEBUG="-all", TEMP=wd, TMP=wd)
    captured = bytearray()
    overflow = threading.Event()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, cwd=wd,
                                start_new_session=(os.name != "nt"))
    except OSError as exc:
        return dict(status="ENVIRONMENT_ERROR", detail="cannot start compiler/runtime: %s" % exc)

    cleanup_lock = threading.Lock()
    stopped = False

    def stop_tree():
        # Kill descendants before the root process disappears. This is best-
        # effort local cleanup, not containment of deliberately detached code.
        nonlocal stopped
        with cleanup_lock:
            if stopped:
                return
            stopped = True
            if os.name == "nt":
                taskkill = os.path.join(env.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
                try:
                    subprocess.run([taskkill, "/PID", str(proc.pid), "/T", "/F"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=env, timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if proc.poll() is None:
                proc.kill()

    def drain():
        try:
            while True:
                chunk = proc.stdout.read1(4096)
                if not chunk:
                    break
                available = COMPILE_OUTPUT_BYTES - len(captured)
                captured.extend(chunk[:available])
                if len(chunk) > available:
                    overflow.set()
                    stop_tree()
                    break
        except OSError:
            pass

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    deadline = time.monotonic() + COMPILE_TIMEOUT_SECONDS
    timed_out = False
    try:
        proc.wait(timeout=COMPILE_TIMEOUT_SECONDS)
        reader.join(max(0, deadline - time.monotonic()))
        timed_out = reader.is_alive()
    except subprocess.TimeoutExpired:
        timed_out = True
    if timed_out or overflow.is_set():
        stop_tree()
        proc.wait()
        reader.join(0.2)
    if not reader.is_alive():
        proc.stdout.close()
    detail = bytes(captured).decode("utf-8", "replace")[-3000:]
    if overflow.is_set():
        return dict(status="COMPILE_OUTPUT_LIMIT", detail="compiler output exceeded limit\n" + detail)
    if timed_out:
        return dict(status="COMPILE_TIMEOUT", detail="compiler/runtime exceeded time limit\n" + detail)
    return dict(returncode=proc.returncode, detail=detail)


def compile_and_compare(s, va, length, flags, src, wd, image=None):
    """Compile src in wd with flags and compare the target function with the original. Returns a result dict.
    Used by `check` here and by the build host (same code, so the two can only disagree on environment)."""
    wd = os.path.abspath(wd)
    os.makedirs(wd, exist_ok=True)
    start = int(va, 16)
    try:
        source_bytes = src.encode("latin-1")
    except (UnicodeEncodeError, AttributeError):
        return dict(status="SOURCE_ERROR", detail="source must be Latin-1 text for the pinned VC2005 compiler")
    if not isinstance(length, int) or length <= 0:
        return dict(status="ENVIRONMENT_ERROR", detail="target length must be a positive integer")
    for bad in FORBIDDEN:
        if re.search(bad, src):
            return dict(status="FORBIDDEN", detail="forbidden construct %r" % bad)
    defs, decls = parse_markers(src)
    target = next((k for k, v in defs.items() if v == start), None)
    if not target:
        return dict(status="NO_MARKER", detail="no `// %s` line directly above a function definition" % va)
    if re.search(r"^\s*//\s*(iblis|d2match):\s*ob1\s*$", src, re.M) and "/Ob1" not in flags:
        flags = flags + ["/Ob1"]
    with open(os.path.join(wd, "candidate.cpp"), "wb") as f:
        f.write(source_bytes)
    obj = os.path.join(wd, "candidate.obj")
    if os.path.exists(obj):
        os.remove(obj)
    if s.get("runner") not in ("native", "wine"):
        return dict(status="ENVIRONMENT_ERROR", detail="runner must be native or wine")
    runtime = shutil.which("wine") if s.get("runner") == "wine" else None
    if s.get("runner") == "wine" and not runtime:
        return dict(status="ENVIRONMENT_ERROR", detail="Wine runtime is unavailable")
    cmd = ([s["cl"]] if s.get("runner") == "native" else [runtime, s["cl"]]) + flags + ["/Focandidate.obj", "candidate.cpp"]
    inc = os.path.join(KIT, "headers")
    if s.get("runner") != "native":
        inc = "Z:" + inc.replace("/", "\\")
    r = run_compiler(cmd, wd, inc)
    res = dict(command=" ".join(flags), command_argv=list(cmd), source_sha256=hashlib.sha256(source_bytes).hexdigest())
    if "status" in r:
        res.update(r)
        return res
    if r["returncode"] or not os.path.exists(obj):
        res.update(status="COMPILE_ERROR", detail=r["detail"] or "compiler produced no object")
        return res
    try:
        d, secs, syms = read_coff(obj)
    except (OSError, ValueError, struct.error, IndexError) as exc:
        res.update(status="OBJECT_ERROR", detail="invalid compiler object: %s" % exc)
        return res
    funcs = [y for y in syms if y["scn"] > 0 and (y["cls"] == 2 or (y["cls"] == 3 and y["value"] == 0 and not y["name"].startswith((".", "$"))))
             and secs[y["scn"] - 1]["name"].startswith(".text")]
    candidates = [y for y in funcs if undec(y["name"]) == target]
    if len(candidates) != 1:
        res.update(status="NO_SYMBOL", detail="the compiled object has no function named %s" % target)
        return res
    tgt = candidates[0]
    if tgt["value"] != 0:
        res.update(status="OBJECT_ERROR", detail="target must start its own /Gy section")
        return res
    sec = secs[tgt["scn"] - 1]; body = sec["raw"]
    try:
        pe = PE(image or s["image"]); orig = pe.read(start, length)
        if orig is None or len(orig) != length:
            raise ValueError("target bytes are absent or truncated")
    except (OSError, ValueError, struct.error, IndexError) as exc:
        res.update(status="ENVIRONMENT_ERROR", detail="cannot read target image: %s" % exc)
        return res
    # One form of the function per check: another body within 10% of its size counts as an extra copy, unless it carries
    # its own marker for a different address and resembles that address's original more than the target's.
    twins = []
    for y in funcs:
        hb = secs[y["scn"] - 1]["raw"]
        if y["scn"] == tgt["scn"] or len(body) < 64 or abs(len(hb) - len(body)) > len(body) // 10:
            continue
        hva = defs.get(undec(y["name"]))
        if hva and hva != start:
            ratio = lambda a, b: difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
            if ratio(hb, pe.read(hva, len(hb)) or b"") > ratio(hb, orig[:length]):
                continue
        twins.append(y["name"])
    rels, problems = [], []
    mask = bytearray(len(body))
    by_index = {y["idx"]: y for y in syms}
    external_names = {}
    for sy in syms:
        if sy["scn"] == 0:
            key = external_marker_name(sy["name"])
            external_names.setdefault(key, set()).add(sy["name"])
    for k in range(sec["nrel"]):
        off, si, ty = struct.unpack_from("<IIH", d, sec["relptr"] + 10 * k)
        sy = by_index.get(si)
        if off + 4 > len(body) or sy is None:
            res.update(status="OBJECT_ERROR", detail="invalid relocation offset or symbol index")
            return res
        addend = struct.unpack_from("<I", body, off)[0]
        actual = struct.unpack_from("<I", orig, off)[0] if off + 4 <= len(orig) else None
        nm = external_marker_name(sy["name"]) or undec(sy["name"])
        marker = decls.get(nm)
        if nm in decls and marker is None:
            problems.append("%s has conflicting address markers" % nm)
        original_target = None if actual is None else ((start + off + 4 + actual) & 0xffffffff if ty == 0x14 else actual)
        rels.append(dict(offset=off, symbol=sy["name"], type={0x14: "REL32", 6: "DIR32"}.get(ty, hex(ty)),
                         inplace=addend, declared=marker, original_target=original_target))
        if ty not in (0x14, 6):
            problems.append("unsupported relocation type 0x%x for %s" % (ty, sy["name"]))
            continue
        if any(mask[off:off + 4]):
            problems.append("overlapping relocations at +0x%x" % off)
            continue
        mask[off:off + 4] = b'\1' * 4
        if sy["scn"] == tgt["scn"] and sy["value"] < len(body):
            want = start + sy["value"]
            signed_addend = addend if addend < 0x80000000 else addend - 0x100000000
            if not 0 <= sy["value"] + signed_addend < len(body):
                problems.append("%s addend leaves the verified target section" % sy["name"])
            if marker is not None and marker != want:
                problems.append("%s marker conflicts with its actual section offset" % nm)
        elif sy["scn"] == 0 and sy["value"] == 0 and sy["cls"] == 2:
            key = external_marker_name(sy["name"])
            want = decls.get(key) if key and len(external_names.get(key, ())) == 1 else None
        elif (sy["scn"] > 0 and sy["value"] == 0 and secs[sy["scn"] - 1]["name"].startswith(".text")
              and defs.get(undec(sy["name"])) not in (None, start)
              and sum(1 for y in funcs if undec(y["name"]) == undec(sy["name"])) == 1):
            # A helper defined in this file (typically a `static` function, which MSVC may give a register
            # convention its callers then follow) carries its own `// 0x...` line above its definition: the call
            # must land exactly there, the same rule as for an external callee's marker. Its body is a dependency,
            # not part of this compare.
            want = defs[undec(sy["name"])]
        elif (ty == 6 and sy["scn"] > 0 and sy["name"].startswith(("??_C@", "__real@", "__xmm@")) and actual is not None
              and secs[sy["scn"] - 1]["name"].startswith((".rdata", ".data"))):
            # A string literal or float/vector constant (__real@, __xmm@) the compiler emitted itself (including the pieces an inline strcpy/strcat copies at
            # +4, +8, ...): it resolves when the literal's bytes equal the original's data at the address the
            # original uses, minus the same offset into the literal.
            lit = secs[sy["scn"] - 1]["raw"]
            signed_addend = addend if addend < 0x80000000 else addend - 0x100000000
            at = (actual - signed_addend) & 0xffffffff
            want = at if lit and pe.read(at, len(lit)) == lit else None
            if want is None:
                problems.append("literal %s does not equal the original's data at 0x%08X" % (sy["name"], at))
        else:
            want = None
            problems.append("%s has an unverified internal/common/absolute dependency" % sy["name"])
        if want is None:
            problems.append("unresolved relocation target %s; an explicit external address is required" % sy["name"])
            continue
        if off + 4 > len(orig):
            problems.append("relocation is outside the original function")
            continue
        expected = (want + addend - (start + off + 4 if ty == 0x14 else 0)) & 0xffffffff
        if actual != expected:
            problems.append("%s target/addend mismatch at +0x%x (expected 0x%08X, found 0x%08X)" % (sy["name"], off, expected, actual))
    raw_diffs = [i for i in range(min(len(body), len(orig))) if body[i] != orig[i]]
    diffs = [i for i in raw_diffs if not mask[i]]
    same_len = len(body) == length
    if twins:
        status = "MULTI_FORM"
    elif same_len and not raw_diffs and not problems:
        status = "RAW_EQUAL"
    elif same_len and not diffs and not problems:
        status = "RELOCATION_RESOLVED_EQUAL"
    else:
        status = "DIFF"
    res.update(status=status, compiled_length=len(body), length=length, unmasked_diff_bytes=len(diffs),
               first_diff=("+0x%x" % diffs[0]) if diffs else (("+0x%x" % raw_diffs[0]) if raw_diffs and not same_len else None),
               target_problems=problems, extra_forms=twins, symbol=tgt["name"],
               compiled_body_sha256=hashlib.sha256(body).hexdigest(), relocations=len(rels), relocation_records=rels,
               object_sha256=hashlib.sha256(d).hexdigest(), original_body_sha256=hashlib.sha256(orig).hexdigest(), claimed_length=length,
               extent_agrees=same_len, diff_bytes=len(raw_diffs))
    with open(os.path.join(wd, ".last_body.json"), "w") as f:
        json.dump(dict(body=body.hex(), mask=mask.hex()), f)  # local, for sxs
    return res


def cmd_check(va):
    va = norm_va(va); s = settings(); pk = load_packet(va)
    wd = os.path.join(WORK, va); cpp = os.path.join(wd, "candidate.cpp")
    if not os.path.exists(cpp):
        die("write %s first" % cpp)
    src = open(cpp, encoding="latin-1").read()
    t0 = time.time()
    res = compile_and_compare(s, va, pk["length"], pk["flags"].split(), src, wd)
    res["seconds"] = round(time.time() - t0, 1)
    log = os.path.join(wd, "checks.json")
    hist = json.load(open(log)) if os.path.exists(log) else []
    prev_best = min((h for h in hist if h.get("snapshot")), key=score, default=None)
    snap = os.path.join("history", "%03d.cpp" % (len(hist) + 1))
    os.makedirs(os.path.join(wd, "history"), exist_ok=True)
    shutil.copyfile(cpp, os.path.join(wd, snap))
    hist.append(dict(res, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), snapshot=snap)); json.dump(hist, open(log, "w"), indent=1)
    st = res["status"]
    if st in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL"):
        print("MATCH (%s)  %d B\nbody_sha256 %s\nsubmit candidate.cpp exactly as it is now" % (st, res["compiled_length"], res["compiled_body_sha256"]))
    elif st == "DIFF":
        print("DIFF  compiled %d B vs original %d B, %d bytes differ outside relocations, first at %s"
              % (res["compiled_length"], res["length"], res["unmasked_diff_bytes"], res["first_diff"]))
        for p in res["target_problems"]:
            print("  target:", p)
        print("  look closer: python iblis_check.py sxs %s" % va)
    elif st == "MULTI_FORM":
        print("REJECTED  other function bodies of about the target's size: %s. One form of the function per check." % ", ".join(res["extra_forms"]))
    else:
        print(st, " ", res.get("detail", ""))
    print("check %d for %s, %.1fs; source kept as %s" % (len(hist), va, res["seconds"], snap))
    if prev_best is not None and st not in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL"):
        if score(res) < score(prev_best):
            print("closest so far (previous best: %s)" % describe(prev_best))
        else:
            print("not closer than %s: %s  (python iblis_check.py best %s --restore)" % (prev_best["snapshot"], describe(prev_best), va))
    sys.exit(0 if st in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL") else 1)


def score(res):
    """Smaller is closer: a match, then same length with fewest differing bytes, then nearest length. Errors last."""
    st = res.get("status")
    if st in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL"):
        return (0, 0, 0)
    if st != "DIFF":
        return (3, 0, 0)
    off = abs(res.get("compiled_length", 0) - res.get("length", 0))
    # Bytes only one side has count as differing: otherwise a near-empty stub, compared over its few bytes, looks closer
    # than a real body that is a few bytes long.
    return (1 if not off else 2, res.get("unmasked_diff_bytes", 0) + off + len(res.get("target_problems", [])) * 4, off)


def describe(res):
    st = res.get("status")
    if st == "DIFF":
        return "%d B vs %d B, %d bytes differ%s" % (res.get("compiled_length", 0), res.get("length", 0), res.get("unmasked_diff_bytes", 0),
                                                 (", %d target problem(s)" % len(res["target_problems"])) if res.get("target_problems") else "")
    return st


def cmd_best(va, restore=False):
    va = norm_va(va); wd = os.path.join(WORK, va); log = os.path.join(wd, "checks.json")
    hist = [dict(h, n=i + 1) for i, h in enumerate(json.load(open(log)) if os.path.exists(log) else []) if h.get("snapshot")]
    if not hist:
        die("no kept checks for %s yet; run check first" % va)
    ranked = sorted(hist, key=lambda h: (score(h), -h["n"]))
    for h in ranked[:8]:
        print("check %3d  %-48s %s" % (h["n"], describe(h), h["snapshot"]))
    if restore:
        best = ranked[0]; cpp = os.path.join(wd, "candidate.cpp")
        if os.path.exists(cpp) and hashlib.sha256(open(cpp, "rb").read()).hexdigest() not in {h.get("source_sha256") for h in hist}:
            keep = os.path.join(wd, "history", "unchecked-%s.cpp" % time.strftime("%Y%m%dT%H%M%S"))
            shutil.copyfile(cpp, keep); print("kept your unchecked candidate.cpp as", os.path.relpath(keep, wd))
        shutil.copyfile(os.path.join(wd, best["snapshot"]), cpp)
        print("candidate.cpp is now check %d (%s)" % (best["n"], describe(best)))


def cmd_sweep(va):
    """Check every variants/*.cpp in its own folder; candidate.cpp is left alone. Rank them by closeness."""
    va = norm_va(va); s = settings(); pk = load_packet(va)
    vdir = os.path.join(WORK, va, "variants")
    files = sorted(glob.glob(os.path.join(vdir, "*.cpp")))
    if not files:
        die("put alternative spellings as %s/*.cpp first (each a complete candidate)" % vdir)
    rows = []
    for p in files:
        name = os.path.splitext(os.path.basename(p))[0]
        res = compile_and_compare(s, va, pk["length"], pk["flags"].split(), open(p, encoding="latin-1").read(), os.path.join(vdir, ".build", name))
        rows.append(dict(variant=os.path.basename(p), status=res["status"], score=list(score(res)), summary=describe(res),
                         compiled_body_sha256=res.get("compiled_body_sha256"), first_diff=res.get("first_diff")))
        print("  %-28s %s" % (os.path.basename(p), describe(res)))
    rows.sort(key=lambda r: r["score"])
    json.dump(rows, open(os.path.join(vdir, "SWEEP.json"), "w"), indent=1)
    print("closest: %s (%s). Copy it to candidate.cpp and run check before submitting." % (rows[0]["variant"], rows[0]["summary"]))
    same = {}
    for r in rows:
        if r["compiled_body_sha256"]:
            same.setdefault(r["compiled_body_sha256"], []).append(r["variant"])
    for group in same.values():
        if len(group) > 1:
            print("  same code: %s (the change between them does not matter to the compiler)" % ", ".join(group))


def cmd_sxs(va):
    import capstone
    va = norm_va(va); s = settings(); pk = load_packet(va); start = int(va, 16)
    p = os.path.join(WORK, va, ".last_body.json")
    if not os.path.exists(p):
        die("run check first")
    last = json.load(open(p)); body = bytes.fromhex(last["body"]); mask = bytes.fromhex(last["mask"])
    orig = PE(s["image"]).read(start, pk["length"])
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    a = [(i.address - start, i.size, i.mnemonic, i.op_str) for i in md.disasm(body, start)]
    b = [(i.address - start, i.size, i.mnemonic, i.op_str) for i in md.disasm(orig, start)]
    rows = align_rows(a, b, body, orig, mask)
    show_all = "--all" in sys.argv
    keep = set(range(len(rows))) if show_all else {j for i, r in enumerate(rows) if r[0] not in " ~" for j in range(i - 3, i + 4)}
    print("%-7s %-44s | %s" % ("yours", "", "original"))
    last = -1
    for i, (flag, x, y) in enumerate(rows):
        if i not in keep:
            continue
        if last >= 0 and i != last + 1:
            print("   ...")
        last = i
        print("%s %-6s %-44s | %-6s %s" % (flag, "+%x" % x[0] if x else "", ("%s %s" % (x[2], x[3]))[:44] if x else "",
                                         "+%x" % y[0] if y else "", "%s %s" % (y[2], y[3]) if y else ""))
    tally = {}
    for flag, x, y in rows:
        tally[flag] = tally.get(flag, 0) + 1
    print("\n! different instruction  r same instruction, different registers  ~ only a relocated address differs  "
          "< only in yours  > only in the original")
    print("totals: " + (", ".join("%s %d" % (k, v) for k, v in sorted(tally.items()) if k != " ") or "no differences") +
          ("" if show_all else "   (--all shows every line, including relocation-only ones)"))


REGS = re.compile(r"\b(?:e?[abcd]x|[abcd][lh]|e?(?:si|di|bp|sp)|[sd]il|st\(\d\)|xmm\d)\b")


def align_rows(a, b, body, orig, mask):
    """Pair your instructions with the original's by edit distance over mnemonics, so one extra instruction does not
    shift every later line. Rows are (flag, yours, original); instructions are (offset, size, mnemonic, operands)."""
    key = lambda i: i[2]
    sm = difflib.SequenceMatcher(None, [key(x) for x in a], [key(y) for y in b], autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or tag == "replace" and i2 - i1 == j2 - j1:
            for x, y in zip(a[i1:i2], b[j1:j2]):
                xb, yb = body[x[0]:x[0] + x[1]], orig[y[0]:y[0] + y[1]]
                if xb == yb:
                    flag = " "
                elif x[2] == y[2] and x[1] == y[1] and any(mask[x[0]:x[0] + x[1]]) and \
                        all(xb[k] == yb[k] or mask[x[0] + k] for k in range(x[1])):
                    flag = "~"
                elif x[2] == y[2] and REGS.sub("R", x[3]) == REGS.sub("R", y[3]):
                    flag = "r"
                else:
                    flag = "!"
                rows.append((flag, x, y))
        else:
            for k in range(max(i2 - i1, j2 - j1)):
                x = a[i1 + k] if i1 + k < i2 else None
                y = b[j1 + k] if j1 + k < j2 else None
                rows.append(("!" if x and y else "<" if x else ">", x, y))
    return rows


def call_sites(pe, va):
    """Addresses of every direct `call va` in .text (E8 rel32)."""
    sec = next(x for x in pe.sections if x["name"] == ".text")
    lo = sec["rawptr"]; code = pe.data[lo:lo + sec["rawsize"]]; base = pe.base + sec["va"]
    out, i = [], code.find(b"\xe8")
    while i != -1 and i + 5 <= len(code):
        if (base + i + 5 + struct.unpack_from("<i", code, i + 1)[0]) & 0xFFFFFFFF == va:
            out.append(base + i)
        i = code.find(b"\xe8", i + 1)
    return out


def cmd_callers(va):
    """Who calls VA, with the instructions leading up to each call, read from YOUR Game.exe (terminal only: never
    paste it anywhere). Use it for a function that takes values in EAX/EBX/ESI/EDI: write it as a `static` function
    and put a caller in the same file, with its own `// 0x...` line, that sets those values up the same way."""
    import capstone
    va = norm_va(va); s = settings(); pe = PE(s["image"]); t = int(va, 16)
    names = json.load(open(os.path.join(KIT, "names.json")))
    sites = call_sites(pe, t)
    if not sites:
        print("no direct calls to %s (it may be reached through a pointer or a jump table)" % va); return
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for site in sites[:8]:
        # walk back to the nearest preceding padding or return to find the start of the calling function
        # The profile keeps frame pointers, so a function starts `push ebp; mov ebp, esp` right after padding or
        # the previous function's return.
        win = pe.read(site - 0x800, 0x800) or b""
        start, j = None, win.rfind(b"\x55\x8b\xec")
        while j > 0:
            if win[j - 1] in (0xCC, 0x90, 0xC3) or (j >= 3 and win[j - 3] == 0xC2):
                start = site - 0x800 + j; break
            j = win.rfind(b"\x55\x8b\xec", 0, j)
        start = start or site - 0x40
        print("call at 0x%08X, in the function starting near 0x%08X %s" % (site, start, names.get("0x%08X" % start, "")))
        ins = list(md.disasm(pe.read(start, site - start + 5), start))
        for i in ins[-14:]:
            print("    %08X  %s %s" % (i.address, i.mnemonic, i.op_str))
    if len(sites) > 8:
        print("... %d more call sites" % (len(sites) - 8))


def cmd_selftest():
    s = settings(); t = CFG["selftest"]
    wd = os.path.join(WORK, "_selftest")
    with open(os.path.join(KIT, "selftest", "candidate.cpp"), encoding="latin-1") as f:
        src = f.read()
    res = compile_and_compare(s, t["va"], t["length"], t["flags"].split(), src, wd)
    if res["status"] in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL") and res["compiled_body_sha256"] == t["compiled_body_sha256"]:
        print("MATCH  selftest %s compiled to the verified body; your setup is ready" % t["va"])
        return
    detail = res.get("detail") or "; ".join(res.get("target_problems", [])) or res.get("first_diff") or "body/hash differs"
    die("selftest did not match (%s): %s" % (res["status"], detail))


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__); return
    c = argv[1]
    if c == "setup":
        cmd_setup(argv[2:])
    elif c == "selftest":
        cmd_selftest()
    elif c in ("brief", "check", "sxs", "sweep") and len(argv) >= 3:
        {"brief": cmd_brief, "check": cmd_check, "sxs": cmd_sxs, "sweep": cmd_sweep}[c](argv[2])
    elif c == "best" and len(argv) >= 3:
        cmd_best(argv[2], restore="--restore" in argv)
    elif c == "decl" and len(argv) >= 3:
        cmd_decl(argv[2:])
    elif c == "fields" and len(argv) >= 3:
        cmd_fields(argv[2:])
    elif c == "layout":
        cmd_layout(argv[2:])
    elif c == "callers" and len(argv) >= 3:
        cmd_callers(argv[2])
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
