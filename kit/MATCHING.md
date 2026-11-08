# Byte-matching playbook

Optional help when a candidate differs. These observations come from the project's MSVC 2005 SP1 matching work; they are hypotheses to test, not universal compiler rules. The current packet and checker decide what is accepted.

## Start from evidence

- Read the local brief and the packet's `previous_attempt` first. Preserve a useful candidate and avoid repeating changes its notes already ruled out.
- Use [Redivivus](https://github.com/jaenster/redivivus) and [D2MOO](https://github.com/ThePhrozenKeep/D2MOO) for established names, types, helper signatures and statement order. Both publish MIT-licensed source. Record the reference and retain applicable attribution when adapting it.
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

Small algebraic rewrites, added scopes or declaration reordering have often produced identical bodies. Check the result instead of cycling through cosmetic variants.

## When to hand it back

Keep the packet's flags. If evidence suggests an incorrect function extent, missing declaration, different optimization profile or unsupported helper context, use `giveup` with your best source and a concise explanation. Do not change compiler flags or submit a non-MATCH as verified.

A useful handoff says: what matched, what still differs, which ideas were tried, the best result, and the next concrete idea. Keep disassembly and game bytes local; send the C++ and prose notes through the service.
