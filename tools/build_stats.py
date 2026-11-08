#!/usr/bin/env python3
"""Build public/data/stats.json for the progress site from a checkout of the matching repo.

usage: build_stats.py <repo> <out.json>

Reads only what the repo already publishes: docs/grid-data.json (one row per function: address, length, module,
state, who), queue/matched/*.json (the model that produced each match) and the git history of queue/matched/ (when
each function first became matched). Publishes counts and sizes only: no names, no code, no game bytes.
"""
import collections, datetime, glob, json, os, subprocess, sys
from site_stats import normalize_stats

TEXT_BYTES = None  # read from tools/board.py so the site and the board agree


def text_size(repo):
    for line in open(os.path.join(repo, "tools", "board.py")):
        if line.strip().startswith("TEXT") and "=" in line:
            return int(eval(line.split("=", 1)[1].split("#")[0]))
    raise SystemExit("TEXT not found in tools/board.py")


def main(repo, out):
    text = text_size(repo)
    grid = json.load(open(os.path.join(repo, "docs", "grid-data.json")))
    length = {r["va"].upper(): r["length"] for r in grid}
    verified = {"verified", "verified_raw"}

    # ---- totals ----
    by_state = collections.Counter()
    bytes_by_state = collections.Counter()
    for r in grid:
        s = "matched" if r["state"] in verified else r["state"]
        by_state[s] += 1
        bytes_by_state[s] += r["length"]
    matched_bytes = bytes_by_state["matched"]

    # ---- per module ----
    group = {"verified": "matched", "verified_raw": "matched", "claimed": "review", "submitted": "review",
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
        model = d.get("model") or "unknown"
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
    normalize_stats(stats)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(stats, open(out, "w"), indent=1)
    print("matched %d fn, %d B = %.2f%% of .text; %d trend days; %d contributors; %d models" % (
        stats["matched_functions"], matched_bytes, stats["matched_pct_text"], len(trend), len(stats["leaderboard"]),
        len(stats["by_model"])))


if __name__ == "__main__":
    main(*sys.argv[1:3])
