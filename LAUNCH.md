# Opening Project Iblis to contributors

The owner's checklist, in order. Each step says how to know it worked. Nothing here publishes project source:
packets carry no project code, and the only code a contributor ever receives is another contributor's give-up.

## 1. Merge and deploy

Merge the pull request into `main`. Workers Builds deploys it. No database migration is needed.

**Done when** `python tools/launch_check.py` passes its public checks: pages, guide address, kit checksum, API
refusals. While intake is still closed, the guide shows the pause notice.

## 2. Set up the build host

The host compiles every submission inside a locked-down Docker container. It needs a machine that:

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
2. `python host/host.py sync --repo <Diablo2-Native>`. Add `--redivivus <checkout>` for reference pointers.

**Done when** the sync prints `synced N of N`.

## 4. Open the gates

In `wrangler.toml`, set `CREDENTIALS_ROTATED = "true"` and `CONTRIBUTIONS_ENABLED = "true"`. Then rebuild the pages
and push:

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

Invite 3–5 people you trust before any public post. Send them the Join page. What they need:

- their own Diablo II: Lord of Destruction 1.14d;
- an agent: Claude Code, Codex or similar;
- a GitHub account;
- Git and Python; the kit downloads the compiler for them.

**About the compiler.** Iblis hosts no Microsoft files. The guide's default is `setup --fetch`, which downloads the
pinned files from a third-party mirror and verifies every hash (owner decision); `--cl` uses an installation a
contributor already has. The mirror's own notice says it grants no licence for the files.

Watch `host.py review list` and the first strikes closely during the soft launch: they show where the guide or the
kit is unclear.
