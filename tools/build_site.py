#!/usr/bin/env python3
"""Render every page in src/pages/ into public/, with the shared head, style, masthead and footer.

usage: build_site.py <stats.json> <public-dir>

Pages carry the figures inline (window.IBLIS) so each one works on its own, as a static file or a preview.
Only counts and sizes are published: no names, code or bytes from the game.
"""
import glob, json, os, re, sys
from site_stats import normalize_stats

FONTS = ("https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=Spectral:ital,wght@0,400;0,600;0,700;1,400"
         "&family=IBM+Plex+Mono:wght@400;500&display=swap")


DEFAULT_SITE = "https://iblis.captainlegbreak.workers.dev"


def site_url():
    """The one public address pages and agents.md give agents: $IBLIS_SITE_URL, else SITE_URL in wrangler.toml, else
    the workers.dev address. Keeping it in one place stops the Join page and agents.md drifting from the live site."""
    if os.environ.get("IBLIS_SITE_URL"):
        return os.environ["IBLIS_SITE_URL"].rstrip("/")
    w = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wrangler.toml")
    m = re.search(r'^\s*SITE_URL\s*=\s*"([^"]+)"', open(w).read(), re.M) if os.path.exists(w) else None
    return (m.group(1) if m else DEFAULT_SITE).rstrip("/")


def intake_open():
    """Pause notices follow the service's own gate: CONTRIBUTIONS_ENABLED in wrangler.toml."""
    w = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "wrangler.toml")
    m = re.search(r'^\s*CONTRIBUTIONS_ENABLED\s*=\s*"([^"]*)"', open(w).read(), re.M) if os.path.exists(w) else None
    return bool(m and m.group(1) == "true")


def pause_blocks(text, is_open):
    """{{#paused}}...{{/paused}} shows only while intake is closed."""
    out = re.sub(r"\{\{#paused\}\}(.*?)\{\{/paused\}\}", "" if is_open else r"\1", text, flags=re.S)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", out) if is_open else out


def main(stats_path, out_dir):
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
    stats = normalize_stats(json.load(open(stats_path)))
    site = site_url()
    is_open = intake_open()
    print("intake:", "open" if is_open else "paused (CONTRIBUTIONS_ENABLED in wrangler.toml)")
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
        page = (pause_blocks(page, is_open).replace("{{SITE}}", site)
                    .replace("{{HEAD}}", head).replace("{{NAV}}", part("nav.html")).replace("{{FOOTER}}", part("footer.html"))
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
            text = pause_blocks(open(p, encoding="utf-8").read(), is_open).replace("{{SITE}}", site)
            assert "{{" not in text, p
            open(os.path.join(out_dir, extra), "w", encoding="utf-8").write(text)
            print("copied", extra)


if __name__ == "__main__":
    main(*sys.argv[1:3])
