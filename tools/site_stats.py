"""Apply the public site's contributor and model attribution to generated counts."""
import re
from collections import defaultdict

# A GitHub login: letters, digits and single hyphens, at most 39 characters. Anything else in a names map is ignored.
LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
IBLIS = re.compile(r"^iblis-(\d+)$", re.I)


def parse_names(doc):
    """The service's opt-in login map (GET /api/v1/names: {"names": [{"github_id": n, "login": "x"}, ...]}, or the bare
    list) as {github id: login}. Entries without a positive integer id or a valid GitHub login are dropped, never
    guessed. The map lists only contributors who turned on "Show my GitHub name on the board"."""
    rows = doc.get("names") if isinstance(doc, dict) else doc
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        gid, login = r.get("github_id"), r.get("login")
        if isinstance(gid, str) and gid.isdigit():
            gid = int(gid)
        if isinstance(gid, int) and not isinstance(gid, bool) and gid > 0 and isinstance(login, str) and LOGIN.match(login):
            out[gid] = login
    return out


def normalize_stats(stats, names=None):
    """Merge contributor and model spellings. Site contributors are imported as iblis-<github id>: one whose id is in
    `names` (parse_names: the opted-in contributors only) is shown by GitHub login, every other one is merged into
    "Anonymous". The id itself never reaches the output. A login row carries "github": true, so a later pass (build_site
    renormalises the published file) keeps it exactly as written instead of re-capitalising it as a handle."""
    # iblis-120912283 is the owner's own GitHub account (Regubreaku): credited as Legbreak whatever the map says.
    aliases = {'lead': 'Legbreak', 'leg': 'Legbreak', 'fable': 'Legbreak', 'iblis-120912283': 'Legbreak',
               'legbreak': 'Legbreak', 'codex': 'Nathan', 'nathan': 'Nathan'}
    names = names or {}
    for field, key in (('leaderboard', 'who'), ('by_model', 'model')):
        grouped = defaultdict(lambda: {'functions': 0, 'bytes': 0})
        for row in stats[field]:
            name, github = row[key], False
            if key == 'who':
                m = IBLIS.match(name)
                if row.get('github') is True:
                    github = True
                elif name.casefold() in aliases:
                    name = aliases[name.casefold()]
                elif m:
                    login = names.get(int(m.group(1)))
                    name, github = (login, True) if login else ('Anonymous', False)
                else:
                    name = name[:1].upper() + name[1:]
            elif name == 'lead':
                name = 'claude-opus-5-5'
            grouped[(name, github)]['functions'] += row['functions']
            grouped[(name, github)]['bytes'] += row['bytes']
        # A login that reads like a project handle (or "Anonymous") must not take that row's credit, or give it its own.
        taken = {n.casefold() for n, g in grouped if not g}
        merged = defaultdict(lambda: {'functions': 0, 'bytes': 0})
        for (name, github), counts in grouped.items():
            if github and name.casefold() in taken:
                name += ' (GitHub)'
            for k in ('functions', 'bytes'):
                merged[(name, github)][k] += counts[k]
        stats[field] = sorted(({key: name, **counts, **({'github': True} if github else {})}
                               for (name, github), counts in merged.items()),
                              key=lambda row: (-row['bytes'], row[key]))
    return stats


# ---- code-section bar ----------------------------------------------------------------------------------------------
# The build host measures every byte of Game.exe's .text and writes per-category totals (TEXT_COVERAGE.json). The site
# only ever receives those totals: no bytes, names or code. Categories, all in bytes and mutually exclusive:
#   matched, submitted, claimed, open, parked   bytes covered by a queue record in that state
#   not_function                                 queue records confirmed not to be function starts (fragments, data)
#   padding                                      alignment fill between functions (int3 / nop / zero runs)
#   crt_library                                  statically linked MSVC C runtime (OWNER-RULING-CRT-LIBRARY-20261108)
#   third_party                                  other statically linked library code that is not the game's
#   data                                         data the linker placed inside .text (tables, literals)
#   missing_game                                 game code with no queue record at all
COVERAGE_CATEGORIES = ("matched", "submitted", "claimed", "open", "parked", "not_function", "padding", "crt_library",
                       "third_party", "data", "missing_game")
NOT_GAME = ("padding", "crt_library", "third_party", "data", "not_function")


def _int(v):
    if isinstance(v, dict):
        v = v.get("bytes")
    if isinstance(v, str):
        v = int(v, 0)
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ValueError("coverage byte total is not a non-negative integer: %r" % (v,))
    return v


# the build host's measurement names some categories by what they hold; these are the same totals
ALIASES = {"crt_compiler_helper": "crt_library", "crt": "crt_library", "third_party_library": "third_party",
           "data_or_tables": "data", "game_function_missing": "missing_game", "padding_bytes": "padding"}


def _category(name):
    """'a_crt_compiler_helper' -> 'crt_library': drop a one-letter ordering prefix, then map aliases."""
    if len(name) > 2 and name[1] == "_" and name[0].isalpha() and name[2:] in set(COVERAGE_CATEGORIES) | set(ALIASES):
        name = name[2:]
    return ALIASES.get(name, name)


def load_coverage(cov):
    """Normalise a TEXT_COVERAGE.json document to {"bytes": {category: n}, "section_bytes": n, "range": [lo, hi] | None,
    "generated_utc": str | None, "has_queue": bool}. Totals may sit at the top level or under "bytes", "totals",
    "categories" and "queue_bytes" (queue states), each a number or {"bytes": n}; "padding_bytes" is padding. Only the
    totals are kept: per-run address lists in the file are dropped."""
    found = {}
    for group in [cov] + [cov[k] for k in ("bytes", "totals", "categories", "queue_bytes") if isinstance(cov.get(k), dict)]:
        for k, v in group.items():
            c = _category(k)
            if c in COVERAGE_CATEGORIES and (isinstance(v, (int, str)) and not isinstance(v, bool) or isinstance(v, dict) and "bytes" in v):
                if c in found and _int(v) != found[c]:
                    raise ValueError("coverage file gives %s twice with different totals" % c)
                found[c] = _int(v)
    if not found:
        raise ValueError("coverage file has none of the categories %s" % ", ".join(COVERAGE_CATEGORIES))
    out = {k: found.get(k, 0) for k in COVERAGE_CATEGORIES}
    sec = next((cov[k] for k in ("section", "text") if isinstance(cov.get(k), dict)), {})
    lo, hi = sec.get("start", cov.get("start")), sec.get("end", cov.get("end"))
    size = sec.get("bytes", sec.get("size", cov.get("section_bytes", cov.get("text_bytes"))))
    if size is None and lo is not None and hi is not None:
        size = _int(hi) - _int(lo)
    size = _int(size) if size is not None else sum(out.values())
    if sum(out.values()) != size:
        raise ValueError("coverage categories sum to %d B but the measured section is %d B" % (sum(out.values()), size))
    rng = ["0x%08X" % _int(lo), "0x%08X" % _int(hi)] if lo is not None and hi is not None else None
    when = str(cov.get("generated_utc") or cov.get("computed_utc") or cov.get("generated") or "") or None
    if when and len(when) == 8 and when.isdigit():
        when = "%s-%s-%s" % (when[:4], when[4:6], when[6:])
    return {"bytes": out, "section_bytes": size, "range": rng, "generated_utc": when,
            "has_queue": all(k in found for k in ("matched", "parked", "open"))}


def code_section(stats, coverage):
    """The home page's code-section bar: game-code states from the queue figures already in `stats` (so the bar agrees
    with the headline), then the bytes that are not game code from the host's coverage totals, placed last. Every
    segment is a share of stats["text_bytes"]; .text bytes past the measured section (alignment to the section's size
    in the image) count as padding."""
    total = stats["text_bytes"]
    st = lambda k: stats["states"].get(k, {}).get("bytes", 0)
    c = coverage["bytes"]
    if coverage["section_bytes"] > total:
        raise ValueError("coverage measured %d B, more than the %d B code section" % (coverage["section_bytes"], total))
    padding = c["padding"] + total - coverage["section_bytes"]
    library = c["crt_library"] + c["third_party"]
    other = c["data"] + c["not_function"]
    # The host counts each byte once (overlapping queue records are not double counted, as summed function lengths
    # are). When its matched total is the headline's, it measured this queue snapshot: use its split as is. Otherwise
    # take the game-code states from the queue and let never-tried be what is left.
    same = coverage.get("has_queue") and c["matched"] == stats["matched_bytes"]
    if same:
        game = [("matched", "Verified", c["matched"]), ("pending", "Waiting for the build host", c["claimed"] + c["submitted"]),
                ("parked", "Tried, still open", c["parked"])]
        never = c["open"] + c["missing_game"]
    else:
        game = [("matched", "Verified", stats["matched_bytes"]),
                ("pending", "Waiting for the build host", st("claimed") + st("submitted") + st("lead_counted")),
                ("parked", "Tried, still open", st("parked"))]
        never = total - sum(b for _, _, b in game) - padding - library - other
    segs = [{"key": k, "label": l, "bytes": b, "game": True} for k, l, b in game]
    segs.append({"key": "never", "label": "Never tried", "bytes": max(never, 0), "game": True})
    segs.append({"key": "padding", "label": "Not game code: padding", "bytes": padding, "game": False})
    segs.append({"key": "library", "label": "Not game code: compiler runtime library" if not c["third_party"]
                 else "Not game code: runtime and other libraries", "bytes": library, "game": False})
    if other:
        segs.append({"key": "other", "label": "Not game code: data and fragments", "bytes": other, "game": False})
    return {
        "bytes": total,
        "measured_bytes": coverage["section_bytes"],
        "range": coverage["range"],
        "coverage_generated_utc": coverage["generated_utc"],
        "split": "coverage" if same else "queue",
        "segments": segs,
        # the host's own count of never-tried game code (open + no queue record), for checking the remainder above
        "never_tried_measured": c["open"] + c["missing_game"],
    }


# ---- linked executable ---------------------------------------------------------------------------------------------
# tools/relink.py in the matching repo links every verified function, the C runtime and import thunks into a whole
# executable and compares its .text with the original byte for byte, writing docs/RELINK_COVERAGE.json. The site keeps
# only its per-class byte counts (text_verify.classes: {class: {equal, different}}); the file's address lists
# (first_differing, per-library examples, problem examples) never leave the loader. Classes, mutually exclusive:
#   matched       verified functions, placed from their matched objects
#   submitted     functions submitted and waiting for the build host
#   pending       open or parked functions (not verified, so the link has no code for them)
#   not_function  queue records confirmed not to be function starts (fragments, data)
#   thunk         import thunks (jmp [IAT])
#   crt           the statically linked C runtime, taken from the library by identity
#   padding       alignment fill between functions
#   other         anything else
RELINK_CLASSES = (("matched", "Verified functions"), ("submitted", "Submitted, waiting for the build host"),
                  ("pending", "Open or parked functions"), ("not_function", "Fragments and data in the queue"),
                  ("thunk", "Import thunks"), ("crt", "C runtime library"), ("padding", "Padding"), ("other", "Other"))

# From milestone 6 the report also measures the whole file (built_vs_copied: "file" and per-section "sections"), every
# byte of the linked image against the original, by where the byte came from. Classes, mutually exclusive:
#   source       compiled from the project's own C++ (verified functions and the data they define)
#   library      the statically linked C runtime, from the library by identity
#   linker       what link.exe generates itself (headers, import and relocation tables, resources)
#   padding      alignment fill
#   copied       the game's own data tables and strings, copied from the original until they are reconstructed
#   placeholder  stand-ins for functions not yet verified (open, parked or submitted)
#   signature    the Authenticode signature, which no build can reproduce
# "built" = source + library + linker + padding (everything the build itself produces); "identical" adds copied.
FILE_CLASSES = (("source", "Compiled from the project's source"), ("library", "C runtime library"),
                ("linker", "Linker-generated headers and tables"), ("padding", "Padding"),
                ("copied", "Game data tables and strings, copied"), ("placeholder", "Placeholders for unverified functions"),
                ("signature", "Authenticode signature"))
BUILT_CLASSES = ("source", "library", "linker", "padding")
SECTION_ORDER = (".text", ".rdata", ".data", ".rsrc", ".reloc", "headers")


def _file_classes(by_class):
    if not isinstance(by_class, dict) or not by_class:
        raise ValueError("whole-file relink report has no by_class")
    rows, known = [], dict(FILE_CLASSES)
    for key, label in FILE_CLASSES:
        if key in by_class:
            rows.append((key, label, by_class[key]))
    for key, v in by_class.items():
        if key not in known:
            rows.append((key, key.replace("_", " ").capitalize(), v))
    out = []
    for key, label, v in rows:
        if not isinstance(v, dict) or "equal" not in v or "different" not in v:
            raise ValueError("whole-file relink class %s is not {equal, different}: %r" % (key, v))
        out.append({"key": key, "label": label, "equal": _int(v["equal"]), "different": _int(v["different"])})
    return out


def load_relink_file(bvc):
    """Normalise built_vs_copied (the whole-file measurement) to counts only: {"bytes", "built_bytes", "copied_bytes",
    "identical_bytes", "placeholder_bytes", "signature_bytes", "pct_built", "pct_identical", "classes": [...],
    "sections": [{"name", "bytes", "built", "copied", "identical"}, ...]}. Returns None when the document has none."""
    if not isinstance(bvc, dict) or not isinstance(bvc.get("file"), dict):
        return None
    f = bvc["file"]
    classes = _file_classes(f.get("by_class"))
    total = _int(f.get("bytes", sum(c["equal"] + c["different"] for c in classes)))
    if sum(c["equal"] + c["different"] for c in classes) != total:
        raise ValueError("whole-file relink classes sum to %d B but the file is %d B" % (
            sum(c["equal"] + c["different"] for c in classes), total))
    eq = {c["key"]: c["equal"] for c in classes}
    built = _int(f.get("identical_built", sum(eq.get(k, 0) for k in BUILT_CLASSES)))
    copied = _int(f.get("copied_bytes", eq.get("copied", 0)))
    identical = _int(f.get("identical_including_copied", built + copied))
    if not 0 <= built <= identical <= total:
        raise ValueError("whole-file relink totals are inconsistent: built %d, identical %d, file %d" % (built, identical, total))
    sections = []
    secs = bvc.get("sections") if isinstance(bvc.get("sections"), dict) else {}
    for name in list(SECTION_ORDER) + sorted(k for k in secs if k not in SECTION_ORDER):
        s = secs.get(name)
        if not isinstance(s, dict):
            continue
        sections.append({"name": str(name), "bytes": _int(s.get("bytes", 0)), "built": _int(s.get("identical_built", 0)),
                         "copied": _int(s.get("copied_bytes", 0)), "identical": _int(s.get("identical_including_copied", 0))})
    if sum(s["bytes"] for s in sections) > total:
        raise ValueError("whole-file relink sections sum to more than the file")
    sig = next((c["equal"] + c["different"] for c in classes if c["key"] == "signature"), 0)
    placeholder = next((c["equal"] + c["different"] for c in classes if c["key"] == "placeholder"), 0)
    return {
        "bytes": total,
        "built_bytes": built,
        "copied_bytes": copied,
        "identical_bytes": identical,
        "placeholder_bytes": placeholder,
        "signature_bytes": sig,
        "pct_built": round(100.0 * built / total, 2),
        "pct_identical": round(100.0 * identical / total, 2),
        "classes": classes,
        "sections": sections,
    }


def load_relink(doc, generated_utc=None, commit=None):
    """Normalise a RELINK_COVERAGE.json document to counts only: {"milestone": n | None, "classes": [{"key", "label",
    "equal", "different"}, ...], "identical_bytes": n, "bytes": n, "pct": float, "generated_utc": str | None,
    "commit": str | None, "sections_equal": bool | None, "entry_equal": bool | None, "iat_equal": bool | None}.
    The classes may sit under text_verify.classes (the relink tool's layout) or at the top level as "classes".
    `generated_utc` and `commit` are the file's own date and commit when the caller knows them (the document carries
    neither); fields of the same name in the document are used when the caller gives none. "file" is the whole-file
    measurement (load_relink_file) when the document carries built_vs_copied, else None."""
    tv = doc.get("text_verify") if isinstance(doc.get("text_verify"), dict) else {}
    classes = tv.get("classes") if isinstance(tv.get("classes"), dict) else doc.get("classes")
    if not isinstance(classes, dict) or not classes:
        raise ValueError("relink file has no text_verify.classes")
    rows, known = [], dict(RELINK_CLASSES)
    for key, label in RELINK_CLASSES:
        if key in classes:
            rows.append((key, label, classes[key]))
    for key, v in classes.items():
        if key not in known:
            rows.append((key, key.replace("_", " ").capitalize(), v))
    out = []
    for key, label, v in rows:
        if not isinstance(v, dict) or "equal" not in v or "different" not in v:
            raise ValueError("relink class %s is not {equal, different}: %r" % (key, v))
        out.append({"key": key, "label": label, "equal": _int(v["equal"]), "different": _int(v["different"])})
    total = sum(r["equal"] + r["different"] for r in out)
    if total == 0:
        raise ValueError("relink classes count no bytes")
    text = tv.get("text") if isinstance(tv.get("text"), dict) else {}
    rng = text.get("original")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        size = _int(rng[1]) - _int(rng[0])
        if size != total:
            raise ValueError("relink classes sum to %d B but the original .text is %d B" % (total, size))
    identical = sum(r["equal"] for r in out)
    when = generated_utc or doc.get("generated_utc") or doc.get("computed_utc") or None
    when = str(when) if when else None
    if when and len(when) == 8 and when.isdigit():
        when = "%s-%s-%s" % (when[:4], when[4:6], when[6:])
    entry = tv.get("entry") if isinstance(tv.get("entry"), dict) else {}
    flag = lambda v: v if isinstance(v, bool) else None
    milestone = doc.get("milestone")
    return {
        "milestone": milestone if isinstance(milestone, int) and not isinstance(milestone, bool) else None,
        "classes": out,
        "identical_bytes": identical,
        "bytes": total,
        "pct": round(100.0 * identical / total, 2),
        "generated_utc": when,
        "commit": str(commit or doc.get("commit") or "") or None,
        "sections_equal": flag(tv.get("sections_equal")),
        "entry_equal": flag(entry.get("equal")),
        "iat_equal": flag(tv.get("iat_equal")),
        "file": load_relink_file(doc.get("built_vs_copied")),
    }


def relink_file_block(f):
    """The whole-file figure: built from source / identical but copied / not yet, each a share of the file, and what
    the "not yet" bytes are: placeholders for unverified functions, linker and library bytes that still differ, and
    the signature no build can reproduce. Counts only."""
    if not f:
        return None
    diff = {c["key"]: c["different"] for c in f["classes"]}
    rest = f["bytes"] - f["identical_bytes"]
    linker = diff.get("linker", 0) + diff.get("library", 0)
    signature = f["signature_bytes"]
    placeholder = f["placeholder_bytes"]
    other = rest - placeholder - linker - signature
    remaining = [{"key": "placeholder", "label": "Placeholders for parked and unverified functions", "bytes": placeholder},
                 {"key": "linker", "label": "Linker and library bytes that still differ", "bytes": linker},
                 {"key": "signature", "label": "Authenticode signature, never reproducible", "bytes": signature}]
    if other:
        remaining.append({"key": "other", "label": "Other", "bytes": other})
    return {
        "bytes": f["bytes"],
        "built_bytes": f["built_bytes"],
        "copied_bytes": f["copied_bytes"],
        "identical_bytes": f["identical_bytes"],
        "pct_built": f["pct_built"],
        "pct_identical": f["pct_identical"],
        "pct_signature": round(100.0 * signature / f["bytes"], 2),
        "segments": [{"key": "built", "label": "Built from source", "bytes": f["built_bytes"]},
                     {"key": "copied", "label": "Identical, copied data", "bytes": f["copied_bytes"]},
                     {"key": "rest", "label": "Not yet", "bytes": rest}],
        "remaining": remaining,
        "classes": f["classes"],
        "sections": f["sections"],
    }


def relink_block(stats, relink):
    """The home page's "linked executable" figure: the relink's identical bytes as a share of stats["text_bytes"], the
    per-class split, and the bytes the headline does not cover grouped the way the page explains them: unverified
    functions (open, parked, submitted), verified functions the linker cannot place yet, library and other addresses
    (the C runtime's relocations to library data, thunks, fragments) and padding. Counts only. "file" is the
    whole-file figure (relink_file_block) when the report measured it, else None."""
    total = stats["text_bytes"]
    if relink["bytes"] != total:
        raise ValueError("relink measured %d B but the code section is %d B" % (relink["bytes"], total))
    diff = {r["key"]: r["different"] for r in relink["classes"]}
    unverified = diff.get("submitted", 0) + diff.get("pending", 0)
    unplaced = diff.get("matched", 0)  # verified functions the linker could not place yet (RELINK_STATUS.md, Blockers)
    padding = diff.get("padding", 0)
    library = sum(v for k, v in diff.items() if k not in ("submitted", "pending", "matched", "padding"))
    return {
        "pct_text": round(100.0 * relink["identical_bytes"] / total, 2),
        "identical_bytes": relink["identical_bytes"],
        "bytes": total,
        "milestone": relink["milestone"],
        "classes": relink["classes"],
        "remaining": [{"key": "unverified", "label": "Unverified functions (open, parked or submitted)", "bytes": unverified},
                      {"key": "unplaced", "label": "Verified functions the linker cannot place yet", "bytes": unplaced},
                      {"key": "library", "label": "Library data addresses, thunks and fragments", "bytes": library},
                      {"key": "padding", "label": "Padding", "bytes": padding}],
        "generated_utc": relink["generated_utc"],
        "commit": relink["commit"],
        "sections_equal": relink["sections_equal"],
        "entry_equal": relink["entry_equal"],
        "iat_equal": relink["iat_equal"],
        "file": relink_file_block(relink.get("file")),
    }
