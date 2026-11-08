#!/usr/bin/env python3
"""The Project Iblis build host. Runs where the authenticated Game.exe and the source repository live.

  python host/host.py sync      --repo PATH           push the queue (state, length, module, name, flags) to the service
  python host/host.py verify    --repo PATH [--once]  poll pending submissions, recompile each, post verdicts
  python host/host.py integrate --repo PATH           write the day's verified sources into the repository (nightly)
  python host/host.py flags     --repo PATH VA        print the compile flags for one function

Environment: IBLIS_URL (default https://iblis.pages.dev), IBLIS_HOST_KEY (the service's HOST_KEY secret).
Verdicts: verified = the host's compile matches; failed = the submission cannot be right as sent (forbidden construct,
no marker, does not compile, extra forms); review = it compiles cleanly but the host sees different bytes (not a strike).
"""
import glob, json, os, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "kit"))
import iblis_check as kit  # noqa: E402

URL = os.environ.get("IBLIS_URL", "https://iblis.pages.dev").rstrip("/")
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

    def queue(self):
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
                out.append(dict(va=va, length=it["length"], module=it.get("module", "?"), state=st,
                                name=self.names.get(va), flags=" ".join(self.flags(va)), body_sha256=it.get("body_sha256"),
                                note=it.get("previous_park_reason") or it.get("last_park_reason")))
        return out


def cmd_sync(repo):
    fs = repo.queue()
    for i in range(0, len(fs), 500):
        r = api("POST", "/api/v1/host/sync", {"functions": fs[i:i + 500]})
        print("synced", i + r["synced"], "of", len(fs))


def verdict_for(res):
    st = res["status"]
    if st in ("RAW_EQUAL", "RELOCATION_RESOLVED_EQUAL"):
        return "verified", "%s, body %s" % (st, res["compiled_body_sha256"][:16])
    if st in ("FORBIDDEN", "NO_MARKER", "COMPILE_ERROR", "NO_SYMBOL", "MULTI_FORM"):
        return "failed", "%s: %s" % (st, (res.get("detail") or ", ".join(res.get("extra_forms", [])))[:600])
    return "review", "host sees %d B vs %d B, %d bytes differ outside relocations, first at %s" % (
        res["compiled_length"], res["length"], res["unmasked_diff_bytes"], res["first_diff"])


def cmd_verify(repo, once):
    s = json.load(open(kit.SETTINGS))
    if kit.sha256_file(s["image"]) != repo.cfg["image"]["sha256"]:
        sys.exit("the host's Game.exe is not the authenticated image")
    lengths = {f["va"]: f["length"] for f in repo.queue()}
    while True:
        pend = api("GET", "/api/v1/host/pending?limit=20")["pending"]
        for p in pend:
            va = norm(p["va"])
            wd = os.path.join(repo.root, "work", "host", "%s-r%d" % (va, p["id"]))
            res = kit.compile_and_compare(s, va, lengths[va], repo.flags(va), p["source"], wd)
            v, note = verdict_for(res)
            if v == "verified" and p.get("body_sha256") and p["body_sha256"] != res["compiled_body_sha256"]:
                v, note = "review", "matches here, but the submitted body hash differs from the host's"
            json.dump(dict(p, host=res, verdict=v), open(os.path.join(wd, "verdict.json"), "w"), indent=1)
            api("POST", "/api/v1/host/verdict", {"id": p["id"], "verdict": v, "note": note})
            print(time.strftime("%H:%M:%S"), va, p["login"], v, note[:100])
        if once:
            return
        time.sleep(60 if not pend else 1)


def cmd_integrate(repo):
    """Write each verified submission's source into queue/submitted/<VA>/ with the contributor credited, the same layout
    tools/submit.py uses, so the repository's own verifier flow moves it to matched. Run nightly after verify."""
    done = os.path.join(repo.root, "work", "host")
    n = 0
    for vp in sorted(glob.glob(os.path.join(done, "*", "verdict.json"))):
        v = json.load(open(vp))
        if v.get("verdict") != "verified" or v.get("integrated"):
            continue
        va = norm(v["va"]); sdir = os.path.join(repo.root, "queue", "submitted", va[2:])
        os.makedirs(sdir, exist_ok=True)
        open(os.path.join(sdir, "candidate.cpp"), "w", encoding="latin-1", newline="\n").write(v["source"])
        if v.get("notes"):
            open(os.path.join(sdir, "NOTES.md"), "w").write(v["notes"])
        who = v["login"] if v.get("show_name") else "iblis-%d" % v["github_id"]
        json.dump(dict(va=va, source="iblis", contributor=who, github_id=v["github_id"], model=v.get("model"),
                       host_status=v["host"]["status"], compiled_body_sha256=v["host"]["compiled_body_sha256"],
                       command=v["host"]["command"]), open(os.path.join(sdir, "IBLIS.json"), "w"), indent=1)
        v["integrated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()); json.dump(v, open(vp, "w"), indent=1)
        n += 1
    print("integrated", n, "verified submissions into queue/submitted/; commit them with the contributors as authors")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    repo = Repo(arg("--repo") or sys.exit("--repo PATH is required"))
    c = sys.argv[1]
    if c == "flags":
        print(" ".join(repo.flags(norm(sys.argv[-1]))))
    elif c == "sync":
        cmd_sync(repo)
    elif c == "verify":
        cmd_verify(repo, "--once" in sys.argv)
    elif c == "integrate":
        cmd_integrate(repo)
    else:
        sys.exit(__doc__)
