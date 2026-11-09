# Byte-matching playbook

Optional help when a candidate differs. These observations come from the project's MSVC 2005 SP1 matching work; they are hypotheses to test, not universal compiler rules. The current packet and checker decide what is accepted.

## Start from evidence

- Read the local brief and the packet's `previous_attempt` first. Preserve a useful candidate and avoid repeating changes its notes already ruled out.
- Use [D2MOO](https://github.com/ThePhrozenKeep/D2MOO) (MIT) for established names, types, helper signatures and statement order. Credit it in your notes when you start from it; it is a guide, not a match.
- Try the reference's literal C++ structure before inventing a new one. Its enum names, conditional expressions and helper parameter order have often mattered.
- Change one evidence-backed idea per check. `check` keeps every source in `history/` and `best --restore` returns to the closest; `sweep` compiles a folder of variants and names the ones that produce the same body, which is not progress.
- Look up before guessing: `decl <VA>` for a callee's verified signature (convention, parameter types and order decide registers), `fields <Struct>` and `layout` for offsets. They are what verified functions used, which is evidence, not proof.

## Diagnose the difference

| What differs | Useful things to inspect |
|---|---|
| Registers or extra spills | Helper parameter order/types, named temporaries, number of uses, statement order around calls |
| Loads, stores or field offsets | Struct layout and padding, field width, signedness, global declarations, ordering of assignments |
| Branches or extra tests | Early returns versus one result/return, conditional expressions versus if/else, loop test placement, switch shape |
| Stack slots | Variable lifetimes, reused locals, address-taken values, buffer size and struct-by-value arguments |
| Inlining or exception setup | Real helper context, accurate calling/exception declarations, then whether the packet's profile needs host review |

Use `python iblis_check.py sxs <VA>` to inspect differences locally. Judge the check by its status, compiled length and differing bytes outside relocations. A first differing offset alone can mislead: one earlier size change shifts later jumps.

## Strategies that have helped

- **Types and signatures:** restoring a helper's parameter order or a byte-sized argument has corrected register allocation across a whole function. A free function cannot be declared `__thiscall`; use a member declaration for a real method.
- **Locals and statement order:** removing a temporary, computing a value after a call, or assigning fields in their original order can avoid spills and alter register choices. A shared store after an if/else can compile differently from a store in each branch.
- **Control flow:** a result local with one return, a literal enum comparison, or a top-tested versus bottom-tested loop has resolved near matches. Equivalent C++ does not necessarily produce equivalent bytes.
- **Layouts:** validate actual offsets rather than guessing padding. Adjacent globals represented together sometimes need a leading field to put the second at the correct offset. Never change a layout solely to force a match without evidence.
- **Globals and qualifiers:** `static` or `volatile` can change reloads, stores and register allocation. Use them only when the function's behavior and context justify them, not as blanket fixes.
- **Helpers:** the compiler's knowledge of a real same-file callee can affect its caller. Explain necessary helper context in your notes; do not fabricate stand-in bodies or add alternate target versions to trick the check. Report a rejected context body rather than padding it to evade detection.

## Register arguments (EAX, EBX, ESI, EDI)

A function that reads EAX, EBX, ESI or EDI before setting it, or a callee that receives a value in one of them, is
not a dead end. With this profile MSVC gives a `static` function whose every call it can see a register convention of
its own, including EBX, ESI and EDI parameters, and the original was built the same way.

- **The target takes register values:** write it as a `static` function (keep `// <VA>` directly above it) and add one
  caller in the same file with that caller's own `// 0x...` line. `python iblis_check.py callers <VA>` lists the call
  sites and the instructions leading up to each one.
- **A callee takes register values:** define that callee as a `static` function in your file with its own `// 0x...`
  line and a real attempt at its body. The checker verifies that your call lands on that address; the helper's body
  is context, not part of your compare. A real body is usually enough for the caller's setup to line up.
- **The static's body decides its registers.** `__stdcall`, `__fastcall` or `__cdecl` on it and what the caller looks
  like made no difference to the static's own code, and its parameter order usually made none either (once it moved a
  value from EDX to ECX, so it is worth a sweep). Small body changes do move them:
  writing through a struct field instead of a pointer local, one result variable through a switch instead of several
  returns, or reading a field into a local before the call (that swapped two incoming registers into the original's).
- **Parameter order still matters to the caller:** it sets the order the arguments are evaluated at the call site.
  When only the caller's setup differs, sweep the permutations of the static's parameters.
- A static with its own convention can come out without `push ebp` even under `/Oy-`, so a missing frame pointer
  alone does not mean a function needs a different profile.
- The compiler also knows which registers a static leaves alone: ECX and EDX can stay live across a call to a static
  that only uses EAX, as in the original.
- `/Ob2` inlines a small static into its caller; mark it `__declspec(noinline)` when the original calls it.
- There are no CRT headers, so `memset` and `= {}` compile to calls (which also clobber ECX and EDX). A plain element
  loop gives `rep stosd`; a short fixed loop gives unrolled stores.
- Open problem: a parameter and a local sometimes swap callee-saved registers (the original gives the local the first
  pick). No spelling has flipped it yet; note it in your give-up so the pattern can be studied.

Examples matched this way: `0x0064D060` (calls two such statics, grid in EAX, box in ECX); `0x004549F0` (target as a
static, text pointer in EBX); `0x006C3D20`, `0x006C42E0`, `0x006C4980`, `0x0065A710` (targets as statics, values in
EAX/ECX/EDX/ESI/EDI); `0x006C5A90` (target and callee both statics); `0x004DB2E0` (ordinary target, four static
callees); `0x00681560`, `0x006C50D0` (targets as statics); `0x00540DF0` (static callee, queue in EAX).

Small algebraic rewrites, added scopes or declaration reordering have often produced identical bodies. Check the result instead of cycling through cosmetic variants.

## Callee-saved register swaps (ESI, EDI, EBX)

When everything matches except that two values sit in each other's ESI/EDI/EBX, the original's source had different references or a different chain structure that produce the same instructions. From about 1,500 controlled compiles with this profile:

- **Order.** The highest-priority live range gets ESI, the next EDI, the third EBX. The order of the prologue pushes only shows which register was needed first.
- **Priority is a count of references in the optimised code.** Every surviving read, write, compare, push or address use counts once. Uses inside a loop count about 9 to 10 times. A use before the first branch outweighs one in a late block (about 3:1 when that block returns). A parameter's incoming value adds nothing, but a local's definition counts.
- **Ties** go to the value whose definition is emitted first, not to declaration order or first use in the source.
- **The unit is the def-use chain, not the variable.** Updating in place (`x += ...`, `n >>= 8`) keeps the references in one chain. Writing a result into a new local starts a second chain with its own priority.
- **Spellings the optimiser removes cannot flip a swap.** Declaration order, initialisers, copies of a parameter, taking an address, `else {}`, `goto`, a ternary or `do {} while (0)` that compile to the same code all leave it unchanged. Don't spend checks on them.

Levers:
- To move a value up (toward ESI), merge it with the value it is computed from or into: accumulate into the parameter (0x0057B420), or hold the raw call result across the call and update it in place.
- To move it down, put the computed result in a new local.
- On a tie, change which definition comes first.
- A double-evaluating clamp such as `MIN(MAX(n, lo), hi)` builds different chains (0x005BFC60), but it can also change the code.

## When to hand it back

Keep the packet's flags. If evidence suggests an incorrect function extent, missing declaration, different optimization profile or unsupported helper context, use `giveup` with your best source and a concise explanation. Do not change compiler flags or submit a non-MATCH as verified.

A useful handoff says: what matched, what still differs, which ideas were tried, the best result, and the next concrete idea. Keep disassembly and game bytes local; send the C++ and prose notes through the service.
