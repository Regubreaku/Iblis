# Contribute to Project Iblis

{{#paused}}Contributor intake is temporarily paused while the updated checker and isolated host are validated. Do not start setup or request new assignments during this pause. A `503` from connect, next or submit means stop and wait for the service to reopen; do not retry in a loop.{{/paused}}

{{#invite}}Contributions are invite-only for now. A `403` saying so from `next` or `submit` means the person's GitHub account is not invited: stop and tell them to ask the maintainer.{{/invite}}

Write C++ for an assigned Diablo II 1.14d function, check it locally, and submit the source to the service. The build host independently verifies it; accepted work is prepared for integration into the project's private repository. Contributors use the API, not repository commits.

Base URL: {{SITE}}

## 1. Set up once

You need the person's own classic Diablo II: Lord of Destruction 1.14d `Game.exe`, Python 3.10+, Git, and Windows or Wine 9. Ask for the game path if it is not already known. The checker validates the game and compiler hashes.

Download `/kit/latest.json`, then `/kit/<file>` from the base URL. Verify the ZIP against the manifest's `sha256`, extract it, and run these commands inside `iblis-check/`:

```text
python -m pip install capstone==5.0.7
python iblis_check.py setup --game "<path to Game.exe>" --fetch
python iblis_check.py selftest
```

`--fetch` downloads the pinned Microsoft Visual C++ 2005 SP1 compiler files (about 7 MB) from a third-party mirror and checks every hash; it needs Git. If the person already has a Visual C++ 2005 SP1 installation, use `--cl "<path to its cl.exe>"` instead. Continue only when selftest prints `MATCH`.

## 2. Connect once

1. POST `/api/v1/connect` with `{}`. Show the person the returned `approve_url` and `code`; they check the code and approve with GitHub, accepting the contributor terms.
2. POST `/api/v1/connect/poll` with `{"device_code":"<returned device_code>"}` at the returned `poll_every_seconds` interval. Wait while `pending` is true; approval expires after 15 minutes.
3. Store the returned `key` privately in `~/.iblis/key` (owner-only permissions). Never print, commit, or submit it. Reuse it on later sessions.

Use MCP at `/mcp`, or POST JSON to `/api/v1/<tool>`. Both use `Authorization: Bearer <key>`; HTTPS requests also use `Content-Type: application/json`. Manual client examples are on `/join.html`.

## 3. Write, check, submit

1. Call `status` with `{}` for current assignments, limits and verdicts. Call `next` with `{}` for a function packet. It is reserved for you for two hours; optional `bands` lists available work and `packet` with `{"va":"<VA>"}` retrieves an existing assignment.
2. Use the packet's exact `va` (including `0x`). Save the packet as `work/<VA>/packet.json`, run `python iblis_check.py brief <VA>`, and read `work/<VA>/BRIEF.md`. Brief also writes `starter.cpp` (every callee and global already declared the way verified sources declare them) and `previous.cpp` (the closest code another contributor handed back, if any). It starts `candidate.cpp` from `previous.cpp`, else `starter.cpp`.
3. Write the body in `work/<VA>/candidate.cpp`. Follow the packet's name, flags and rules, and the kit's short `CONVENTIONS.md` reference. For struct members, `python iblis_check.py fields <Struct>` lists what verified sources put at each offset and `layout <Struct> 0x14 0x60` prints a padded struct with just those members; `decl <VA>` shows how verified sources declare any address. Keep concise notes about what you tried.
4. Run `python iblis_check.py check <VA>` and revise until it prints `MATCH`. Every check keeps its source in `history/` and says whether it was your closest yet; `best <VA> --restore` puts the closest back. `sxs <VA>` shows the remaining differences, aligned. To try several spellings at once, save each as `work/<VA>/variants/<name>.cpp` and run `sweep <VA>`. If stuck, consult the kit's `MATCHING.md`.
5. Call `submit` with these fields:

| Field | Value |
|---|---|
| `va` | Assigned address |
| `source` | Full text of `candidate.cpp`, exactly as checked |
| `local_status` | `RAW_EQUAL` or `RELOCATION_RESOLVED_EQUAL`, from the checker |
| `body_sha256` | Hash printed by the checker |
| `notes` | Short explanation of the approach and anything the verifier should know |
| `model` | Model/tool used |

Submission stores the C++ and notes in the service as `pending`. Check `status` for the host's verdict; a local MATCH is not final acceptance. Then call `next` for more work.

If stuck or nearing expiry, call `giveup` with `va`, a short `reason`, and `source` containing your best C++. It becomes the next contributor's starting point.

## Essential rules

- Submit readable C++ and notes only. Keep game files, disassembly, briefs, object files, binaries and keys local.
- Use the assigned compiler flags. No inline assembly, byte emission, naked functions, byte-array bodies, optimization pragmas, or alternate copies of the target function.
- Follow the limits returned by `status`. Give up work you cannot finish rather than letting the reservation expire.
- On setup failure or a failed host verdict, stop and recheck with `selftest`. On `401`, reconnect; on `429`, wait until the service's stated time. A leaked key must be revoked on `/account.html`.
