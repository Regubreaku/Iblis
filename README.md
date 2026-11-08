# Project Iblis: site, service, checker kit, build host

Byte-exact decompilation of Diablo II 1.14d, run as a contributor project. See `PLAN.md` for the design.

| Folder | What |
|---|---|
| `src/` | site pages, shared partials, `agents.md` |
| `public/` | built site (Cloudflare static assets): pages, `agents.md`, `data/stats.json`, `kit/` |
| `worker/` | the service: `index.js` (Cloudflare Worker), `schema.sql` (D1) |
| `kit/` | the contributor checker `iblis_check.py` (+ `kit.json`, `names.json`, `headers/`, `selftest/` from `build_kit.py`) |
| `host/` | the build host `host.py` |
| `tools/` | `build_stats.py`, `build_site.py`, `build_kit.py`, `build_context.py` (kit declaration index), `launch_check.py` (live checks) |

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

**Start with [`LAUNCH.md`](LAUNCH.md)**: the checklist in order, with a check for each step. The notes below are
the original setup reference.

1. Cloudflare account: `npx wrangler login`, `npx wrangler d1 create iblis`, put the id in `wrangler.toml`,
   `npx wrangler d1 execute iblis --remote --file worker/schema.sql`.
2. GitHub OAuth app: callback `https://<site>/auth/callback`; `npx wrangler secret put GITHUB_CLIENT_ID`,
   `GITHUB_CLIENT_SECRET`, and a long random `HOST_KEY`.
3. Set `SITE_URL` in `wrangler.toml` (and the URLs in `src/agents.md` and the Join page), then `npx wrangler deploy`.
4. Follow `host/README.md` to provision the isolated compiler using your local game and compiler.
   Set `IBLIS_RUNNER_CONFIG` and the current host key. Test a real known match and a deliberate mismatch before
   running `python host/host.py verify --repo <source-repo>`. There is no unsandboxed public compilation fallback.
5. Rotate `HOST_KEY` in Cloudflare and in the private host environment. Regenerate the GitHub OAuth app client secret
   and replace `GITHUB_CLIENT_SECRET` in Cloudflare, then revoke the old OAuth secret. Set `CREDENTIALS_ROTATED = "true"`
   only after both replacements are complete. Until then, host routes and OAuth sign-in are disabled.
6. After validating the isolated host and downstream private import,
   set `CONTRIBUTIONS_ENABLED = "true"`, remove the temporary pause notice in the Join page and agent guide, rebuild
   and deploy. Until then, intake stays closed; existing credentials can still view status or revoke their keys.

Run `npm test` for API/SQLite and Python regression tests. `npx wrangler deploy --dry-run` checks Worker packaging.
Do not treat those checks as proof of an operational build host or a re-verification of historical matches.
