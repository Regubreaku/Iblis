#!/usr/bin/env python3
"""Assemble the contributor checker kit from the source repository.

usage: build_kit.py <repo> <out-dir>
       build_kit.py --package-only <out-dir>

Writes kit.json (game and compiler hashes, selftest), names.json (address -> name), headers/, context.json
(declarations and struct layouts mined from verified sources, see build_context.py) with NOTICES/ (the licence notices
of the MIT/zlib code those sources adapt), CONVENTIONS.md and the selftest source next to iblis_check.py, then zips it as public/kit/iblis-check-<date>.zip with a .sha256 beside it.
The kit carries hashes, names and one verified function's source; never any bytes from the game.
"""
import datetime, glob, hashlib, io, json, os, re, shutil, sys, zipfile
from pathlib import Path

def package_kit(kit, out):
    """Publish the prepared kit, excluding local work and maintainer material."""
    kit, out = Path(kit), Path(out)
    required = ['README.md', 'LICENSE', 'CONVENTIONS.md', 'MATCHING.md', 'iblis_check.py', 'kit.json', 'names.json',
                'headers/README.md', 'selftest/candidate.cpp', 'selftest/fixture.json']
    paths = [kit / name for name in required]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    paths.extend(p for p in (kit / 'headers').rglob('*')
                 if p.is_file() and p.suffix.lower() in {'.h', '.hpp', '.hh', '.hxx', '.inl', '.inc'})
    # The context index is mined from verified sources that adapt MIT/zlib-licensed code: it ships only with those notices.
    notices = sorted(p for p in (kit / 'NOTICES').glob('*.txt') if p.is_file()) if (kit / 'NOTICES').is_dir() else []
    if (kit / 'context.json').is_file():
        if not notices:
            raise FileNotFoundError(kit / 'NOTICES' / '*.txt')
        paths.append(kit / 'context.json')
    paths.extend(notices)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(paths):
            z.write(path, 'iblis-check/' + path.relative_to(kit).as_posix())
    payload = archive.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    # Content-qualified names keep a cached older ZIP separate from this release.
    name = 'iblis-check-%s-%s.zip' % (datetime.date.today().isoformat(), digest[:12])
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    target.write_bytes(payload)
    target.with_suffix('.zip.sha256').write_text('%s  %s\n' % (digest, name), encoding='utf-8')
    (out / 'latest.json').write_text(json.dumps(dict(file=name, sha256=digest, bytes=len(payload)), indent=1), encoding='utf-8')
    print('kit: %s (%d KB), %d files' % (name, len(payload) // 1024, len(paths)))
    return str(target)


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
    # Keep the independently checked, relocation-free setup fixture stable across repository refreshes.
    st = json.load(open(os.path.join(kit, "selftest", "fixture.json")))
    kitcfg = dict(
        image_sha256=cfg["image"]["sha256"],
        toolchain=dict(cl_version=cfg["toolchain"]["cl_version"], repo=m.group(1), commit=m.group(2), files=files),
        selftest=st,
    )
    with open(os.path.join(kit, "kit.json"), "w") as f:
        json.dump(kitcfg, f, indent=1); f.write("\n")
    json.dump(dict(sorted(r.names.items())), open(os.path.join(kit, "names.json"), "w"), separators=(",", ":"))
    # Keep the service's own contributor notes; source-repo notes describe the old workflow.
    headers = Path(kit, 'headers').resolve()
    if headers.parent != Path(kit).resolve():
        raise ValueError('Headers must stay inside the kit directory')
    header_note = (headers / 'README.md').read_text(encoding='utf-8')
    shutil.rmtree(headers)
    shutil.copytree(os.path.join(repo, 'headers'), headers)
    (headers / 'README.md').write_text(header_note, encoding='utf-8')

    # Declarations and layouts from verified sources, with the licence notices of the code they adapt.
    import build_context
    idx = build_context.build(repo)
    with open(os.path.join(kit, "context.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, separators=(",", ":"), sort_keys=True)
    nd = Path(kit, "NOTICES")
    if nd.exists():
        shutil.rmtree(nd)
    nd.mkdir()
    for p in sorted(glob.glob(os.path.join(repo, "NOTICE-*.txt"))):
        shutil.copyfile(p, nd / os.path.basename(p))
    print("context: %d declarations, %d structs; notices: %s" % (len(idx["decls"]), len(idx["fields"]),
                                                               ", ".join(sorted(os.listdir(nd))) or "NONE"))

    package_kit(kit, out)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--package-only':
        package_kit(Path(__file__).resolve().parent.parent / 'kit', sys.argv[2])
    else:
        main(*sys.argv[1:3])
