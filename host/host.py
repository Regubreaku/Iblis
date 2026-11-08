#!/usr/bin/env python3
"""Project Iblis controller. Public compilation requires the isolated Docker runner (host/README.md).

  python host/host.py sync      --repo PATH [--redivivus PATH] [--examples N]
                                                      push the queue (state, length, module, name, flags, closest
                                                      parked source; a Redivivus pointer; N verified examples each)
  python host/host.py verify    --repo PATH [--once]  poll pending submissions, recompile each, post verdicts
  python host/host.py integrate --repo PATH           write the day's verified sources into the repository (nightly)
  python host/host.py review list|recheck|adjudicate --repo PATH [--id N] [--verdict failed --note TEXT]
  python host/host.py flags     --repo PATH VA        print the compile flags for one function

Environment: IBLIS_RUNNER_CONFIG, IBLIS_HOST_KEY, IBLIS_URL (default https://iblis.captainlegbreak.workers.dev).
Verdicts: verified = the host's compile matches; failed = the submission cannot be right as sent (forbidden construct,
no marker, does not compile, extra forms); review = it compiles cleanly but the host sees different bytes (not a strike).
"""
import glob, hashlib, json, os, re, shutil, subprocess, sys, time, urllib.request
from pathlib import Path
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolated_runner import DockerRunner, RunnerError

HERE = os.path.dirname(os.path.abspath(__file__))

URL = os.environ.get("IBLIS_URL", "https://iblis.captainlegbreak.workers.dev").rstrip("/")
KEY = os.environ.get("IBLIS_HOST_KEY", "")
EXCLUDE_STATES = {"not_function"}
OPT_FLAGS = ("/Oy-", "/Ob1", "/Ob2", "/Oy", "/O1", "/O2", "/Os", "/Ot")


def api(method, path, body=None):
    req = urllib.request.Request(URL + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"authorization": "Bearer " + KEY, "content-type": "application/json",
                                          "user-agent": "iblis-host"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def norm(va):
    return "0x%08X" % int(str(va), 16)


class Repo:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.cfg = json.load(open(os.path.join(self.root, "config.json")))
        self.rulings = []
        for p in sorted(glob.glob(os.path.join(self.root, "docs", "rulings", "*.json"))):
            try:
                self.rulings.append((os.path.basename(p), json.load(open(p))))
            except Exception:
                pass
        self.names = {norm(k): v for k, v in json.load(open(os.path.join(self.root, "names.json"))).items()}
        lib = next((r for _, r in self.rulings if r.get("id") == "OWNER-RULING-CRT-LIBRARY-20261108"), None)
        self.library = (int(lib["range"][0], 16), int(lib["range"][1], 16)) if lib else None
        self.no_body = {norm(v) for v in self.cfg["rules"].get("no_body", []) if re.match(r"^0[xX][0-9A-Fa-f]+$", str(v))}

    def flags(self, va):
        """The profile plus every flag ruling naming this address, in file-name order (the order match.py uses)."""
        f = list(self.cfg["profile"]["flags"])
        for _, r in self.rulings:
            if r.get("rule") == "flag" and va in {norm(x) for x in r.get("vas", [])} and (not f or f[-1] != r.get("flag")):
                if r["flag"] in f and r["flag"] not in OPT_FLAGS:
                    continue
                f.append(r["flag"])
        return f

    def closest_source(self, state, va):
        """A parked or reopened function's last candidate.cpp, if the service would accept it as text."""
        p = os.path.join(self.root, "queue", state, va[2:], "candidate.cpp")
        try:
            src = open(p, encoding="latin-1").read()
        except OSError:
            return None
        return src if service_text_ok(src) and len(src) <= 60000 else None

    def queue(self, redivivus=None, examples=0):
        refs = redivivus_refs(redivivus) if redivivus else {}
        pool = example_pool(self.root) if examples else []
        out = []
        for state in ("open", "claimed", "submitted", "parked", "matched"):
            for p in glob.glob(os.path.join(self.root, "queue", state, "*.json")):
                it = json.load(open(p))
                va = norm(it["va"]); a = int(va, 16)
                if state == "matched":
                    st = "matched"
                elif self.library and self.library[0] <= a <= self.library[1] or va in self.no_body:
                    st = "excluded"
                else:
                    st = "open"
                f = dict(va=va, length=it["length"], module=it.get("module", "?"), state=st,
                         name=self.names.get(va), flags=" ".join(self.flags(va)), body_sha256=it.get("body_sha256"),
                         note=it.get("previous_park_reason") or it.get("last_park_reason") or it.get("park_reason"))
                if st == "open":
                    f["best_source"] = self.closest_source(state, va)
                    if refs.get(a):
                        f["reference"] = refs[a]
                    if pool:
                        ex = pick_examples(pool, va, it["length"], f["module"], f["best_source"], examples)
                        if ex:
                            f["context"] = dict(examples=ex)
                out.append(f)
        return out


MARKER = re.compile(r"//\s*(?:0x|.*?\bat\s+0x)([0-9A-Fa-f]{8})\b")


def service_text_ok(s):
    """The service's own text rules (worker checkText), so the host never sends what a contributor could not."""
    if "\0" in s or re.search(r"[A-Za-z0-9+/=]{400,}", s) or re.search(r"(?:\b[0-9A-Fa-f]{2}\b[ ,]+){48,}", s):
        return False
    if re.search(r"\b(?:push|mov|lea|call|jmp|ret)\s+(?:e[a-d]x|e[sd]i|e[sb]p|dword|byte|0x)", s, re.I) and \
            len(re.findall(r"\n\s*[0-9A-F]{6,8}[: ]", s, re.I)) > 8:
        return False
    return not re.search(r"__asm|\b_emit\b|__declspec\s*\(\s*naked|#pragma\s+(?:optimize|code_seg)", s)


def redivivus_refs(root):
    """Address -> 'file (function)' from a Redivivus checkout's Source/**/*.map files. Words only."""
    out = {}
    for m in glob.glob(os.path.join(root, "Source", "**", "*.map"), recursive=True):
        try:
            d = json.load(open(m, encoding="utf-8"))
        except Exception:
            continue
        rel = (d.get("file") or os.path.relpath(m, root)[:-4]).replace("\\", "/")
        for fn in d.get("functions", []):
            try:
                out[int(str(fn.get("address")), 16)] = "Redivivus %s (%s)" % (rel, fn.get("name", "?"))
            except (TypeError, ValueError):
                pass
    return out


def example_pool(root):
    """Verified single-function sources (contributor submissions) that can serve as examples."""
    pool = []
    for p in glob.glob(os.path.join(root, "queue", "matched", "*.json")):
        try:
            it = json.load(open(p))
            src = open(os.path.join(root, "queue", "matched", os.path.basename(p)[:-5], "candidate.cpp"), encoding="latin-1").read()
        except (OSError, ValueError):
            continue
        if len(src) > 12000 or not service_text_ok(src):
            continue
        pool.append(dict(va=norm(it["va"]), length=it["length"], module=it.get("module", "?"), source=src,
                         marks={int(x, 16) for x in MARKER.findall(src)}))
    return pool


def pick_examples(pool, va, length, module, source, n):
    """Most shared callees first, then same module, then nearest size; never the function itself."""
    import math
    marks = {int(x, 16) for x in MARKER.findall(source or "")} - {int(va, 16)}
    ranked = []
    for e in pool:
        if e["va"] == va or e["length"] > max(4 * length, 64):
            continue
        shared = len(marks & (e["marks"] - {int(e["va"], 16)}))
        ranked.append(((-shared, e["module"] != module, abs(math.log((e["length"] + 1) / (length + 1)))), shared, e))
    ranked.sort(key=lambda r: (r[0], r[2]["va"]))
    out = []
    for _, shared, e in ranked[:n]:
        why = ", ".join(x for x in (("shares %d declared callee(s)" % shared) if shared else "",
                                    "same module" if e["module"] == module else "", "%d B" % e["length"]) if x)
        out.append(dict(va=e["va"], length=e["length"], why=why, source=e["source"]))
    return out


def sync_batches(fs, limit=1_500_000, most=500):
    """Split records into requests under the service's 2 MiB body limit (sources and examples make records large)."""
    batch, size = [], 2
    for f in fs:
        n = len(json.dumps(f)) + 1
        if batch and (size + n > limit or len(batch) >= most):
            yield batch
            batch, size = [], 2
        batch.append(f); size += n
    if batch:
        yield batch


def cmd_sync(repo, redivivus=None, examples=0):
    fs = repo.queue(redivivus, examples)
    print("closest sources %d, references %d, with examples %d" % (
        sum(1 for f in fs if f.get("best_source")), sum(1 for f in fs if f.get("reference")), sum(1 for f in fs if f.get("context"))))
    done = 0
    for batch in sync_batches(fs):
        r = api("POST", "/api/v1/host/sync", {"functions": batch})
        done += r["synced"]
        print("synced", done, "of", len(fs))


def verdict_for(res):
    st = res.get("status", "INFRA_ERROR")
    if st in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL"):
        return "verified", "%s, body %s" % (st, res["compiled_body_sha256"][:16])
    if st == "COMPILE_ERROR":
        return "failed", "COMPILE_ERROR: compilation failed; reproduce locally for diagnostics"
    if st in ("FORBIDDEN", "NO_MARKER", "NO_SYMBOL", "MULTI_FORM", "INVALID_SOURCE", "SOURCE_ERROR"):
        return "failed", "%s: %s" % (st, (res.get("detail") or ", ".join(res.get("extra_forms", [])))[:600])
    return "review", "%s: owner review/recheck required; diagnostics retained privately" % st


def load_runner(cfg):
    path = os.environ.get("IBLIS_RUNNER_CONFIG")
    if not path:
        raise RunnerError("IBLIS_RUNNER_CONFIG is required; public source compilation is disabled")
    return DockerRunner(path, cfg["image"]["sha256"], cfg.get("toolchain", {}).get("cl_sha256"))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes((json.dumps(value, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))
    os.replace(temp, path)


def process_items(repo, runner, lengths, items):
    for incoming in items:
        p = dict(incoming)
        if isinstance(p.get("source"), str) and "\r" in p["source"]:
            p["original_source"] = p["source"]
            p["source"] = p["source"].replace("\r\n", "\n").replace("\r", "\n")
        try:
            va = norm(p["va"])
            rid = int(p["id"])
            if rid <= 0 or va not in lengths:
                raise ValueError("unknown submission address or id")
            source = p["source"]
            if not isinstance(source, str) or not source or len(source) > 262144:
                raise ValueError("source must be nonempty text under 262144 characters")
            source.encode("latin-1")
        except (ValueError, TypeError, KeyError, UnicodeError) as e:
            res = dict(status="INVALID_SOURCE", detail=str(e))
        else:
            try:
                res = runner.compile(va, lengths[va], repo.flags(va), source)
            except Exception as e:
                # Runner/transport/parser failures are infrastructure or ambiguous, never strikes.
                res = dict(status="INFRA_ERROR", detail="isolated runner: " + type(e).__name__)
        try:
            v, note = verdict_for(res)
            if v == "verified" and p.get("body_sha256") and p["body_sha256"] != res["compiled_body_sha256"]:
                v, note = "review", "matches here, but the submitted body hash differs from the host's"
            wd = Path(repo.root) / "work" / "host" / ("%s-r%d" % (norm(p["va"]), int(p["id"])))
            wd.mkdir(parents=True, exist_ok=True)
            obj = res.pop("object_bytes", None)
            if obj is not None:
                (wd / "candidate.obj").write_bytes(obj)
            # Save before delivery for audit, but integration requires an acknowledged service verdict.
            record = dict(p, host=res, verdict=v, delivered=False)
            save_json(wd / "verdict.json", record)
            api("POST", "/api/v1/host/verdict", {"id": p["id"], "verdict": v, "note": note})
            record["delivered"] = True
            save_json(wd / "verdict.json", record)
            print(time.strftime("%H:%M:%S"), p["va"], v, note[:100])
        except Exception as e:
            print("submission %s deferred: %s" % (p.get("id"), type(e).__name__), file=sys.stderr)


def cmd_verify(repo, once):
    runner = load_runner(repo.cfg)  # fail before fetching work when isolation is unconfigured
    lengths = {f["va"]: f["length"] for f in repo.queue()}
    while True:
        pend = api("GET", "/api/v1/host/pending?limit=20")["pending"]
        process_items(repo, runner, lengths, pend)
        if once:
            return
        time.sleep(60 if not pend else 1)


def cmd_review(repo, action, rid=None, decision=None, note=None):
    path = "/api/v1/host/review"
    if rid is not None:
        rid = int(rid)
        if rid <= 0: raise ValueError("review id must be positive")
        path += "?id=%d" % rid
    rows, cursor = [], 0
    while True:
        page = api("GET", path)
        rows.extend(page["review"])
        next_cursor = page.get("next_cursor")
        if rid is not None or next_cursor is None:
            break
        next_cursor = int(next_cursor)
        if next_cursor <= cursor:
            raise ValueError("review pagination did not advance")
        cursor = next_cursor
        path = "/api/v1/host/review?after=%d&limit=100" % cursor
    if action == "list":
        print(json.dumps(rows, indent=1))
        return
    selected = [p for p in rows if rid is None or int(p["id"]) == int(rid)]
    if action == "recheck":
        process_items(repo, load_runner(repo.cfg), {f["va"]: f["length"] for f in repo.queue()}, selected)
    elif action == "adjudicate":
        if rid is None or len(selected) != 1 or decision not in ("verified", "failed") or not note:
            raise ValueError("adjudicate requires a current review --id, --verdict verified|failed and --note")
        # Manual acceptance alone does not create importable evidence: recheck to generate that.
        api("POST", "/api/v1/host/verdict", {"id": int(rid), "verdict": decision, "note": "owner: " + note})
    else:
        raise ValueError("review action must be list, recheck or adjudicate")


@contextmanager
def import_lock(root):
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-common-dir"], capture_output=True, text=True)
    common = Path(result.stdout.strip()) if result.returncode == 0 else root / ".git"
    if not common.is_absolute():
        common = root / common
    common.mkdir(parents=True, exist_ok=True)
    lock = common / "d2match.lock"
    lock.mkdir()  # same lock as private tools; never steal an active/stale lock
    try:
        (lock / "owner").write_text("%d iblis-import" % os.getpid())
        yield
    finally:
        (lock / "owner").unlink()
        lock.rmdir()


def import_record(repo, vp, v):
    root = Path(repo.root)
    va = norm(v["va"])
    who = "iblis-%d" % int(v["github_id"])
    with import_lock(root):
        matches = [(p.parent.name, p) for p in (root / "queue").glob("*/*.json")
                   if p.name.split(".")[0].upper() == va[2:]]
        if len(matches) != 1 or matches[0][0] != "open":
            raise ValueError("address is not uniquely open; preserve existing contributor work")
        q = matches[0][1]
        item = json.loads(q.read_text(encoding="utf-8"))
        sd = root / "queue" / "submitted" / va[2:]
        dst = sd.parent / (va[2:] + "." + who + ".json")
        drop = root / "work" / "host" / "objects" / va[2:]
        if sd.exists() or dst.exists() or drop.exists():
            raise ValueError("submission or object drop exists; owner reconciliation required")
        res = dict(v["host"])
        src = v["source"].encode("latin-1")
        obj = (vp.parent / "candidate.obj").read_bytes()
        if hashlib.sha256(src).hexdigest() != res["source_sha256"] or hashlib.sha256(obj).hexdigest() != res["object_sha256"]:
            raise ValueError("source/object changed since host verification")
        for key in ("symbol", "compiled_length", "claimed_length", "original_body_sha256", "compiled_body_sha256", "cl_sha256", "command_argv"):
            if key not in res:
                raise ValueError("incomplete verifier metadata: " + key)
        if not isinstance(res.get("relocations"), list):
            raise ValueError("verifier needs relocation records, not a count")
        if not isinstance(res["command_argv"], list) or not res["command_argv"] or not all(isinstance(x, str) for x in res["command_argv"]):
            raise ValueError("verifier needs the actual compiler argv list")
        attempt = dict(cl_sha256=res["cl_sha256"], command=res["command_argv"], exit_code=0, source_sha256=res["source_sha256"], object_sha256=res["object_sha256"])
        created = []
        try:
            sd.mkdir(parents=True); created.append(sd)
            drop.mkdir(parents=True); created.append(drop)
            (sd / "candidate.cpp").write_bytes(src)
            save_json(sd / "COMPARE.json", res)
            save_json(sd / "attempts.json", [attempt])
            files = ["candidate.cpp", "COMPARE.json", "attempts.json"]
            (drop / "candidate.obj").write_bytes(obj)
            if repo.cfg["rules"].get("share_objects"):
                (sd / "candidate.obj").write_bytes(obj); files.append("candidate.obj")
            (sd / "SUBMISSION_MANIFEST.sha256").write_text("".join(hashlib.sha256((sd / f).read_bytes()).hexdigest() + "  " + f + "\n" for f in files), encoding="ascii")
            save_json(sd / "IBLIS.json", dict(result_id=v["id"], contributor=v["login"] if v.get("show_name") else who, github_id=v["github_id"], source="iblis", model=v.get("model")))
            if v.get("notes"):
                (sd / "NOTES.md").write_text(v["notes"], encoding="utf-8")
            item.update(status="submitted", submitted_by=who, submitted_utc=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
                        attempts_used=item.get("attempts_used", 0)+1, chat="iblis-result-%d" % int(v["id"]), producer_status=res["status"],
                        object_sha256=res["object_sha256"], source_sha256=res["source_sha256"], compiled_body_sha256=res["compiled_body_sha256"], symbol=res["symbol"], model=v.get("model") or "unknown")
            with dst.open("x", encoding="utf-8", newline="\n") as f:
                json.dump(item, f, indent=1)
            created.append(dst)
            q.unlink()
        except Exception:
            for p in reversed(created):
                if p.is_dir(): shutil.rmtree(p)
                else: p.unlink(missing_ok=True)
            raise
        v["integrated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_json(vp, v)


def cmd_integrate(repo):
    n = 0
    for vp in sorted((Path(repo.root) / "work" / "host").glob("*/verdict.json")):
        try:
            v = json.loads(vp.read_text(encoding="utf-8"))
            if v.get("verdict") != "verified" or v.get("integrated") or not v.get("delivered"):
                continue
            import_record(repo, vp, v)
            n += 1
        except Exception as e:
            print("import deferred %s: %s" % (vp.parent.name, e), file=sys.stderr)
    print("prepared", n, "submissions; review the queue changes and commit privately; no push was performed")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    repo = Repo(arg("--repo") or sys.exit("--repo PATH is required"))
    c = sys.argv[1]
    if c == "flags": print(" ".join(repo.flags(norm(sys.argv[-1]))))
    elif c == "sync": cmd_sync(repo, arg("--redivivus"), int(arg("--examples", "0")))
    elif c == "verify": cmd_verify(repo, "--once" in sys.argv)
    elif c == "integrate": cmd_integrate(repo)
    elif c == "review": cmd_review(repo, sys.argv[2], arg("--id"), arg("--verdict"), arg("--note"))
    else: sys.exit(__doc__)
