#!/usr/bin/env python3
"""Build public/data/stats.json for the progress site from a checkout of the matching repo.

usage: build_stats.py <repo> <out.json> [--coverage TEXT_COVERAGE.json] [--relink RELINK_COVERAGE.json]
                      [--names FILE-OR-URL | --no-names]

Reads only what the repo already publishes: docs/grid-data.json (one row per function: address, length, module,
state, who), queue/matched/*.json (the model that produced each match) and the git history of queue/matched/ (when
each function first became matched). Publishes counts and sizes only: no function names, no code, no game bytes;
contributors appear by handle, and site contributors by GitHub login only when they opted in (below).

The code-section bar also needs the bytes that are not game code (padding between functions, the statically linked
C runtime, data in .text). Only the build host can measure those, because it alone holds the image: it writes
per-category byte totals to TEXT_COVERAGE.json (format in tools/site_stats.py). Pass that file with --coverage, or set
IBLIS_TEXT_COVERAGE. Only its totals reach stats.json. Without it the bar has no "not game code" segments and the
page falls back to a single "Never tried or not yet queued" remainder.

The "linked executable" figure comes from the matching repo's docs/RELINK_COVERAGE.json (tools/relink.py links every
verified function, the C runtime and the import thunks into a whole image and compares its .text with the original).
Pass it with --relink, or set IBLIS_RELINK_COVERAGE; by default the repo's own copy is used when it exists. Only its
per-class equal/different byte counts reach stats.json (as "relink"); the file's address lists do not. From milestone 6
the report also measures the whole file (built from source, identical but copied game data, not yet); those counts go
in as "relink.file" and give the headline of the "Linked executable" slab.

The board credits site contributors, whom the matching repo records as iblis-<github id>, by GitHub login only when they
turned on "Show my GitHub name on the board". That opt-in lives in the service, so the build reads the service's login
map, GET <site>/api/v1/names, which lists opted-in accounts and nobody else. --names takes a URL or a saved copy of that
response (a build host without network access fetches it elsewhere and passes the file); the default is
$IBLIS_NAMES_URL, else <site>/api/v1/names for the site address build_site.py publishes. If the map cannot be read, every
iblis-<id> row is "Anonymous", as before the map existed: the build never fails over it and never guesses a name.
Neither github ids nor any login outside the map reach stats.json.
"""
import argparse, collections, datetime, glob, json, os, subprocess, sys, urllib.request
from site_stats import code_section, load_coverage, load_relink, normalize_stats, parse_names, relink_block

NAMES_MAX_BYTES = 1 << 20


def default_names_url():
    """$IBLIS_NAMES_URL, else /api/v1/names on the address build_site.py publishes (IBLIS_SITE_URL / wrangler.toml)."""
    if os.environ.get("IBLIS_NAMES_URL"):
        return os.environ["IBLIS_NAMES_URL"]
    from build_site import site_url
    return site_url() + "/api/v1/names"


def load_names(source, timeout=15):
    """{github id: login} for opted-in contributors from a URL or a file; {} (everyone anonymous) when it cannot be read.
    Prints only how many logins it found, never the logins or ids."""
    if not source:
        return {}
    try:
        if source.startswith(("https://", "http://")):
            req = urllib.request.Request(source, headers={"accept": "application/json", "user-agent": "iblis-build-stats"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(NAMES_MAX_BYTES + 1)
        else:
            with open(source, "rb") as f:
                raw = f.read(NAMES_MAX_BYTES + 1)
        if len(raw) > NAMES_MAX_BYTES:
            raise ValueError("larger than %d bytes" % NAMES_MAX_BYTES)
        names = parse_names(json.loads(raw.decode("utf-8")))
    except Exception as e:  # network, file or format: fall back to the anonymous board, never fail the build
        print("warning: board names not read from %s (%s); site contributors stay Anonymous" % (source, type(e).__name__),
              file=sys.stderr)
        return {}
    print("board names: %d opted-in contributor%s from %s" % (len(names), "" if len(names) == 1 else "s", source))
    return names

TEXT_BYTES = None  # read from tools/board.py so the site and the board agree


def text_size(repo):
    for line in open(os.path.join(repo, "tools", "board.py")):
        if line.strip().startswith("TEXT") and "=" in line:
            return int(eval(line.split("=", 1)[1].split("#")[0]))
    raise SystemExit("TEXT not found in tools/board.py")


def file_provenance(repo, path, tracked=None):
    """(date, short commit) of a file: its last commit when it is tracked in `repo`, or when it is a byte-for-byte copy
    of the tracked file `tracked` (a coverage file copied out of the repo for the host), else its modification date."""
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(repo))
        if rel.startswith("..") and tracked and os.path.exists(os.path.join(repo, tracked)) and \
                open(path, "rb").read() == open(os.path.join(repo, tracked), "rb").read():
            rel = tracked
        if not rel.startswith(".."):
            r = subprocess.run(["git", "-C", repo, "log", "-1", "--format=%cs %h", "--", rel],
                               capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                day, commit = r.stdout.split()[:2]
                return day, commit
    except (OSError, ValueError):
        pass
    return datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc).strftime("%Y-%m-%d"), None


def main(repo, out, coverage=None, relink=None, names=None):
    text = text_size(repo)
    grid = json.load(open(os.path.join(repo, "docs", "grid-data.json")))
    length = {r["va"].upper(): r["length"] for r in grid}
    # "steered" (Diablo2-Native's grid, 2026-11-16): verifier-confirmed through a steering form admitted by a ruling for
    # that one function; it is verified, counted in every verified total, and also reported on its own (states.steered)
    verified = {"verified", "verified_raw", "steered"}

    # ---- totals ----
    by_state = collections.Counter()
    bytes_by_state = collections.Counter()
    for r in grid:
        s = "matched" if r["state"] in verified else r["state"]
        by_state[s] += 1
        bytes_by_state[s] += r["length"]
        if r["state"] == "steered":
            by_state["steered"] += 1
            bytes_by_state["steered"] += r["length"]
    matched_bytes = bytes_by_state["matched"]

    # ---- per module ----
    group = {"verified": "matched", "verified_raw": "matched", "steered": "matched", "claimed": "review", "submitted": "review",
             "lead_counted": "review", "open": "open", "parked": "parked"}
    mods = collections.defaultdict(lambda: {"functions": 0, "bytes": 0, "matched_functions": 0, "matched_bytes": 0,
                                            "review_bytes": 0, "open_bytes": 0, "parked_bytes": 0})
    for r in grid:
        m = mods[r["module"] if r["module"] != "?" else "Unattributed"]
        m["functions"] += 1
        m["bytes"] += r["length"]
        g = group.get(r["state"], "open")
        m[g + "_bytes"] += r["length"]
        if g == "matched":
            m["matched_functions"] += 1
    modules = sorted(({"name": k, **v} for k, v in mods.items()), key=lambda x: -x["bytes"])

    # ---- leaderboard (verified matches only) ----
    lb = collections.defaultdict(lambda: {"functions": 0, "bytes": 0})
    for r in grid:
        if r["state"] in verified:
            who = r.get("who") or "unattributed"
            lb[who]["functions"] += 1
            lb[who]["bytes"] += r["length"]
    leaderboard = sorted(({"who": k, **v} for k, v in lb.items()), key=lambda x: -x["bytes"])

    # ---- by model ----
    bm = collections.defaultdict(lambda: {"functions": 0, "bytes": 0})
    for f in glob.glob(os.path.join(repo, "queue", "matched", "*.json")):
        d = json.load(open(f))
        model = str(d.get("model") or "unknown").strip().lower()  # "Codex" and "codex" are one model
        bm[model]["functions"] += 1
        bm[model]["bytes"] += d.get("length", 0)
    by_model = sorted(({"model": k, **v} for k, v in bm.items()), key=lambda x: -x["bytes"])

    # ---- daily trend: first day each address appeared in queue/matched/ ----
    log = subprocess.run(
        ["git", "-C", repo, "log", "--reverse", "--no-renames", "--diff-filter=A", "--name-only",
         "--format=@%ad", "--date=short", "origin/main", "--", "queue/matched/"],
        capture_output=True, text=True, check=True).stdout
    first = {}
    day = None
    for line in log.splitlines():
        if line.startswith("@"):
            day = line[1:]
        elif line.startswith("queue/matched/") and line.endswith(".json"):
            va = "0X" + os.path.basename(line)[:-5].upper().replace("0X", "")
            first.setdefault(va, day)
    per_day = collections.defaultdict(lambda: [0, 0])
    for va, d in first.items():
        per_day[d][0] += 1
        per_day[d][1] += length.get(va, 0)
    trend, cf, cb = [], 0, 0
    for d in sorted(per_day):
        cf += per_day[d][0]
        cb += per_day[d][1]
        trend.append({"day": d, "functions": per_day[d][0], "bytes": per_day[d][1],
                      "cum_functions": cf, "cum_bytes": cb})

    # ---- size map of what is left ----
    # the same size bands the service hands work out in (worker/index.js BANDS)
    buckets = [("tiny", 0, 64), ("small", 64, 256), ("medium", 256, 512), ("large", 512, 1024), ("huge", 1024, 1 << 30)]
    left = []
    for band, lo, hi in buckets:
        rows = [r for r in grid if r["state"] not in verified and lo <= r["length"] < hi]
        tried = [r for r in rows if r["state"] in ("parked",)]
        left.append({"band": band, "range": [lo, hi if hi < (1 << 30) else None], "functions": len(rows),
                     "bytes": sum(r["length"] for r in rows), "tried": len(tried)})

    head = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "origin/main"],
                          capture_output=True, text=True).stdout.strip()
    stats = {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "commit": head,
        "text_bytes": text,
        "functions_total": len(grid),
        "bytes_total": sum(r["length"] for r in grid),
        "matched_functions": by_state["matched"],
        "matched_bytes": matched_bytes,
        "matched_pct_text": round(100.0 * matched_bytes / text, 2),
        "states": {k: {"functions": by_state[k], "bytes": bytes_by_state[k]} for k in by_state},
        "certified": 0,
        "modules": modules,
        "leaderboard": leaderboard,
        "by_model": by_model,
        "trend": trend,
        "left_by_size": left,
    }
    if coverage:
        stats["code_section"] = cs = code_section(stats, load_coverage(json.load(open(coverage))))
        never = next(s["bytes"] for s in cs["segments"] if s["key"] == "never")
        if abs(never - cs["never_tried_measured"]) > max(4096, text // 1000):
            print("warning: never-tried remainder %d B differs from the host's measured %d B (queue moved since the "
                  "coverage run? regenerate it)" % (never, cs["never_tried_measured"]), file=sys.stderr)
        print("code section: " + "; ".join("%s %.2f%%" % (s["label"], 100.0 * s["bytes"] / text) for s in cs["segments"]))
    else:
        print("warning: no coverage file; the bar cannot separate padding and library code from never-tried code",
              file=sys.stderr)
    if relink is None and os.path.exists(os.path.join(repo, "docs", "RELINK_COVERAGE.json")):
        relink = os.path.join(repo, "docs", "RELINK_COVERAGE.json")
    if relink:
        day, commit = file_provenance(repo, relink, os.path.join("docs", "RELINK_COVERAGE.json"))
        stats["relink"] = rl = relink_block(stats, load_relink(json.load(open(relink)), day, commit))
        print("linked executable: %.2f%% of .text identical (%s B of %s B; milestone %s, %s%s)" % (
            rl["pct_text"], rl["identical_bytes"], rl["bytes"], rl["milestone"], rl["generated_utc"],
            " @ " + rl["commit"] if rl["commit"] else ""))
        if rl.get("file"):
            f = rl["file"]
            print("whole file: %.2f%% built from source, %.2f%% identical including copied data (%s of %s B; copied %s B; "
                  "signature %s B)" % (f["pct_built"], f["pct_identical"], f["identical_bytes"], f["bytes"], f["copied_bytes"],
                                       f["remaining"][2]["bytes"] if len(f["remaining"]) > 2 else 0))
    else:
        print("warning: no relink coverage file; the page shows no linked-executable figure", file=sys.stderr)
    normalize_stats(stats, names or {})
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(stats, open(out, "w"), indent=1)
    print("matched %d fn, %d B = %.2f%% of .text; %d trend days; %d contributors; %d models" % (
        stats["matched_functions"], matched_bytes, stats["matched_pct_text"], len(trend), len(stats["leaderboard"]),
        len(stats["by_model"])))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the site's stats.json from the matching repo.")
    ap.add_argument("repo")
    ap.add_argument("out")
    ap.add_argument("--coverage", default=os.environ.get("IBLIS_TEXT_COVERAGE"),
                    help="TEXT_COVERAGE.json from the build host (default $IBLIS_TEXT_COVERAGE)")
    ap.add_argument("--relink", default=os.environ.get("IBLIS_RELINK_COVERAGE"),
                    help="RELINK_COVERAGE.json from the matching repo's tools/relink.py (default $IBLIS_RELINK_COVERAGE, "
                         "else <repo>/docs/RELINK_COVERAGE.json when present)")
    ap.add_argument("--names", default=None,
                    help="opted-in login map: a URL or a saved copy of GET <site>/api/v1/names (default $IBLIS_NAMES_URL, "
                         "else the published site's /api/v1/names)")
    ap.add_argument("--no-names", action="store_true", help="do not read the login map: every site contributor is Anonymous")
    a = ap.parse_args()
    main(a.repo, a.out, a.coverage, a.relink, {} if a.no_names else load_names(a.names or default_names_url()))
