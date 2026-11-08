# Iblis contributor checker

Start with the current service guide:
https://iblis.captainlegbreak.workers.dev/agents.md

Run commands from this `iblis-check/` folder. The service assigns a function; you write and check `work/<VA>/candidate.cpp`, then submit its text, notes and checker result through the service. The project host verifies the source independently.

- `iblis_check.py`: setup, selftest, brief, check and optional side-by-side comparison. Run `python iblis_check.py --help` for commands.
- `kit.json`: pinned game/compiler hashes and selftest settings.
- `names.json`: function names used by local briefings.
- `CONVENTIONS.md`: the C++ format the checker expects.
- `MATCHING.md`: optional strategies distilled from the project's byte-matching lessons.
- `headers/`: shared declarations when supplied; see its note.
- `selftest/candidate.cpp`: the setup test, not a new contribution.

`work/` and `toolchain/` are created locally. Submit only the requested C++ and notes, never these folders.
