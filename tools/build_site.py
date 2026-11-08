#!/usr/bin/env python3
"""Render every page in src/pages/ into public/, with the shared head, style, masthead and footer.

usage: build_site.py <stats.json> <public-dir>

Pages carry the figures inline (window.IBLIS) so each one works on its own, as a static file or a preview.
Only counts and sizes are published: no names, code or bytes from the game.
"""
import glob, json, os, shutil, sys
from site_stats import normalize_stats

FONTS = ("https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Spectral:ital,wght@0,400;0,600;0,700;1,400"
         "&family=IBM+Plex+Mono:wght@400;500&display=swap")


def main(stats_path, out_dir):
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
    stats = normalize_stats(json.load(open(stats_path)))
    part = lambda n: open(os.path.join(src, "partials", n)).read()
    head = ('<meta name="claude:viewport-fit-sides" content="cover">\n'
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
            f'<link rel="stylesheet" href="{FONTS}">\n'
            f"<style>\n{part('style.css')}</style>")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    with open(os.path.join(out_dir, "data", "stats.json"), "w", encoding="utf-8") as output:
        json.dump(stats, output, indent=1)
    kp = os.path.join(out_dir, "kit", "latest.json")
    kit = open(kp).read().strip() if os.path.exists(kp) else "null"
    for path in sorted(glob.glob(os.path.join(src, "pages", "*.html"))):
        page = open(path).read()
        page = (page.replace("{{HEAD}}", head).replace("{{NAV}}", part("nav.html")).replace("{{FOOTER}}", part("footer.html"))
                    .replace("/*__STATS__*/null", json.dumps(stats, separators=(",", ":")) + "; window.IBLIS_KIT = " + kit))
        assert "{{" not in page, path
        name = os.path.basename(path)
        # Deployed pages are complete documents. The preview's entry page stays a fragment (the preview host wraps it).
        title, rest = page.split("\n", 1)  # every page starts with its <title> line
        doc = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
               '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' + title + "\n" +
               '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
               '[hidden]{display:none!important}img{max-width:100%}</style>\n</head>\n<body>\n' + rest + "\n</body>\n</html>\n")
        open(os.path.join(out_dir, name), "w").write(doc)
        if name == "index.html":
            os.makedirs(os.path.join(out_dir, "..", "preview"), exist_ok=True)
            open(os.path.join(out_dir, "..", "preview", "index.html"), "w").write(page)
        print("wrote %s (%d KB)" % (name, len(doc) // 1024))
    for extra in ("agents.md",):
        p = os.path.join(src, extra)
        if os.path.exists(p):
            shutil.copy(p, os.path.join(out_dir, extra))
            print("copied", extra)


if __name__ == "__main__":
    main(*sys.argv[1:3])
