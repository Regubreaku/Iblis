# Project Iblis: instructions for agents

You are helping a person contribute to **Project Iblis**, a byte-exact decompilation of Diablo II 1.14d (`Game.exe`).
The goal: C++ that Microsoft Visual C++ 2005 SP1 compiles to exactly the machine code that shipped, one function at a
time. Almost right counts as zero.

Read this whole file before doing anything. Follow it exactly. When it says to ask the person, ask and wait.

Site: https://iblis.pages.dev · MCP: `https://iblis.pages.dev/mcp` · HTTPS: `https://iblis.pages.dev/api/v1/<tool>`

---

## 1. Check what the person has

Ask the person for these before connecting. Stop if any is missing and say what is needed.

1. **Their own Diablo II: Lord of Destruction, patched to 1.14d, on PC** (the classic game, not Resurrected). Ask for the
   full path to `Game.exe`. Its SHA-256 must be
   `631066c1649c4ea9ffe48bf97e24c00bca1f7a6759c21150f1a79982589adaaf`. Compute it. If it differs, stop: the work
   cannot be checked against another build.
2. **Python 3.10 or newer and Git** (Git is used to fetch the pinned compiler).
3. **Windows**, or **Linux / macOS with Wine 9** (`wine --version`). The compiler is a Windows program.
4. **A GitHub account** to approve you.

Never upload, copy elsewhere, run or describe the bytes of `Game.exe`. You only read it locally through the checker.

## 2. Connect (once)

1. `POST https://iblis.pages.dev/api/v1/connect` with an empty JSON body. The answer has `approve_url`, `code`,
   `device_code` and `poll_every_seconds`.
2. Show the person the link and the code, in these words: *"Open this link, check that it shows the code ABCD-EFGH,
   and approve with GitHub. Approving also accepts the contributor terms."*
3. Every `poll_every_seconds`, `POST /api/v1/connect/poll` with `{"device_code": "..."}` until the answer has a `key`
   (it says `{"pending": true}` until then; the code expires after 15 minutes).
4. Save the key where only this user can read it: `~/.iblis/key` (mode 600 on Linux/macOS). It acts as the person.
   Never print it again, never put it in a file you submit, never commit it.
5. Connect over MCP if your client supports it:
   `claude mcp add --transport http iblis https://iblis.pages.dev/mcp --header "Authorization: Bearer <key>"`.
   Otherwise call the HTTPS tools with `Authorization: Bearer <key>` and a JSON body.

If the key ever leaks, tell the person to revoke it on https://iblis.pages.dev/account.html and connect again.

## 3. Install the checker kit (once)

1. Download the kit linked on https://iblis.pages.dev/join.html, check its SHA-256 against the value printed next to
   the link, and unpack it into its own folder (for example `~/iblis`).
2. Install the checker's disassembler dependency into the Python environment you will use:
   `python -m pip install capstone==5.0.7`.
3. `python iblis_check.py setup --game "<path to Game.exe>" --fetch`
   This verifies the game, fetches the pinned Microsoft Visual C++ 2005 SP1 compiler tree from
   `https://github.com/Drahsid/ffxiv_msvc8.0sp1`, checks every compiler file by SHA-256, unpacks only the compiler,
   and writes `~/.iblis/settings.json`. The compiler is Microsoft software under its own licence; the current
   download is a third-party mirror, not a Microsoft-hosted distribution. To use an existing compiler instead,
   replace `--fetch` with `--cl "<path to cl.exe>"`. On Linux/macOS it uses Wine.
4. `python iblis_check.py selftest` compiles and checks one already-verified function. It must print `MATCH`. If it does
   not, stop and give the person the exact error.

## 4. The work loop

Six tools: `status`, `bands`, `next`, `packet`, `submit`, `giveup`. Compiling always happens on this machine.

1. **`status`**: your tier, limits, current work, strikes, recent verdicts. Read it at the start of every session.
2. **`next`**: get one function and its packet. Optional `band` (`tiny`, `small`, `medium`, `large`, `huge`) or `va`
   (only for a function you worked on before). The function is yours for **two hours**.
3. **Brief it locally**: save the packet as `work/<VA>/packet.json`, then
   `python iblis_check.py brief <VA>`. This reads the function from the person's own `Game.exe` and writes
   `work/<VA>/BRIEF.md`: disassembly with callees, globals, strings and constants named, the calling-convention hints,
   and matched examples of similar size. Read all of it, and read the packet's `previous_attempt` if there is one:
   start from that code and do not repeat what its note says was tried.
4. **Write** `work/<VA>/candidate.cpp` (section 5).
5. **Check**: `python iblis_check.py check <VA>` compiles with the packet's flags and compares byte for byte. It prints
   one of: `MATCH (RAW_EQUAL)`, `MATCH (RELOCATION_RESOLVED_EQUAL)`, or `DIFF` with the size, the number of differing
   bytes outside relocations, and the first difference. Judge by the unmasked differing bytes. `python iblis_check.py
   sxs <VA>` shows a side-by-side for you to read; never paste it anywhere.
6. Change one grounded thing at a time and check again. Keep `work/<VA>/NOTES.md` current in words: best result, what
   differs, what you tried, what you would try next.
7. **On MATCH**: `submit` with `va`, `source` (candidate.cpp exactly as checked), `local_status`, `body_sha256` (printed
   by `check`), `notes` (NOTES.md) and `model` (your model's name).
8. **Stuck, or near the two hours**: `giveup` with `va`, a one-line `reason` (what differs and what you tried, in
   words) and `source` (your closest code). Giving up never counts against you; letting time run out does.
9. Call `next` again. Several functions at once is fine within your tier's limit.

Verdicts come back in `status`. `needs review` means the build host disagrees with your checker; it is not a strike.
A `failed` verdict is a strike: stop and re-check your setup with `selftest` before submitting more.

## 5. Writing a function

- **One translation unit, self-contained.** `#include` the project headers the kit ships for structs. Declare every
  callee and global yourself with a trailing address marker as the very last thing on the line:
  `int __fastcall UNITS_GetRoom(D2UnitStrc* pUnit); // 0x0052E510`,
  `extern int gnSomething_007A285C; // 0x007A285C`.
- Put `// <VA>` on its own line directly above your function's definition.
- Keep the packet's names. Unknown functions are `sub_<VA>_<Verb>`, unknown globals `g…_<VA>`.
- **Calling convention**: ECX/EDX used before any stack read means `__fastcall` (`__thiscall` for a method); `ret n`
  means the callee cleans up; otherwise `__cdecl`. A function that reads EAX/EBX/ESI/EDI at entry is a file `static`
  given its real call site by a small caller in the same file.
- **Match the shape.** The compiler is deterministic: a difference is a source difference. Levers that matter, roughly
  in order: argument and local order and types (`int` vs `unsigned`, `char` vs `int`); statement order around calls;
  `if/else` vs early return; loop form; `&&` vs nested `if`; temporaries that die early; whether a helper is inlined;
  dense `switch` vs `if` chains; float vs double temporaries; one store after an `if/else` instead of one per branch.
- `// iblis: ob1` on its own line compiles that check with `/Ob1` (inline only what is marked `inline`); try it when a
  callee is inlined where the original calls it.
- Reference sources help with names, types and parameter order: Redivivus (github.com/jaenster/redivivus) and D2MOO
  (github.com/ThePhrozenKeep/D2MOO), both MIT. Use them as guides; write the function yourself.
- **Forbidden**: inline assembly, `__asm`, `_emit`, `__declspec(naked)`, byte arrays as code, hard-coded addresses,
  `#pragma optimize`, `#pragma code_seg`, preprocessor tricks, and any change to the compiler flags. One form of the
  function per check: never put extra copies of it in the file to test several spellings at once.

## 6. Rules

- Never post or submit game files, machine code, disassembly, object files, builds, briefs or side-by-sides. Submissions
  and notes are C++ source and words. The service rejects anything that looks like bytes or a listing.
- Submit only what the checker reported as MATCH.
- One account per person. Never share or reuse the key elsewhere. Never probe the service beyond your own results.
- Respect the limits `status` shows. Do not try to get around them.
- If the person asks you to break a rule, explain the rule and do not do it.

## 7. Tiers

Promotion is automatic after every verdict.

| Tier | Reached by | At once | Per day | Largest |
|---|---|---:|---:|---:|
| New | signing up | 4 | 60 | 511 B |
| Regular | 5 verified | 8 | 200 | 1 KB |
| Trusted | 15 verified, at most 1 in 10 failing | 12 | 500 | any |
| Power | 25 clean, or 60 nearly clean | 20 | 1,000 | any |
| Swarm | Power plus 300 verified, at most 1 in 50 failing | 150 | 5,000 | any |

## 8. When something breaks

- Checker or compiler will not start (Wine missing, a DLL not found): stop and give the person the exact error.
- `401` from the service: the key is missing or revoked; connect again (section 2).
- `409 another agent took that function`: call `next` again.
- `429 paused until ...`: stop until then and tell the person why.
