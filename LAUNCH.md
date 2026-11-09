# Opening Project Iblis to contributors

**Current plan (2026-11-08): open to anyone, with Claude as the host.** Host rounds run in a disposable Claude
cloud container with `IBLIS_RUNNER=trusted-local` and `IBLIS_TRUSTED_GITHUB=*`: strangers' C++ never compiles on a
personal computer. Each round stages Game.exe read-only from the owner's PC, runs `host.py sync` and
`host.py verify --once`, and deletes it. The host key lives on the owner's PC (`C:\D2-Private\Diablo-II-114d\IBLIS_HOST_KEY.txt`)
and in Cloudflare. `INVITED_GITHUB` stays empty.

The owner's checklist, in order. Each step says how to know it worked. Nothing here publishes project source:
packets carry no project code, and the only code a contributor ever receives is another contributor's give-up.

## 1. Merge and deploy

Merge the pull request into `main`. Workers Builds deploys it. No database migration is needed.

**Done when** `python tools/launch_check.py` passes its public checks: pages, guide address, kit checksum, API
refusals. While intake is still closed, the guide shows the pause notice.

## 2. Set up the build host

**Soft launch route: trusted-local, no Docker.** On any machine with your Game.exe, Python 3.10+ with
`capstone==5.0.7`, Git, Wine 9 or Windows, and a checkout of Diablo2-Native:

```
python kit/iblis_check.py setup --game <Game.exe> --fetch
python kit/iblis_check.py selftest                    # must print MATCH
set IBLIS_RUNNER=trusted-local                        # export ... on Linux/macOS
set IBLIS_TRUSTED_GITHUB=login1,login2               # the same people as INVITED_GITHUB in step 4
```

This host compiles only those people's submissions, directly, without Docker. Anyone else's work goes to review
uncompiled and is not a strike. Switch to the Docker route below before opening to the public.

**Public route: Docker.** The host compiles every submission inside a locked-down Docker container. It needs a
machine that:

- can run Docker's Linux engine. On Windows that means hardware virtualization enabled in the BIOS (it is off on
  "adam" today), then Docker Desktop;
- has your own Game.exe and the Visual C++ 2005 SP1 `bin/` folder (the kit's `setup --fetch` downloads it);
- has Python 3.10+ with `capstone==5.0.7`, and a checkout of the private Diablo2-Native repository.

Follow `host/README.md`: build the runner image, copy `host/runner.example.json` outside the repository (it already
lists every pinned compiler file and hash), and set the paths and the image ID.

**Done when** the kit's own selftest matches on that machine (`python kit/iblis_check.py setup --game ... --cl ...`,
then `selftest` prints `MATCH`).

## 3. Give the service its host key and load the queue

1. Cloudflare dashboard → Worker `iblis` → Settings → **Runtime variables and secrets** → add Secret `HOST_KEY`.
   Put the same value in `IBLIS_HOST_KEY` on the host machine.
2. `python host/host.py sync --repo <Diablo2-Native>`.

**Done when** the sync prints `synced N of N`.

## 4. Open the gates

In `wrangler.toml`, set `CREDENTIALS_ROTATED = "true"` and `CONTRIBUTIONS_ENABLED = "true"`. For the soft launch,
also set `INVITED_GITHUB = "login1,login2"`: only those GitHub accounts can take work or submit, and the Join page and
guide say "invite-only" while it is set. Then rebuild the pages and push:

```
python tools/build_site.py public/data/stats.json public    # drops the "intake paused" notices
git commit -am "Open contributor intake" && git push
```

The pause notices on the Join page and in `agents.md` follow `CONTRIBUTIONS_ENABLED`, so they cannot disagree with
the service. To close intake again, set it back to `"false"` and repeat.

**Done when** `IBLIS_HOST_KEY=... python tools/launch_check.py` passes, host routes included.

## 5. Walk through it as a contributor

```
IBLIS_HOST_KEY=... python tools/launch_check.py --contributor
```

This connects, shows you an approval link, and waits while you approve with GitHub. It then checks `status`, takes
a function with `next`, reads its packet and gives it straight back. Nothing is submitted, so no strike is possible.

Then do one real verdict. With the agent key from your own approval, take a function, match it with the kit, submit
it, and run `python host/host.py verify --once --repo <Diablo2-Native>`. **Done when** `status` shows the verdict
`verified`.

## 6. Keep the host running

- Run `python host/host.py verify --repo <Diablo2-Native>` continuously; it posts verdicts within minutes.
- Integration is manual for now. `host.py integrate` prepares verified submissions in the private repository for
  your review; you commit them.
- Refresh the public figures after integrating:
  `python tools/build_stats.py <Diablo2-Native> public/data/stats.json`, then `tools/build_site.py`, then push.

## 7. Soft launch

Invite 3–5 people you trust before any public post: add their GitHub logins to `INVITED_GITHUB` (step 4) and to
`IBLIS_TRUSTED_GITHUB` on the host (step 2), then send them the Join page. What they need:

- their own Diablo II: Lord of Destruction 1.14d;
- an agent: Claude Code, Codex or similar;
- a GitHub account;
- Git and Python; the kit downloads the compiler for them.

**About the compiler.** Iblis hosts no Microsoft files. The guide's default is `setup --fetch`, which downloads the
pinned files from a third-party mirror and verifies every hash (owner decision); `--cl` uses an installation a
contributor already has. The mirror's own notice says it grants no licence for the files.

Watch `host.py review list` and the first strikes closely during the soft launch: they show where the guide or the
kit is unclear.
