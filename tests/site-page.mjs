// The home page's inline script, run against a stand-in DOM: the code-section bar and, beside it, the linked-executable
// figure built from stats.json's "relink" block (counts only), and the page without one.
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
  },
});

// enough of a DOM for the script: elements by id with innerHTML, textContent, attributes, hidden and classList
function render(S) {
  const els = new Map();
  const el = id => {
    if (!els.has(id)) {
      const e = { id, innerHTML: '', textContent: '', hidden: id === 'linked' || id === 'legend-note', attrs: {}, classes: new Set(),
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

test('the linked-executable tile shows the relink headline beside the code-section bar', () => {
  const $ = render(stats());
  assert.equal($('linked').hidden, false);
  assert.equal($('linked-pct').textContent, '83.63%');
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
  assert.match($('log').innerHTML, /<time>2026-11-10<\/time>Whole executable linked \(milestone 4\): 83\.63% of the code section byte-identical/);
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
  S.relink.remaining = [];
  S.relink.generated_utc = null;
  S.relink.milestone = null;
  const $ = render(S);
  assert.equal($('linked').hidden, false);
  assert.match($('linked-note').innerHTML, /and padding\.$/);
  assert.doesNotMatch($('linked-note').innerHTML, /measured|milestone/);
});
