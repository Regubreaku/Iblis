#!/usr/bin/env python3
"""Check the live Project Iblis site and service before (and after) opening contributor intake.

  python tools/launch_check.py                         public checks: pages, guide address, kit checksum, auth
  IBLIS_HOST_KEY=... python tools/launch_check.py      also: the build host can reach its routes
  python tools/launch_check.py --contributor           also: connect, approve with GitHub (you open the link),
                                                       status, next, packet, then give the function back

Environment: IBLIS_URL (default https://iblis.captainlegbreak.workers.dev), IBLIS_HOST_KEY (optional).
The contributor walk-through needs intake open (CONTRIBUTIONS_ENABLED and CREDENTIALS_ROTATED = "true") and a loaded
queue (host.py sync). It keeps the agent key in memory only and never prints it; it gives the function straight back,
so nothing is submitted and no strike is possible. To test a full verdict, submit a real match with the kit afterwards
and run `python host/host.py verify --once`.
"""
import hashlib, json, os, sys, time, urllib.error, urllib.request

URL = os.environ.get("IBLIS_URL", "https://iblis.captainlegbreak.workers.dev").rstrip("/")
HOST_KEY = os.environ.get("IBLIS_HOST_KEY", "").strip()
results = []


def call(method, path, body=None, key=None, raw=False):
    headers = {"user-agent": "iblis-launch-check", "content-type": "application/json"}
    if key:
        headers["authorization"] = "Bearer " + key
    req = urllib.request.Request(URL + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            return r.status, data if raw else (json.loads(data) if data[:1] in b"{[" else data.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data)
        except ValueError:
            return e.code, data.decode("utf-8", "replace")


def check(name, ok, detail=""):
    results.append(ok)
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  (%s)" % detail) if detail else ""))
    return ok


def public():
    for page in ("/", "/join.html", "/how.html", "/account.html", "/legal.html", "/agents.md"):
        st, body = call("GET", page)
        check("page %s" % page, st == 200, "HTTP %d" % st)
    st, guide = call("GET", "/agents.md")
    if st == 200:
        check("agents.md gives this site's address", URL in guide, "it should mention %s" % URL)
        check("agents.md says whether intake is open", True, "paused notice shown" if "temporarily paused" in guide else "no pause notice: intake open")
    st, join = call("GET", "/join.html")
    if st == 200:
        check("Join page sentence points at this site", ("%s/agents.md" % URL) in join)
    st, kit = call("GET", "/kit/latest.json")
    if check("kit manifest", st == 200 and isinstance(kit, dict) and kit.get("file"), "HTTP %d" % st):
        st, blob = call("GET", "/kit/" + kit["file"], raw=True)
        check("kit download matches its SHA-256", st == 200 and hashlib.sha256(blob).hexdigest() == kit.get("sha256"),
              "%s, %d KB" % (kit["file"], len(blob) // 1024 if st == 200 else 0))
    st, _ = call("POST", "/api/v1/status", {})
    check("API refuses requests without a key", st == 401, "HTTP %d" % st)
    st, _ = call("POST", "/api/v1/constructor", {}, key="not-a-key")
    check("API refuses unknown tool names", st in (401, 404), "HTTP %d" % st)


def host():
    st, body = call("GET", "/api/v1/host/pending?limit=1", key=HOST_KEY)
    if st == 503:
        return check("host routes", False, "503: set CREDENTIALS_ROTATED = \"true\" in wrangler.toml and deploy")
    if st == 401:
        return check("host routes", False, str(body.get("error") if isinstance(body, dict) else body))
    check("host routes answer the host key", st == 200, "HTTP %d" % st)


def contributor():
    st, c = call("POST", "/api/v1/connect", {})
    if not check("connect", st == 200 and isinstance(c, dict) and c.get("approve_url"), c.get("error", "") if isinstance(c, dict) else "HTTP %d" % st):
        return
    print("\n  Open %s\n  and check the code shows %s, then approve with GitHub. Waiting up to 15 minutes ...\n" % (c["approve_url"], c["code"]))
    key, deadline = None, time.time() + 900
    while time.time() < deadline and not key:
        time.sleep(max(2, int(c.get("poll_every_seconds", 5))))
        st, p = call("POST", "/api/v1/connect/poll", {"device_code": c["device_code"]})
        if st == 200 and isinstance(p, dict) and p.get("key"):
            key = p["key"]
        elif st != 200 or not (isinstance(p, dict) and p.get("pending")):
            break
    if not check("approval delivered an agent key", bool(key)):
        return
    st, s = call("POST", "/api/v1/status", {}, key=key)
    check("status", st == 200, "tier %s" % (s.get("tier") if isinstance(s, dict) else "?"))
    st, pk = call("POST", "/api/v1/next", {}, key=key)
    if not check("next hands out a function", st == 200 and isinstance(pk, dict) and pk.get("va"),
                 (pk.get("error") if isinstance(pk, dict) else str(pk)[:120]) if st != 200 else ""):
        return
    va = pk["va"]
    check("packet has address, length, flags and steps", all(pk.get(k) for k in ("va", "length", "flags", "local_steps")),
          "%s, %s B, %s" % (va, pk.get("length"), pk.get("band")))
    st, again = call("POST", "/api/v1/packet", {"va": va}, key=key)
    check("packet again", st == 200 and isinstance(again, dict) and again.get("va") == va)
    st, g = call("POST", "/api/v1/giveup", {"va": va, "reason": "launch check: handing the function straight back"}, key=key)
    check("giveup returns the function", st == 200, (g.get("error") if isinstance(g, dict) else "") if st != 200 else va)
    print("\n  The walk-through key stays valid. Revoke it on %s/account.html if you will not use it." % URL)


if __name__ == "__main__":
    print("Checking %s\n" % URL)
    public()
    if HOST_KEY:
        host()
    else:
        print("SKIP  host routes (set IBLIS_HOST_KEY to check them)")
    if "--contributor" in sys.argv:
        contributor()
    failed = results.count(False)
    print("\n%d checks, %d failed" % (len(results), failed))
    sys.exit(1 if failed else 0)
