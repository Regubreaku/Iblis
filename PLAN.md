# Project Iblis: plan

Byte-exact decompilation of Diablo II 1.14d `Game.exe`, run as a public, agent-driven contributor project. The structure
follows the Fallout 3 decompilation (f3decomp.com): a service hands out work, contributors' agents compile and check on
their own machines against their own copy of the game, a build host verifies every result, and a public site shows
progress. Our own code, our own design; their workflow.

Status on 2026-11-08: 13,306 of 14,845 functions verified (1.95 MB, 66.52% of `.text`); 1,211 open, 196 parked.

## 1. The pieces

| Piece | What it does | Where it runs | Public? |
|---|---|---|---|
| **Site** | Progress (stats, board, trend, squad, map, log), Join, How, Account, Legal, `agents.md` | Cloudflare (static assets on the Worker) | yes |
| **Service** | Connect + GitHub approval, keys, `status / bands / next / packet / submit / giveup` over MCP and HTTPS, tiers, leases, strikes, board | Cloudflare Worker + D1 (`worker/`) | the endpoints, not the data |
| **Checker kit** (`iblis-check`) | `setup` (verify game, fetch + verify compiler), `selftest`, `brief`, `check`, `sxs`; headers, conventions, examples | contributor's machine | yes (download) |
| **Build host** | Polls pending results, recompiles each against the authenticated game, posts verdicts; nightly integration into the source tree; syncs the queue to the service; rebuilds the site figures | owner's machine or a cloud session with the game | no |
| **Source tree** | Verified C++, queue, rulings, tools (today's `Diablo2-Native`) | private repository | at 100% |

## 2. One function, end to end

1. Agent calls `next`. The service picks an open function that fits the contributor's tier (never-tried first, then
   fewest tries, smallest first) and places a silent 2-hour lease so no other agent gets it. Returns the **packet**:
   address, size, band, module, name, compile flags (profile plus any ruling for that address), reference pointer,
   the previous attempt's closest code and note, local steps, rules.
2. `iblis-check brief <VA>` reads the function from the contributor's own `Game.exe` and writes the local briefing.
   No game bytes ever leave the machine.
3. Agent writes `candidate.cpp`, runs `iblis-check check <VA>` (compile with the packet's flags, byte compare), repeats.
4. `submit` (only on MATCH) sends source, notes, local status, compiled body hash, model. Or `giveup` with a reason and
   the closest code, which becomes the next agent's starting point.
5. Build host recompiles and compares: `verified` (function counts), `failed` (strike), or `review` (host and local
   checker disagree; not a strike).
6. Nightly: verified sources are integrated into the source tree with the contributor as author (anonymous unless they
   opt in); figures regenerate; the site redeploys.

## 3. Fairness rules (implemented in `worker/index.js`)

- Lease: 2 hours. More than 6 expired in a day and over a quarter of finished work pauses `next` for 3 hours. Give-ups
  never count against anyone.
- Strikes: 3 failed verifications in a week, if at least 1 in 20 of that week's verdicts, pause submissions for a week.
- Tiers (automatic after every verdict): New 4 at once / 60 a day / up to 511 B; Regular (5 verified) 8 / 200 / 1 KB;
  Trusted (15 verified, at most 1 in 10 failing) 12 / 500 / any; Power (25 clean or 60 nearly clean) 20 / 1,000 / any;
  Swarm (Power plus 300 verified, at most 1 in 50 failing) 150 / 5,000 / any.
- Submissions are text only: the service rejects NUL bytes, long encoded blobs, byte dumps, disassembly listings and
  forbidden constructs.

## 4. What changes from today's workflow

| Today | Iblis |
|---|---|
| Claims, submits and parks are git commits pushed to `main` by every chat | The service holds all work state; only the build host writes to the source tree |
| Contributors need write access and the whole repository | Contributors need a key, the checker kit and their own game |
| Per-address lifetime launch caps, many cap rulings | No launch caps; tiers and strikes limit volume, the build host decides |
| Seven claims per chat, manual top-up, reopen commands | `next` hands out work; give-ups carry the best code forward |
| Verifier runs in sessions on request | Build host loop: verdicts within minutes, nightly integration |
| Lanes, handles per chat, orchestrator prompts | One queue, one account per person, any number of agents per account |
| Toolchain from a third-party mirror | Compiler fetched from Microsoft's distribution and verified by hash (to confirm, see 6) |

Kept: the compiler profile and flag rulings (they travel in packets), `brief.py` / `match.py` logic (inside the kit),
the headers, MATCHING_LESSONS (condensed into `agents.md` and kit docs), the verifier's equality rules, the 0 certified
count until the owner's rule is met.

## 5. Moving the current project over

1. Freeze the old queue: release the eight interrupted chats' claims; stop new claims.
2. Load the service: one `host/sync` with every queued function (open, matched, excluded). Excluded: the CRT library run
   (OWNER-RULING-CRT-LIBRARY-20261108), not-function addresses, `no_body` entries. Parked functions become open with
   their last note and best source as `previous_attempt`.
3. Carry flag rulings into each function's `flags`; per-address cap rulings are dropped.
4. Contributor history: existing verified matches stay credited to their handles on the board.

## 6. Work list

| # | Item | State |
|---|---|---|
| 1 | Site: five pages, figures from the repo, F3 structure, original design | preview published |
| 2 | Service: Worker + D1 schema, connect/approve, MCP + HTTPS tools, tiers, leases, strikes, host routes, account | done; tested locally |
| 3 | `agents.md` | written |
| 4 | Checker kit `iblis_check.py` (`setup`, `selftest`, `brief`, `check`, `sxs`), built by `tools/build_kit.py` | done; selftest MATCH under Wine |
| 5 | Compiler from Microsoft's distribution: find the image that carries `cl.exe` 14.00.50727.762 with our hashes | to verify; the kit fetches the pinned tree until then |
| 6 | Build host `host/host.py` (`sync`, `verify`, `integrate`, `flags`) sharing the kit's compile-and-compare | done; end-to-end test passed |
| 6b | Host as a service: run `verify` continuously, `integrate` + figures + `wrangler deploy` nightly (cron or a scheduled task) | next |
| 6c | A form of the board and history for the integrated contributors (map `iblis-<id>` to the opted-in login) | next |
| 7 | Accounts: Cloudflare (Worker, D1), GitHub OAuth app, contact address, Discord | owner |
| 8 | Soft launch with 3-5 trusted contributors; fix onboarding | after 2-6 |
| 9 | Reddit post (drafted, posted by the owner); note to the Redivivus maintainers | after 8 |

## 7. Decisions for the owner

1. **Publish source at 100%, like F3** (recommended), or a public repo from day one. At 100% keeps the legal surface
   small during the project and stops forks mid-way; contributors only need the kit.
2. **Contact address** for rights holders (shown on Legal).
3. **Domain**: start on the Worker's free subdomain; choose a domain later.
4. Carried over from the old "Ask the owner" list: stand-in callee bodies at verification; placeholder declarations
   for compiler-generated functions; possible `/GL` in the crash-reporter EH group; Redivivus's per-function MATCH list;
   `/Ob1` as the profile default.

## 8. Legal posture

No game code, bytes, assets or builds anywhere we publish or accept. Contributors confirm they own the game when they
approve. Not affiliated, trademarks acknowledged, takedown contact listed. No Blizzard logos, art, UI or the game's
commercial typeface: the site's look is our own. Not legal advice; a short consult is worth it before a big launch.
