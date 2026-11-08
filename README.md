# Project Iblis: site, service, checker kit, build host

Byte-exact decompilation of Diablo II 1.14d, run as a contributor project. See `PLAN.md` for the design.

| Folder | What |
|---|---|
| `src/` | site pages, shared partials, `agents.md` |
| `public/` | built site (Cloudflare static assets): pages, `agents.md`, `data/stats.json`, `kit/` |
| `worker/` | the service: `index.js` (Cloudflare Worker), `schema.sql` (D1) |
| `kit/` | the contributor checker `iblis_check.py` (+ `kit.json`, `names.json`, `headers/`, `selftest/` from `build_kit.py`) |
| `host/` | the build host `host.py` |
| `tools/` | `build_stats.py`, `build_site.py`, `build_kit.py` |

## Build

```
python tools/build_stats.py <source-repo> public/data/stats.json
python tools/build_kit.py   <source-repo> public/kit
python tools/build_site.py  public/data/stats.json public
```

## Run locally

```
npm i                                         # wrangler
npx wrangler d1 execute iblis --local --file worker/schema.sql
printf 'GITHUB_CLIENT_ID=x\nGITHUB_CLIENT_SECRET=x\nHOST_KEY=dev\n' > .dev.vars
npx wrangler dev --local
IBLIS_URL=http://127.0.0.1:8787 IBLIS_HOST_KEY=dev python host/host.py sync --repo <source-repo>
```

## Go live (owner)

1. Cloudflare account: `npx wrangler login`, `npx wrangler d1 create iblis`, put the id in `wrangler.toml`,
   `npx wrangler d1 execute iblis --remote --file worker/schema.sql`.
2. GitHub OAuth app: callback `https://<site>/auth/callback`; `npx wrangler secret put GITHUB_CLIENT_ID`,
   `GITHUB_CLIENT_SECRET`, and a long random `HOST_KEY`.
3. Set `SITE_URL` in `wrangler.toml` (and the URLs in `src/agents.md` and the Join page), then `npx wrangler deploy`.
4. On the build host: `python kit/iblis_check.py setup --game <Game.exe> --fetch`, then
   `python host/host.py sync --repo <source-repo>` and keep `python host/host.py verify --repo <source-repo>` running.
