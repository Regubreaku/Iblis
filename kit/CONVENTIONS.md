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
- Preserve established names. For unknown functions use `sub_<VA>_<Verb>`; unknown globals can include their address in the name.
- Use ordinary C++: no inline assembly, raw byte emission, naked functions, byte-array bodies, optimization pragmas or changed compiler flags. Keep one version of the target per check.
- Run `python iblis_check.py check <VA>`. Submit the exact checked source only after MATCH, with its status and body hash.
- Keep explanations in short prose. Briefs, disassembly and compiled output stay local.

If a declaration or layout is uncertain, explain it in the notes. The project host recompiles every submission independently.
