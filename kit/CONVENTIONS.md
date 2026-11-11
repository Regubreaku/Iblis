# C++ submission format

Write one self-contained `work/<VA>/candidate.cpp` for the service's assigned function. Use the packet's function name and compiler flags.

## Address markers

Put `// <VA>` on its own line directly above the target definition. Declare each external callee and global with its real address as a trailing comment, at the end of the line:

```cpp
struct D2UnitStrc;
int __fastcall UNITS_GetRoom(D2UnitStrc* pUnit); // 0x0052E510
extern int gnGlobal_007A285C; // 0x007A285C
```

These are format examples, not declarations to copy into every submission. Take addresses, types and calling conventions from the packet and local brief. Use struct member declarations for `__thiscall` methods.

## Source and checks

- Use supplied headers when available. Otherwise include the declarations and layouts your function needs in `candidate.cpp`; do not assume the kit contains the full game's headers.
- Name the target by the rule below. Unknown globals can include their address in the name.
- Use ordinary C++: no inline assembly, raw byte emission, naked functions, byte-array bodies, optimization pragmas or changed compiler flags. Keep one version of the target per check.
- Run `python iblis_check.py check <VA>`. Submit the exact checked source only after MATCH, with its status and body hash.
- Keep explanations in short prose. Briefs, disassembly and compiled output stay local.

If a declaration or layout is uncertain, explain it in the notes. The project host recompiles every submission independently.

## Naming the target

The function defined at your address must take its name from one of these, and nothing else:

- `names.json` (this kit's copy): the address's own name, or any name the file lists. When the packet gives a name, use it.
- [D2MOO](https://github.com/ThePhrozenKeep/D2MOO)'s function names (an identifier that appears in its source).
- Blizzard's own name lists: the game's exports, the Mac symbol lists, and the D2CMP names (bare or written `D2CMP_<name>`).
- `sub_<address>` or `sub_<address>_<Verb>`, with the target's own eight-digit address, e.g. `sub_00401220_GetRoom`.

Constructors, destructors and methods of a struct your file defines are named by their class and pass. The rule is
about the target; callees and globals keep the names you declare them with. Do not invent a module-style name such as
`UNITS_Something` that none of these lists carries: use `sub_<address>_<Verb>` instead.

The build host checks the target's name before it compiles. A name outside the rule comes back as `failed` with a
`NAME_POLICY` note (it counts like any failed verification). Rename the function and its uses in the file, run `check`
again (a rename changes no bytes) and submit again.
