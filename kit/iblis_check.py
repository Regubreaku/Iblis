#!/usr/bin/env python3
"""iblis-check: the Project Iblis checker. Everything happens on this machine, against YOUR copy of Game.exe.

  python iblis_check.py setup --game PATH [--cl PATH | --fetch]   verify the game, find or fetch the compiler
  python iblis_check.py selftest                                compile one verified function; must print MATCH
  python iblis_check.py brief VA                                work/<VA>/BRIEF.md from work/<VA>/packet.json
  python iblis_check.py check VA                                compile work/<VA>/candidate.cpp and compare
  python iblis_check.py sxs VA                                  side-by-side of the last check (terminal only)

Nothing this tool reads from the game is ever written anywhere but work/, and work/ is never submitted. Submit only
candidate.cpp and your notes, through the service.
"""
import difflib, hashlib, json, os, re, shutil, struct, subprocess, sys, time

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
        self.data = d = open(path, "rb").read()
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
        if not s:
            return None
        o = s["rawptr"] + va - self.base - s["va"]
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
            print("fetching the compiler (Microsoft Visual C++ 2005 SP1, Microsoft software under its own licence) ...")
            if os.path.exists(root):
                shutil.rmtree(root)
            for step in (["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", tc["repo"], root],
                         ["git", "-C", root, "checkout", "--quiet", tc["commit"], "--", "bin"]):
                r = subprocess.run(step, capture_output=True, text=True)
                if r.returncode:
                    die("fetching the compiler failed:\n" + r.stderr[-800:])
        cl = os.path.join(root, "bin", "cl.exe")
    else:
        die("give --cl PATH (an existing cl.exe) or --fetch")
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
        f.write("## Declarations\nDeclare every callee and global above with a trailing `// 0x...` marker, e.g.\n"
                "`int __fastcall SOME_Func(int a); // 0x0052E510` and `extern int gnGlobal_007A285C; // 0x007A285C`.\n"
                "Put `// %s` on its own line directly above your function's definition.\n" % va)
    print("wrote %s: %d calls, %d globals, %d strings" % (out, len(calls), len(dref), len(strings)))


# ---------------------------------------------------------------- check

FORBIDDEN = [r"\b__asm\b", r"\basm\b", r"\b_emit\b", r"\bnaked\b", r"#pragma\s+(optimize|code_seg|check_stack|function)"]


def parse_markers(src):
    defs, decls, pending = {}, {}, None
    for ln in src.splitlines():
        t = ln.strip()
        m = re.match(r"//\s*(0x[0-9A-Fa-f]{8})\s*$", t)
        if m:
            pending = int(m.group(1), 16); continue
        code = t.split("//")[0]
        m2 = re.search(r"//\s*(0x[0-9A-Fa-f]{8})\s*$", t)
        if m2 and code.strip():
            fn = re.findall(r"([A-Za-z_~]\w*)\s*\(", code)
            nm = fn[0] if fn else (re.findall(r"([A-Za-z_]\w*)\s*(?:\[|;|=)", code) or [None])[-1]
            if nm:
                decls[nm] = int(m2.group(1), 16)
        if pending is not None and code.strip():
            fn = re.findall(r"([A-Za-z_~]\w*)\s*\(", code)
            if fn:
                defs[fn[0]] = pending
            pending = None
    return defs, decls


def undec(n):
    m = re.match(r"^\?\?([01])([A-Za-z_0-9]+)@", n)
    if m:
        return ("~" if m.group(1) == "1" else "") + m.group(2)
    if n.startswith("@"):
        return n[1:].split("@")[0]
    return re.sub(r"^\?\??_?([A-Za-z_~0-9]+)@.*$", r"\1", n).lstrip("_").split("@")[0]


def read_coff(path):
    d = open(path, "rb").read()
    nsec, symptr, nsym = struct.unpack_from("<H", d, 2)[0], struct.unpack_from("<I", d, 8)[0], struct.unpack_from("<I", d, 12)[0]
    strtab = symptr + 18 * nsym

    def symname(o):
        raw = d[o:o + 8]
        if raw[:4] == b"\0\0\0\0":
            z = strtab + struct.unpack_from("<I", raw, 4)[0]
            return d[z:d.index(b"\0", z)].decode("latin-1")
        return raw.rstrip(b"\0").decode("latin-1")
    secs = []
    for i in range(nsec):
        name, _, _, rsz, rptr, relptr, _, nrel, _, _ = struct.unpack_from("<8sIIIIIIHHI", d, 20 + 40 * i)
        secs.append(dict(name=name.rstrip(b"\0").decode(), raw=d[rptr:rptr + rsz], relptr=relptr, nrel=nrel))
    syms, i = [], 0
    while i < nsym:
        o = symptr + 18 * i
        val, scn, _, cls, naux = struct.unpack_from("<IhHBB", d, o + 8)
        syms.append(dict(name=symname(o), value=val, scn=scn, cls=cls, idx=i)); i += 1 + naux
    return d, secs, syms


def compile_and_compare(s, va, length, flags, src, wd, image=None):
    """Compile src in wd with flags and compare the target function with the original. Returns a result dict.
    Used by `check` here and by the build host (same code, so the two can only disagree on environment)."""
    start = int(va, 16)
    for bad in FORBIDDEN:
        if re.search(bad, src):
            return dict(status="FORBIDDEN", detail="forbidden construct %r" % bad)
    defs, decls = parse_markers(src)
    target = next((k for k, v in defs.items() if v == start), None)
    if not target:
        return dict(status="NO_MARKER", detail="no `// %s` line directly above a function definition" % va)
    if re.search(r"^\s*//\s*(iblis|d2match):\s*ob1\s*$", src, re.M) and "/Ob1" not in flags:
        flags = flags + ["/Ob1"]
    os.makedirs(wd, exist_ok=True)
    open(os.path.join(wd, "candidate.cpp"), "w", encoding="latin-1", newline="\n").write(src)
    obj = os.path.join(wd, "candidate.obj")
    if os.path.exists(obj):
        os.remove(obj)
    cmd = ([s["cl"]] if s.get("runner") == "native" else ["wine", s["cl"]]) + flags + ["/Focandidate.obj", "candidate.cpp"]
    inc = os.path.join(KIT, "headers")
    if s.get("runner") != "native":
        inc = "Z:" + inc.replace("/", "\\")
    env = dict(os.environ, INCLUDE=inc, WINEDEBUG="-all")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=wd)
    res = dict(command=" ".join(flags), source_sha256=hashlib.sha256(src.encode("latin-1")).hexdigest())
    if r.returncode or not os.path.exists(obj):
        res.update(status="COMPILE_ERROR", detail=(r.stdout + r.stderr)[-3000:])
        return res
    d, secs, syms = read_coff(obj)
    funcs = [y for y in syms if y["scn"] > 0 and (y["cls"] == 2 or (y["cls"] == 3 and y["value"] == 0 and not y["name"].startswith((".", "$"))))
             and secs[y["scn"] - 1]["name"].startswith(".text")]
    tgt = next((y for y in funcs if undec(y["name"]) == target or y["name"].lstrip("_").split("@")[0] == target), None)
    if not tgt:
        res.update(status="NO_SYMBOL", detail="the compiled object has no function named %s" % target)
        return res
    sec = secs[tgt["scn"] - 1]; body = sec["raw"]
    pe = PE(image or s["image"]); orig = pe.read(start, max(length, len(body)))
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
    rels = []
    for k in range(sec["nrel"]):
        off, si, ty = struct.unpack_from("<IIH", d, sec["relptr"] + 10 * k)
        sy = next(y for y in syms if y["idx"] == si)
        rels.append(dict(offset=off, symbol=sy["name"], type={0x14: "REL32", 6: "DIR32"}.get(ty, hex(ty)),
                         inplace=struct.unpack_from("<I", body, off)[0]))
    mask = bytearray(len(body))
    for rl in rels:
        for k in range(4):
            mask[rl["offset"] + k] = 1
    raw_diffs = [i for i in range(min(len(body), len(orig))) if body[i] != orig[i]]
    diffs = [i for i in raw_diffs if not mask[i]]
    problems = []
    for rl in rels:
        ov = struct.unpack_from("<I", orig, rl["offset"])[0]
        if rl["type"] == "REL32":
            disp = ov - (1 << 32) if ov >= 1 << 31 else ov
            ot = (start + rl["offset"] + 4 + disp) & 0xFFFFFFFF
        else:
            add = rl["inplace"] - (1 << 32) if rl["inplace"] >= 1 << 31 else rl["inplace"]
            ot = (ov - add) & 0xFFFFFFFF
        want = decls.get(undec(rl["symbol"]))
        if want is not None and want != ot:
            problems.append("%s is declared at 0x%08X but the original points at 0x%08X" % (undec(rl["symbol"]), want, ot))
    same_len = len(body) == length
    if twins:
        status = "MULTI_FORM"
    elif same_len and not raw_diffs:
        status = "RAW_EQUAL"
    elif same_len and not diffs and not problems:
        status = "RELOCATION_RESOLVED_EQUAL"
    else:
        status = "DIFF"
    res.update(status=status, compiled_length=len(body), length=length, unmasked_diff_bytes=len(diffs),
               first_diff=("+0x%x" % diffs[0]) if diffs else (("+0x%x" % raw_diffs[0]) if raw_diffs and not same_len else None),
               target_problems=problems, extra_forms=twins, symbol=tgt["name"],
               compiled_body_sha256=hashlib.sha256(body).hexdigest(), relocations=len(rels))
    json.dump(dict(body=body.hex(), mask=mask.hex()), open(os.path.join(wd, ".last_body.json"), "w"))  # local, for sxs
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
    hist.append(dict(res, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))); json.dump(hist, open(log, "w"), indent=1)
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
    print("check %d for %s, %.1fs" % (len(hist), va, res["seconds"]))
    sys.exit(0 if st in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL") else 1)


def cmd_sxs(va):
    import capstone
    va = norm_va(va); s = settings(); pk = load_packet(va); start = int(va, 16)
    p = os.path.join(WORK, va, ".last_body.json")
    if not os.path.exists(p):
        die("run check first")
    last = json.load(open(p)); body = bytes.fromhex(last["body"]); mask = bytes.fromhex(last["mask"])
    orig = PE(s["image"]).read(start, pk["length"])
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    a = [(i.address - start, i.size, "%s %s" % (i.mnemonic, i.op_str)) for i in md.disasm(body, start)]
    b = [(i.address - start, i.size, "%s %s" % (i.mnemonic, i.op_str)) for i in md.disasm(orig, start)]
    print("%-6s %-44s | %s" % ("off", "yours", "original"))
    for k in range(max(len(a), len(b))):
        x = a[k] if k < len(a) else (None, 0, "")
        y = b[k] if k < len(b) else (None, 0, "")
        same = x[0] == y[0] and body[x[0]:x[0] + x[1]] == orig[y[0]:y[0] + y[1]] if x[0] is not None and y[0] is not None else False
        masked = x[0] is not None and any(mask[x[0]:x[0] + x[1]])
        flag = " " if same else ("~" if masked and x[2].split()[0] == y[2].split()[0] else "!")
        print("%s+%-5x %-44s | %s" % (flag, x[0] if x[0] is not None else y[0], x[2][:44], y[2]))
    print("! differs   ~ differs only in a relocated address")


def cmd_selftest():
    s = settings(); t = CFG["selftest"]
    wd = os.path.join(WORK, "_selftest")
    src = open(os.path.join(KIT, "selftest", "candidate.cpp"), encoding="latin-1").read()
    res = compile_and_compare(s, t["va"], t["length"], t["flags"].split(), src, wd)
    if res["status"] in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL") and res["compiled_body_sha256"] == t["compiled_body_sha256"]:
        print("MATCH  selftest %s compiled to the verified body; your setup is ready" % t["va"])
        return
    die("selftest did not match (%s): %s" % (res["status"], res.get("detail") or res.get("first_diff")))


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__); return
    c = argv[1]
    if c == "setup":
        cmd_setup(argv[2:])
    elif c == "selftest":
        cmd_selftest()
    elif c in ("brief", "check", "sxs") and len(argv) >= 3:
        {"brief": cmd_brief, "check": cmd_check, "sxs": cmd_sxs}[c](argv[2])
    else:
        print(__doc__); sys.exit(2)


if __name__ == "__main__":
    main(sys.argv)
