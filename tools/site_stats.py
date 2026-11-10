"""Apply the public site's contributor and model attribution to generated counts."""
from collections import defaultdict


def normalize_stats(stats):
    # iblis-120912283 is the owner's own GitHub account (Regubreaku). Other site contributors are imported as
    # iblis-<github id> and stay anonymous on the board until a login map for opted-in contributors exists.
    aliases = {'lead': 'Legbreak', 'leg': 'Legbreak', 'fable': 'Legbreak', 'iblis-120912283': 'Legbreak',
               'legbreak': 'Legbreak', 'codex': 'Nathan', 'nathan': 'Nathan'}
    for field, key in (('leaderboard', 'who'), ('by_model', 'model')):
        grouped = defaultdict(lambda: {'functions': 0, 'bytes': 0})
        for row in stats[field]:
            name = row[key]
            if key == 'who':
                name = aliases.get(name.casefold()) or ('Anonymous' if name.casefold().startswith('iblis-') else name[:1].upper() + name[1:])
            elif name == 'lead':
                name = 'claude-opus-5-5'
            grouped[name]['functions'] += row['functions']
            grouped[name]['bytes'] += row['bytes']
        stats[field] = sorted(({key: name, **counts} for name, counts in grouped.items()),
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
