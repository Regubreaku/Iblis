#!/usr/bin/env python3
"""Assemble the contributor checker kit from the source repository.

usage: build_kit.py <repo> <out-dir>

Writes kit.json (game and compiler hashes, selftest), names.json (address -> name), headers/, CONVENTIONS.md and the
selftest source next to iblis_check.py, then zips it as public/kit/iblis-check-<date>.zip with a .sha256 beside it.
The kit carries hashes, names and one verified function's source; never any bytes from the game.
"""
import datetime, glob, hashlib, json, os, re, shutil, sys, zipfile

SELFTEST_VA = "0x00411540"


def main(repo, out):
    here = os.path.dirname(os.path.abspath(__file__))
    kit = os.path.join(here, "..", "kit")
    sys.path.insert(0, os.path.join(here, "..", "host"))
    from host import Repo  # flags per address, the same way the build host computes them
    r = Repo(repo)
    cfg = r.cfg
    files = dict(l.split("  ", 1)[::-1] for l in open(os.path.join(repo, "setup", "toolchain.sha256")).read().splitlines() if l.strip())
    prov = cfg["toolchain"]["provenance"]
    m = re.search(r"(https://github\.com/\S+) @ ([0-9a-f]{40})", prov)
    st = json.load(open(os.path.join(repo, "queue", "matched", SELFTEST_VA[2:] + ".json")))
    kitcfg = dict(
        image_sha256=cfg["image"]["sha256"],
        toolchain=dict(cl_version=cfg["toolchain"]["cl_version"], repo=m.group(1), commit=m.group(2), files=files),
        selftest=dict(va=SELFTEST_VA, length=st["length"], flags=" ".join(r.flags(SELFTEST_VA)),
                      compiled_body_sha256=st["compiled_body_sha256"]),
    )
    json.dump(kitcfg, open(os.path.join(kit, "kit.json"), "w"), indent=1)
    json.dump(dict(sorted(r.names.items())), open(os.path.join(kit, "names.json"), "w"), separators=(",", ":"))
    shutil.rmtree(os.path.join(kit, "headers"), ignore_errors=True)
    shutil.copytree(os.path.join(repo, "headers"), os.path.join(kit, "headers"))
    shutil.copy(os.path.join(repo, "examples", "CONVENTIONS.md"), os.path.join(kit, "CONVENTIONS.md"))
    os.makedirs(os.path.join(kit, "selftest"), exist_ok=True)
    shutil.copy(os.path.join(repo, "queue", "matched", SELFTEST_VA[2:], "candidate.cpp"), os.path.join(kit, "selftest", "candidate.cpp"))

    os.makedirs(out, exist_ok=True)
    zname = "iblis-check-%s.zip" % datetime.date.today().isoformat()
    zpath = os.path.join(out, zname)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(glob.glob(os.path.join(kit, "**", "*"), recursive=True)):
            rel = os.path.relpath(p, kit)
            if os.path.isfile(p) and not rel.startswith(("toolchain", "work", "__pycache__")) and not rel.endswith(".pyc"):
                z.write(p, os.path.join("iblis-check", rel))
    h = hashlib.sha256(open(zpath, "rb").read()).hexdigest()
    open(zpath + ".sha256", "w").write("%s  %s\n" % (h, zname))
    json.dump(dict(file=zname, sha256=h, bytes=os.path.getsize(zpath)), open(os.path.join(out, "latest.json"), "w"), indent=1)
    print("kit: %s (%d KB) sha256 %s; selftest flags: %s" % (zname, os.path.getsize(zpath) // 1024, h, kitcfg["selftest"]["flags"]))


if __name__ == "__main__":
    main(*sys.argv[1:3])
