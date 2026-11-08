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
