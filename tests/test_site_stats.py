import copy
import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))


class SiteStatsTests(unittest.TestCase):
    def normalize(self, stats):
        self.assertIsNotNone(importlib.util.find_spec('site_stats'), 'Site attribution normalization is missing')
        from site_stats import normalize_stats
        return normalize_stats(stats)

    def test_contributor_aliases_merge_without_losing_credit(self):
        stats = {'leaderboard': [
            {'who': 'lead', 'functions': 12436, 'bytes': 1614773},
            {'who': 'leg', 'functions': 700, 'bytes': 179633},
            {'who': 'fable', 'functions': 25, 'bytes': 108247},
            {'who': 'esk', 'functions': 116, 'bytes': 40172},
            {'who': 'angel', 'functions': 16, 'bytes': 927},
            {'who': 'codex', 'functions': 13, 'bytes': 4377},
        ], 'by_model': []}
        result = self.normalize(stats)
        self.assertEqual(result['leaderboard'], [
            {'who': 'Legbreak', 'functions': 13161, 'bytes': 1902653},
            {'who': 'Esk', 'functions': 116, 'bytes': 40172},
            {'who': 'Nathan', 'functions': 13, 'bytes': 4377},
            {'who': 'Angel', 'functions': 16, 'bytes': 927},
        ])
        self.assertEqual(sum(row['functions'] for row in result['leaderboard']), 13306)
        self.assertEqual(sum(row['bytes'] for row in result['leaderboard']), 1948129)

    def test_lead_model_credit_is_merged_once_even_after_rebuild(self):
        stats = {'leaderboard': [], 'by_model': [
            {'model': 'lead', 'functions': 12436, 'bytes': 1614773},
            {'model': 'claude-opus-5-5', 'functions': 480, 'bytes': 261944},
            {'model': 'codex', 'functions': 276, 'bytes': 56652},
        ], 'matched_functions': 13306, 'matched_bytes': 1948129}
        result = self.normalize(stats)
        self.assertEqual(result['by_model'], [
            {'model': 'claude-opus-5-5', 'functions': 12916, 'bytes': 1876717},
            {'model': 'codex', 'functions': 276, 'bytes': 56652},
        ])
        self.assertEqual(result['matched_functions'], 13306)
        self.assertEqual(result['matched_bytes'], 1948129)
        self.assertEqual(self.normalize(copy.deepcopy(result)), result)

    def test_existing_canonical_names_and_empty_lists_are_supported(self):
        result = self.normalize({'leaderboard': [
            {'who': 'Legbreak', 'functions': 1, 'bytes': 20},
            {'who': 'LEG', 'functions': 2, 'bytes': 30},
            {'who': 'newPerson', 'functions': 1, 'bytes': 10},
        ], 'by_model': []})
        self.assertEqual(result['leaderboard'], [
            {'who': 'Legbreak', 'functions': 3, 'bytes': 50},
            {'who': 'NewPerson', 'functions': 1, 'bytes': 10},
        ])
        self.assertEqual(self.normalize({'leaderboard': [], 'by_model': []}), {'leaderboard': [], 'by_model': []})


class CodeSectionBarTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

    def coverage(self, **over):
        cats = {'matched': 2000, 'submitted': 50, 'claimed': 30, 'open': 20, 'parked': 400, 'not_function': 10,
                'padding': 300, 'crt_library': 180, 'third_party': 0, 'data': 0, 'missing_game': 0}
        cats.update(over)
        return {'generated_utc': '2026-11-10T09:00Z', 'section': {'start': '0x00401000', 'end': hex(0x401000 + sum(cats.values()))},
                'bytes': cats}

    def stats(self):
        return {'text_bytes': 3000, 'matched_bytes': 2000,
                'states': {'matched': {'bytes': 2000}, 'claimed': {'bytes': 30}, 'submitted': {'bytes': 50},
                           'open': {'bytes': 20}, 'parked': {'bytes': 400}}}

    def test_not_game_code_is_split_out_and_shares_cover_the_whole_section(self):
        from site_stats import code_section, load_coverage
        cov = load_coverage(self.coverage())
        self.assertEqual(cov['section_bytes'], 2990)
        cs = code_section(self.stats(), cov)
        seg = {s['key']: s for s in cs['segments']}
        self.assertEqual([s['key'] for s in cs['segments']], ['matched', 'pending', 'parked', 'never', 'padding', 'library', 'other'])
        self.assertEqual(seg['pending']['bytes'], 80)
        self.assertEqual(seg['padding']['bytes'], 310)  # 300 measured + 10 B of section tail past the measured range
        self.assertEqual(seg['library']['label'], 'Not game code: compiler runtime library')
        self.assertEqual(seg['other']['bytes'], 10)
        self.assertEqual(seg['never']['bytes'], 20)
        self.assertEqual(cs['never_tried_measured'], 20)
        self.assertEqual(sum(s['bytes'] for s in cs['segments']), 3000)
        self.assertTrue(all(not s['game'] for s in cs['segments'][4:]))

    def test_coverage_formats_and_validation(self):
        from site_stats import load_coverage
        flat = {k: {'bytes': v} for k, v in self.coverage()['bytes'].items()}
        self.assertEqual(load_coverage(flat)['bytes']['padding'], 300)
        self.assertIsNone(load_coverage(flat)['range'])
        bad = self.coverage()
        bad['section']['end'] = '0x00402000'
        with self.assertRaises(ValueError):
            load_coverage(bad)
        with self.assertRaises(ValueError):
            load_coverage({'bytes': {'padding': -1}})
        with self.assertRaises(ValueError):
            load_coverage({'whatever': 1})

    def test_empty_categories_are_omitted_and_third_party_relabels_the_library(self):
        from site_stats import code_section, load_coverage
        cs = code_section(self.stats(), load_coverage(self.coverage(not_function=0, third_party=10)))
        keys = [s['key'] for s in cs['segments']]
        self.assertNotIn('other', keys)
        self.assertEqual(cs['segments'][-1]['label'], 'Not game code: runtime and other libraries')

    def test_host_measurement_format(self):
        from site_stats import code_section, load_coverage
        doc = {'text': {'start': '0x00401000', 'end': '0x00401BB8', 'bytes': 3000}, 'computed_utc': '20261110',
               'queue_bytes': {'matched': 2000, 'submitted': 50, 'claimed': 30, 'open': 20, 'parked': 400, 'not_function': 10},
               'padding_bytes': 300, 'unqueued_nonpadding_bytes': 190,
               'categories': {'a_crt_compiler_helper': {'bytes': 180, 'runs': 2, 'ranges': [['0x1', '0x2', 'x', 'y']]},
                              'b_third_party_library': {'bytes': 0}, 'c_data_or_tables': {'bytes': 9},
                              'd_game_function_missing': {'bytes': 1}}}
        cov = load_coverage(doc)
        self.assertEqual(cov['generated_utc'], '2026-11-10')
        self.assertEqual((cov['bytes']['crt_library'], cov['bytes']['data'], cov['bytes']['missing_game']), (180, 9, 1))
        cs = code_section(self.stats(), cov)
        self.assertEqual(cs['split'], 'coverage')
        self.assertEqual({s['key']: s['bytes'] for s in cs['segments']}['never'], 21)
        self.assertNotIn('ranges', repr(cs))

    def test_stale_coverage_keeps_queue_figures_and_takes_never_tried_as_remainder(self):
        from site_stats import code_section, load_coverage
        stats = self.stats()
        stats['matched_bytes'] = stats['states']['matched']['bytes'] = 2010
        stats['states']['open']['bytes'] = 10
        cs = code_section(stats, load_coverage(self.coverage()))
        seg = {s['key']: s['bytes'] for s in cs['segments']}
        self.assertEqual(cs['split'], 'queue')
        self.assertEqual(seg['matched'], 2010)
        self.assertEqual(seg['never'], 10)
        self.assertEqual(sum(seg.values()), 3000)

    def test_home_page_renders_the_split_bar(self):
        page = (Path(__file__).resolve().parents[1] / 'src' / 'pages' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('S.code_section', page)
        self.assertIn('Every share is of the whole code section', page)


class IntakeSwitchTests(unittest.TestCase):
    def test_invite_notices_follow_the_invite_list(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
        import build_site
        page = '# Guide\n\n{{#invite}}Invite-only.{{/invite}}\n\nWrite C++.\n'
        self.assertEqual(build_site.invite_blocks(page, True), '# Guide\n\nInvite-only.\n\nWrite C++.\n')
        self.assertEqual(build_site.invite_blocks(page, False), '# Guide\n\nWrite C++.\n')

    def test_pause_notices_follow_the_service_gate(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
        import build_site
        page = '# Guide\n\n{{#paused}}Intake is paused.{{/paused}}\n\nWrite C++.\n'
        self.assertEqual(build_site.pause_blocks(page, False), '# Guide\n\nIntake is paused.\n\nWrite C++.\n')
        self.assertEqual(build_site.pause_blocks(page, True), '# Guide\n\nWrite C++.\n')
        for src in ('src/agents.md', 'src/pages/join.html'):
            text = (Path(__file__).resolve().parents[1] / src).read_text(encoding='utf-8')
            self.assertNotIn('paused', build_site.pause_blocks(text, True).split('Two hours per function')[0].lower().replace('pauses new work', ''))


class BoardNameTests(unittest.TestCase):
    def test_site_contributors_are_anonymous_except_the_owner(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
        from site_stats import normalize_stats
        stats = {'leaderboard': [{'who': 'leg', 'functions': 2, 'bytes': 20}, {'who': 'iblis-120912283', 'functions': 1, 'bytes': 3},
                                 {'who': 'iblis-555', 'functions': 1, 'bytes': 5}, {'who': 'esk', 'functions': 1, 'bytes': 4}],
                 'by_model': []}
        rows = {r['who']: r['functions'] for r in normalize_stats(stats)['leaderboard']}
        self.assertEqual(rows, {'Legbreak': 3, 'Anonymous': 1, 'Esk': 1})


if __name__ == '__main__':
    unittest.main()
