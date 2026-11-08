#!/usr/bin/env python3
"""Mine the verified C++ in the source repository into a context index for the checker kit.

usage: build_context.py <repo> <out.json>

The index holds only declarations and type layouts, never function bodies:
  decls    address -> the declaration verified sources use for it (a verified definition's own signature wins,
           then the most common declaration), with its calling convention and how many sources agree
  fields   struct -> offset -> [type, name, sources]   (members seen at that offset across verified sources)
  sizes    struct -> sizes seen for complete definitions
  typedefs name -> most common typedef line
  enums    name -> most common enum definition
Sources: queue/matched/<VA>/candidate.cpp and src/**/*.cpp of the private repository. Parts of those sources were
adapted from MIT-licensed projects; build_kit.py ships their notices beside this index.
"""
import collections, glob, json, os, re, sys

MARK = re.compile(r"^\s*(?:0x([0-9A-Fa-f]{8})\b|.*?\bat\s+0x([0-9A-Fa-f]{8})\b)")
DECL = re.compile(r"^[^=;{}()]*\b[A-Za-z_~]\w*\s*\([^;{}]*\)\s*(?:const)?$")
CONVS = ("__cdecl", "__stdcall", "__fastcall", "__thiscall")
BUILTIN = {"void", "char", "short", "int", "long", "float", "double", "bool", "signed", "unsigned", "const", "volatile",
           "struct", "class", "union", "enum", "__int8", "__int16", "__int32", "__int64", "wchar_t", "static",
           "extern", "inline", "__forceinline", "__declspec", "dllimport", "noreturn", "__cdecl", "__stdcall", "__fastcall",
           "__thiscall", "virtual", "operator", "sizeof", "register", "__w64", "__ptr32", "__unaligned", "restrict",
           "__restrict", "typename", "template", "return"}
SIZES = {"char": 1, "signed char": 1, "unsigned char": 1, "bool": 1, "short": 2, "unsigned short": 2, "int": 4,
         "unsigned int": 4, "unsigned": 4, "long": 4, "unsigned long": 4, "float": 4, "double": 8, "__int64": 8,
         "unsigned __int64": 8, "long long": 8, "wchar_t": 2, "BYTE": 1, "WORD": 2, "DWORD": 4, "BOOL": 4,
         "uint8_t": 1, "uint16_t": 2, "uint32_t": 4, "int8_t": 1, "int16_t": 2, "int32_t": 4}


def split_lines(text):
    """(code, marker) per line; comments dropped, string/char literals respected, block comments removed."""
    out, in_block = [], False
    for raw in text.split("\n"):
        code, comment, i, n = [], "", 0, len(raw)
        while i < n:
            if in_block:
                j = raw.find("*/", i)
                if j < 0:
                    i = n; break
                in_block, i = False, j + 2; continue
            c = raw[i]
            if c in "\"'":
                j = i + 1
                while j < n and raw[j] != c:
                    j += 2 if raw[j] == "\\" else 1
                code.append(raw[i:j + 1]); i = j + 1; continue
            if raw.startswith("//", i):
                comment = raw[i + 2:]; break
            if raw.startswith("/*", i):
                in_block, i = True, i + 2; continue
            code.append(c); i += 1
        m = MARK.match(comment) if comment else None
        marker = int(m.group(1) or m.group(2), 16) if m else None
        out.append(("".join(code), marker, comment.strip() if comment else ""))
    return out


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def statements(lines):
    """Yield (kind, text, marker, scope) for statements outside function bodies.
    kind: decl (ends in ';'), def (a function definition header), typedef. scope: the enclosing struct, if any."""
    buf, stack, pending, skip = "", [], None, 0
    for code, marker, comment in lines:
        if not code.strip():
            if marker is not None and re.match(r"^0x[0-9A-Fa-f]{8}\s*$", comment):
                pending = marker
            continue
        if code.lstrip().startswith("#"):
            continue
        for ch in code:
            if skip:
                skip += (ch == "{") - (ch == "}")
                if not skip:
                    buf = ""
                continue
            buf += ch
            if ch == "{":
                head = norm(buf[:-1]); buf = ""
                tm = re.match(r"^(?:typedef\s+)?(?:struct|class|union)\s+(?:__declspec\([^)]*\)\s+)?([A-Za-z_]\w*)\s*(?::[^{(]*)?$", head)
                if tm:
                    stack.append(("type", tm.group(1)))
                elif re.match(r"^(?:namespace\b|extern\s+\"C\")", head):
                    stack.append(("ns", None))
                elif re.search(r"\)\s*(?:const\s*)?(?::.*)?$", head) and not head.startswith(("enum", "typedef enum")) and "=" not in head.split("(")[0]:
                    scope = next((n for k, n in reversed(stack) if k == "type"), None)
                    if DECL.match(re.sub(r"\s*:[^:].*$", "", head)) and not re.match(r"^(?:for|if|while|switch|else|do|return)\b", head):
                        yield ("def", head, pending, scope)
                    pending = None; skip = 1
                else:
                    skip = 1  # enum bodies, initialisers, anything else
            elif ch == "}":
                if stack:
                    stack.pop()
                buf = ""
            elif ch == ";":
                st = norm(buf[:-1]); buf = ""
                if st:
                    scope = next((n for k, n in reversed(stack) if k == "type"), None)
                    yield ("typedef" if st.startswith("typedef") else "decl", st, marker, scope)
        buf += " "


def type_bodies(text):
    """(kind, name, body-with-comments) for every struct/class/union/enum definition."""
    for m in re.finditer(r"\b(struct|class|union|enum)\s+(?:__declspec\([^)]*\)\s+)?([A-Za-z_]\w*)\s*(?::[^{;()]*)?\{", text):
        i, depth = m.end(), 1
        while i < len(text) and depth:
            depth += (text[i] == "{") - (text[i] == "}"); i += 1
        if not depth:
            yield m.group(1), m.group(2), text[m.end():i - 1]


def conv_of(text):
    return next((c for c in CONVS if c in text), "__cdecl" if "(" in text else None)


def type_words(text):
    words = set(re.findall(r"\b[A-Za-z_]\w*\b", text))
    return words


def parse_members(body):
    """Members of one struct body with offsets: from '+0x..' comments when present, else computed while sizes are known."""
    out, off, known = [], 0, True
    for code, marker, comment in split_lines(body):
        c = code.strip()
        if not c or c.startswith(("#", "public:", "private:", "protected:")) or "(" in c:
            continue
        for part in [p for p in c.split(";") if p.strip()]:
            m = re.match(r"^(?:volatile\s+)?((?:const\s+)?(?:unsigned\s+|signed\s+)?[A-Za-z_][\w:]*(?:\s+(?:int|char|short|long|__int64))?)(\s*\*+\s*|\s+)([A-Za-z_]\w*)\s*((?:\[[^\]]+\])*)\s*(?::\s*\d+)?$", part.strip())
            if not m or m.group(1) in ("union", "struct", "class", "enum", "return", "typedef"):
                known = False; continue
            ty, nm, arr = norm(m.group(1) + m.group(2).strip()), m.group(3), m.group(4)
            cm = re.search(r"\+\s*0x([0-9A-Fa-f]+)", comment)
            if cm:
                off, known = int(cm.group(1), 16), True
            size = 4 if ty.endswith("*") else SIZES.get(ty)
            count = 1
            for a in re.findall(r"\[([^\]]+)\]", arr):
                try:
                    count *= int(a, 0)
                except ValueError:
                    count = None; break
            if known:
                out.append((off, ty, nm + arr, size * count if size and count else None))
            if known and size and count:
                off += size * count
            else:
                known = False
    return out


def build(repo):
    files = sorted(glob.glob(os.path.join(repo, "queue", "matched", "*", "candidate.cpp"))) + \
        sorted(glob.glob(os.path.join(repo, "src", "**", "*.cpp"), recursive=True))
    defs, decls, methods = {}, collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    fields = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    sizes = collections.defaultdict(collections.Counter)
    typedefs, enums = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    for p in files:
        try:
            text = open(p, encoding="latin-1").read()
        except OSError:
            continue
        lines = split_lines(text)
        for kind, t, marker, scope in statements(lines):
            if kind == "def" and marker is not None:
                sig = re.sub(r"^\s*(?:static\s+|inline\s+|__forceinline\s+)*", "", t)
                if scope is None and "::" not in sig.split("(")[0]:
                    defs.setdefault(marker, collections.Counter())[sig + ";"] += 1
            elif kind == "decl" and marker is not None and (DECL.match(t) or t.startswith("extern")):
                if scope:
                    methods[marker][(scope, t + ";")] += 1
                else:
                    decls[marker][t + ";"] += 1
            elif kind == "typedef" and scope is None and "{" not in t:
                fp = re.search(r"\(\s*(?:__\w+\s+)?\*\s*([A-Za-z_]\w*)\s*\)\s*\(", t)  # typedef R (__cc *NAME)(...)
                m = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?$", t)
                if fp:
                    typedefs[fp.group(1)][t + ";"] += 1
                elif m and "(" not in t:
                    typedefs[m.group(1)][t + ";"] += 1
        for k, name, body in type_bodies(text):
            if k == "enum":
                enums[name]["enum %s {%s};" % (name, norm(" ".join(c for c, _, _ in split_lines(body))))] += 1
                continue
            mem = parse_members(body)
            for off, ty, nm, sz in mem:
                if not re.match(r"^(unk|pad|_pad|nUnk|dwUnk|unknown|reserved|gap|_unk)", nm):
                    fields[name][off][(ty, nm)] += 1
            if mem and all(sz for *_, sz in mem):
                last = max(mem, key=lambda x: x[0])
                sizes[name][last[0] + last[3]] += 1
    out = dict(sources=len(files), decls={}, fields={}, sizes={}, typedefs={}, enums={})
    for a in set(defs) | set(decls) | set(methods):
        va = "0x%08X" % a
        if a in defs:
            text, n = defs[a].most_common(1)[0]; how = "verified definition"
        elif a in decls:
            text, n = decls[a].most_common(1)[0]; how = "declaration"
        else:
            (scope, text), n = methods[a].most_common(1)[0]; how = "member of " + scope
        e = dict(decl=text, conv=conv_of(text), sources=n + sum(decls[a].values()) - (n if how == "declaration" else 0), how=how)
        if how.startswith("member"):
            e["struct"] = scope
        alts = [t for t, _ in decls[a].most_common(3) if t != text]
        if alts:
            e["alternatives"] = alts[:2]
        out["decls"][va] = e
    for s, offs in fields.items():
        out["fields"][s] = {"+0x%X" % o: [[ty, nm, n] for (ty, nm), n in c.most_common(3)] for o, c in sorted(offs.items())}
    out["sizes"] = {s: "0x%X" % max(c) for s, c in sizes.items()}  # the largest complete layout seen
    out["typedefs"] = {k: c.most_common(1)[0][0] for k, c in typedefs.items()}
    out["enums"] = {k: c.most_common(1)[0][0] for k, c in enums.items()}
    return out


if __name__ == "__main__":
    idx = build(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(idx, f, separators=(",", ":"), sort_keys=True)
    print("context: %d sources, %d declarations, %d structs with fields, %d typedefs, %d enums -> %s (%d KB)" % (
        idx["sources"], len(idx["decls"]), len(idx["fields"]), len(idx["typedefs"]), len(idx["enums"]), sys.argv[2],
        os.path.getsize(sys.argv[2]) // 1024))
