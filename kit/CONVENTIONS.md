# Source conventions (what the matched bodies look like)

- Structs are `D2<Thing>Strc` (`D2UnitStrc`, `D2GameStrc`, `D2DrlgRoomStrc`), from `headers/`. Fields you don't
  know stay as `int unk_<off>;` padding; never reorder a frozen header.
- Functions: `MODULE_Verb` when the name is established (`DATATBLS_ApplyRatio`, `ITEMS_GetItemType`), else
  `sub_<VA>_<Verb>` (`sub_0051E490_ExtractArchiveDll`). Globals: `gn…`/`gp…`/`gsz…` plus `_<VA>` when unnamed
  (`gnGlobal_007A285C`). Addresses in names are 1.14d addresses; 1.13c-style names (`…_6FD447F8`) are legacy.
- Every declaration carries a trailing `// 0xVA`; the definition has `// 0xVA` on the line above.
- `__fastcall` is the house convention for most game code (D2Common/D2Game); UI and Storm code is often
  `__stdcall` or `__thiscall` methods; the brief says which registers the body reads first.
- Asserts: `if (!p) { FOG_Assert("p", gszSourceFile, __LINE__); }` style with the file-name string declared as
  `extern const char gszSomeSourceFile[]; // 0x006DF338`. The source-file address is in the brief's Strings.
- Storm list/hash helpers are tiny templates; copy the matched example rather than re-deriving them.
- Jump tables and float constants are the compiler's; don't try to place them, just write the `switch`/`float`.
