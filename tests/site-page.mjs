// The home page's inline script, run against a stand-in DOM: the code-section bar and, beside it, the linked-executable
// figure built from stats.json's "relink" block (counts only, with the whole-file figure when the report measured it), and
// the page without one.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const page = readFileSync(new URL('../src/pages/index.html', import.meta.url), 'utf8');
const script = page.slice(page.lastIndexOf('<script>') + '<script>'.length, page.lastIndexOf('</script>'));

const stats = () => ({
  generated_utc: '2026-11-10T09:00Z', text_bytes: 3000, matched_functions: 12, functions_total: 20, matched_bytes: 2000,
  matched_pct_text: 66.67, states: { matched: { functions: 12, bytes: 2000 }, open: { functions: 5, bytes: 20 }, parked: { functions: 3, bytes: 400 } },
  modules: [], leaderboard: [{ who: 'A', functions: 12, bytes: 2000 }], by_model: [{ model: 'codex', functions: 12, bytes: 2000 }],
  trend: [{ day: '2026-11-09', functions: 12, bytes: 2000, cum_functions: 12, cum_bytes: 2000 }],
  left_by_size: [{ band: 'tiny', range: [0, 64], functions: 8, bytes: 420, tried: 3 }],
  relink: {
    pct_text: 83.63, identical_bytes: 2509, bytes: 3000, milestone: 4, generated_utc: '2026-11-10', commit: 'abc1234',
    classes: [{ key: 'matched', label: 'Verified functions', equal: 2000, different: 10 }],
    remaining: [{ key: 'unverified', label: 'Unverified functions (open, parked or submitted)', bytes: 464 },
                { key: 'unplaced', label: 'Verified functions the linker cannot place yet', bytes: 10 },
                { key: 'library', label: 'Library data addresses, thunks and fragments', bytes: 16 },
                { key: 'padding', label: 'Padding', bytes: 1 }],
    // the whole file (milestone 6): built from source, the game's data copied until it is reconstructed, not yet
    file: {
      bytes: 4000, built_bytes: 2800, copied_bytes: 600, identical_bytes: 3400, pct_built: 70, pct_identical: 85, pct_signature: 1.95,
      segments: [{ key: 'built', label: 'Built from source', bytes: 2800 }, { key: 'copied', label: 'Identical, copied data', bytes: 600 },
                 { key: 'rest', label: 'Not yet', bytes: 600 }],
      remaining: [{ key: 'placeholder', label: 'Placeholders for parked and unverified functions', bytes: 500 },
                  { key: 'linker', label: 'Linker and library bytes that still differ', bytes: 22 },
                  { key: 'signature', label: 'Authenticode signature, never reproducible', bytes: 78 }],
      classes: [], sections: [{ name: '.text', bytes: 3000, built: 2509, copied: 0, identical: 2509 }],
    },
  },
});

// enough of a DOM for the script: elements by id with innerHTML, textContent, attributes, hidden and classList
function render(S) {
  const els = new Map();
  const el = id => {
    if (!els.has(id)) {
      const e = { id, innerHTML: '', textContent: '', hidden: id === 'linked' || id === 'legend-note' || id === 'linked-also', attrs: {}, classes: new Set(),
        setAttribute(k, v) { this.attrs[k] = v; }, classList: { add: c => e.classes.add(c), contains: c => e.classes.has(c) } };
      e.tBodies = [e];
      els.set(id, e);
    }
    return els.get(id);
  };
  const ctx = { window: { IBLIS: S }, document: { getElementById: el, body: { dataset: {} } }, Intl, Math, String, Object, Array };
  vm.runInNewContext(script, ctx);
  return el;
}

test('the linked-executable tile leads with the whole file: built from source, identical, and the code section second', () => {
  const $ = render(stats());
  assert.equal($('linked').hidden, false);
  assert.equal($('linked-pct').textContent, '70.00%');
  assert.equal($('linked-what').innerHTML, 'of Game.exe built from source, <b>85.00%</b> identical');
  assert.equal($('linked-also').hidden, false);
  assert.match($('linked-also').innerHTML, /Code section: <b>83\.63%<\/b> identical in the linked image\./);
  const track = $('linked-track').innerHTML;
  assert.match(track, /class="g" style="width:70\.000%"/);
  assert.match(track, /class="k" style="width:15\.000%"/);
  assert.match(track, /class="r" style="width:15\.000%"/);
  assert.equal(track.match(/<i /g).length, 3);
  assert.match($('linked-track').attrs['aria-label'], /^Linked executable, whole file: 70\.00% built from source, 85\.00% identical to the original; Built from source 70\.0%, Identical, copied data 15\.0%, Not yet 15\.0%/);
  const legend = $('linked-legend').innerHTML;
  assert.match(legend, /<i class="g"><\/i>Built from source&nbsp;<b>70\.0%<\/b>/);
  assert.match(legend, /<i class="k"><\/i>Identical, copied data&nbsp;<b>15\.0%<\/b>/);
  assert.match(legend, /class="ngame"><i class="r"><\/i>Not yet&nbsp;<b>15\.0%<\/b>/);
  const note = $('linked-note').innerHTML;
  assert.match(note, /^The whole 4,000 B file/);
  assert.match(note, /Copied is the game's data tables and strings, identical but not yet reconstructed from source \(15\.0%\)/);
  assert.match(note, /not yet is placeholders for parked and unverified functions \(12\.5%\) and linker and library bytes that still differ \(0\.6%\)/);
  assert.match(note, /the 1\.95% signature can never be rebuilt\. Measured 2026-11-10 \(milestone 4\)\./);
  assert.ok(!$('bars2').classList.contains('one'));
  assert.match($('log').innerHTML, /<time>2026-11-10<\/time>Whole executable linked \(milestone 4\): 83\.63% of the code section byte-identical in the linked image; of the whole file, 70\.00% built from source and 85\.00% identical\./);
  assert.doesNotMatch($('linked-note').innerHTML + $('log').innerHTML + $('linked-what').innerHTML, /0x[0-9A-Fa-f]{6}/, 'no addresses on the page');
});

test('a relink block without the whole-file measurement shows the code-section headline as before', () => {
  const S = stats();
  delete S.relink.file;
  const $ = render(S);
  assert.equal($('linked').hidden, false);
  assert.equal($('linked-pct').textContent, '83.63%');
  assert.equal($('linked-what').innerHTML, '');  // the markup's own "of the code section identical" stays
  assert.equal($('linked-also').hidden, true);
  assert.match($('linked-track').innerHTML, /class="g" style="width:83\.633%"/);
  assert.match($('linked-track').innerHTML, /class="r" style="width:16\.367%"/);
  assert.match($('linked-track').attrs['aria-label'], /^Linked executable: 83\.63% of the code section identical/);
  assert.match($('linked-legend').innerHTML, /Identical&nbsp;<b>83\.6%<\/b>/);
  assert.match($('linked-legend').innerHTML, /Not yet&nbsp;<b>16\.4%<\/b>/);
  const note = $('linked-note').innerHTML;
  assert.match(note, /unverified functions, open, parked or submitted \(15\.5%\)/);
  assert.match(note, /library data addresses, thunks and fragments \(0\.5%\)/);
  assert.match(note, /cannot place yet \(0\.3%\) and padding \(&lt;0\.1%\)/);
  assert.match(note, /measured 2026-11-10 \(milestone 4\)\./);
  assert.ok(!$('bars2').classList.contains('one'));
  assert.match($('log').innerHTML, /<time>2026-11-10<\/time>Whole executable linked \(milestone 4\): 83\.63% of the code section byte-identical in the linked image\./);
  assert.doesNotMatch($('linked-note').innerHTML + $('log').innerHTML, /0x[0-9A-Fa-f]{6}/, 'no addresses on the page');
});

test('without a relink block the tile stays hidden and the code-section bar takes the row', () => {
  const S = stats();
  delete S.relink;
  const $ = render(S);
  assert.equal($('linked').hidden, true);
  assert.equal($('linked-pct').textContent, '');
  assert.ok($('bars2').classList.contains('one'));
  assert.doesNotMatch($('log').innerHTML, /executable linked/);
  assert.equal($('pct').textContent, '66.67%');
});

test('a relink block measured against another image size still renders from its own totals', () => {
  const S = stats();
  delete S.relink.file;
  S.relink.remaining = [];
  S.relink.generated_utc = null;
  S.relink.milestone = null;
  const $ = render(S);
  assert.equal($('linked').hidden, false);
  assert.match($('linked-note').innerHTML, /and padding\.$/);
  assert.doesNotMatch($('linked-note').innerHTML, /measured|milestone/);
});
