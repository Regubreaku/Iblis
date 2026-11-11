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


class LinkedExecutableTests(unittest.TestCase):
    """The relink figure: RELINK_COVERAGE.json's per-class equal/different byte counts, nothing else."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

    CLASSES = {'matched': {'equal': 2000, 'different': 10}, 'submitted': {'equal': 1, 'different': 49},
               'pending': {'equal': 5, 'different': 415}, 'not_function': {'equal': 4, 'different': 6},
               'thunk': {'equal': 20, 'different': 0}, 'crt': {'equal': 170, 'different': 10},
               'padding': {'equal': 309, 'different': 1}, 'other': {'equal': 0, 'different': 0}}

    def doc(self, **over):
        d = {'generated_by': 'tools/relink.py (milestone 4)', 'milestone': 4,
             'text_verify': {'text': {'original': ['0x00401000', hex(0x401000 + 3000)], 'relinked': ['0x00401000', hex(0x401000 + 3000)]},
                             'classes': copy.deepcopy(self.CLASSES), 'differing_ranges': 2, 'sections_equal': False,
                             'first_differing': [{'va': '0x00401090', 'length': 57, 'cls': 'matched'}],
                             'entry': {'original': '0x00682985', 'relinked': '0x00682985', 'equal': True}, 'iat_equal': True},
             'libraries': {'crt': {'first_differing': [{'va': '0x00681A48', 'length': 15, 'member': 'secchk.obj'}]}},
             'problem_examples': {'address_conflict': ['0x00401000']}}
        d.update(over)
        return d

    def test_counts_only_reach_the_block(self):
        from site_stats import load_relink, relink_block
        rl = load_relink(self.doc(), '2026-11-10', 'abc1234')
        self.assertEqual((rl['bytes'], rl['identical_bytes'], rl['pct'], rl['milestone']), (3000, 2509, 83.63, 4))
        self.assertEqual((rl['generated_utc'], rl['commit']), ('2026-11-10', 'abc1234'))
        self.assertEqual((rl['sections_equal'], rl['entry_equal'], rl['iat_equal']), (False, True, True))
        self.assertEqual([c['key'] for c in rl['classes']], ['matched', 'submitted', 'pending', 'not_function', 'thunk', 'crt', 'padding', 'other'])
        self.assertEqual(rl['classes'][0], {'key': 'matched', 'label': 'Verified functions', 'equal': 2000, 'different': 10})
        block = relink_block({'text_bytes': 3000}, rl)
        self.assertEqual(block['pct_text'], 83.63)
        self.assertEqual({r['key']: r['bytes'] for r in block['remaining']},
                         {'unverified': 464, 'unplaced': 10, 'library': 16, 'padding': 1})
        self.assertEqual(sum(r['bytes'] for r in block['remaining']) + block['identical_bytes'], 3000)
        text = repr(block)
        for leak in ('0x', 'first_differing', 'secchk', 'va', 'ranges', 'problem'):
            self.assertNotIn(leak, text)

    def test_validation(self):
        from site_stats import load_relink, relink_block
        with self.assertRaises(ValueError):
            load_relink({'milestone': 4})
        bad = self.doc()
        bad['text_verify']['text']['original'][1] = '0x00402000'
        with self.assertRaises(ValueError):
            load_relink(bad)
        bad = self.doc()
        bad['text_verify']['classes']['crt'] = {'equal': -1, 'different': 0}
        with self.assertRaises(ValueError):
            load_relink(bad)
        with self.assertRaises(ValueError):
            relink_block({'text_bytes': 2999}, load_relink(self.doc()))

    def test_top_level_classes_unknown_class_and_date_formats(self):
        from site_stats import load_relink
        classes = dict(self.CLASSES, seh={'equal': 1, 'different': 2})
        del classes['other']
        rl = load_relink({'classes': classes, 'computed_utc': '20261110'})
        self.assertEqual(rl['generated_utc'], '2026-11-10')
        self.assertIsNone(rl['milestone'])
        self.assertIsNone(rl['commit'])
        self.assertEqual(rl['classes'][-1], {'key': 'seh', 'label': 'Seh', 'equal': 1, 'different': 2})
        self.assertEqual(rl['bytes'], 3003)

    def test_build_stats_takes_the_file_date_and_commit_from_the_repo_copy(self):
        import json, subprocess, tempfile
        import build_stats
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            (repo / 'docs').mkdir(parents=True)
            (repo / 'docs' / 'RELINK_COVERAGE.json').write_text(json.dumps(self.doc()))
            env = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@x', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@x',
                   'GIT_AUTHOR_DATE': '2026-11-10T06:42:15-08:00', 'GIT_COMMITTER_DATE': '2026-11-10T06:42:15-08:00', 'PATH': '/usr/bin:/bin'}
            for cmd in (['init', '-q'], ['add', '.'], ['commit', '-q', '-m', 'relink']):
                subprocess.run(['git', '-C', str(repo)] + cmd, check=True, env=env)
            head = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
            self.assertEqual(build_stats.file_provenance(str(repo), str(repo / 'docs' / 'RELINK_COVERAGE.json')), ('2026-11-10', head))
            copy_ = Path(tmp) / 'host' / 'RELINK_COVERAGE.json'
            copy_.parent.mkdir()
            copy_.write_bytes((repo / 'docs' / 'RELINK_COVERAGE.json').read_bytes())
            self.assertEqual(build_stats.file_provenance(str(repo), str(copy_), 'docs/RELINK_COVERAGE.json'), ('2026-11-10', head))
            copy_.write_text(json.dumps(self.doc(milestone=5)))
            day, commit = build_stats.file_provenance(str(repo), str(copy_), 'docs/RELINK_COVERAGE.json')
            self.assertIsNone(commit)
            self.assertRegex(day, r'^\d{4}-\d{2}-\d{2}$')

    FILE = {'file': {'bytes': 4000, 'identical_built': 2800, 'identical_from_game_source': 2000, 'identical_including_copied': 3400,
                     'copied_bytes': 600, 'placeholder_bytes': 500,
                     'by_class': {'copied': {'equal': 600, 'different': 0}, 'library': {'equal': 300, 'different': 2},
                                  'linker': {'equal': 200, 'different': 20}, 'padding': {'equal': 300, 'different': 0},
                                  'placeholder': {'equal': 4, 'different': 496}, 'signature': {'equal': 0, 'different': 78},
                                  'source': {'equal': 2000, 'different': 0}}},
            'sections': {'.data': {'bytes': 700, 'identical_built': 50, 'copied_bytes': 600, 'identical_including_copied': 650},
                         '.text': {'bytes': 3000, 'identical_built': 2509, 'copied_bytes': 0, 'identical_including_copied': 2509,
                                   'first_differing': [{'va': '0x00401090'}]},
                         '.reloc': {'bytes': 222, 'identical_built': 200, 'copied_bytes': 0, 'identical_including_copied': 200}}}

    def test_whole_file_counts_reach_the_block(self):
        from site_stats import load_relink, relink_block
        rl = load_relink(self.doc(built_vs_copied=copy.deepcopy(self.FILE)), '2026-11-10', 'abc1234')
        f = rl['file']
        self.assertEqual((f['bytes'], f['built_bytes'], f['copied_bytes'], f['identical_bytes']), (4000, 2800, 600, 3400))
        self.assertEqual((f['pct_built'], f['pct_identical'], f['placeholder_bytes'], f['signature_bytes']), (70.0, 85.0, 500, 78))
        self.assertEqual([c['key'] for c in f['classes']], ['source', 'library', 'linker', 'padding', 'copied', 'placeholder', 'signature'])
        self.assertEqual([s['name'] for s in f['sections']], ['.text', '.data', '.reloc'])
        self.assertEqual(f['sections'][1], {'name': '.data', 'bytes': 700, 'built': 50, 'copied': 600, 'identical': 650})
        block = relink_block({'text_bytes': 3000}, rl)['file']
        self.assertEqual([(s['key'], s['bytes']) for s in block['segments']], [('built', 2800), ('copied', 600), ('rest', 600)])
        self.assertEqual({r['key']: r['bytes'] for r in block['remaining']}, {'placeholder': 500, 'linker': 22, 'signature': 78})
        self.assertEqual(sum(r['bytes'] for r in block['remaining']), block['bytes'] - block['identical_bytes'])
        self.assertEqual((block['pct_built'], block['pct_identical'], block['pct_signature']), (70.0, 85.0, 1.95))
        text = repr(block)
        for leak in ('0x', 'first_differing', 'va'):
            self.assertNotIn(leak, text)
        # the code-section figure is unchanged beside it
        self.assertEqual(relink_block({'text_bytes': 3000}, rl)['pct_text'], 83.63)

    def test_whole_file_is_optional_and_validated(self):
        from site_stats import load_relink, load_relink_file, relink_block
        rl = load_relink(self.doc())
        self.assertIsNone(rl['file'])
        self.assertIsNone(relink_block({'text_bytes': 3000}, rl)['file'])
        self.assertIsNone(load_relink_file(None))
        self.assertIsNone(load_relink_file({'sections': {}}))
        bad = copy.deepcopy(self.FILE)
        bad['file']['bytes'] = 4001
        with self.assertRaises(ValueError):
            load_relink_file(bad)
        bad = copy.deepcopy(self.FILE)
        bad['file']['identical_including_copied'] = 2000  # less than built
        with self.assertRaises(ValueError):
            load_relink_file(bad)
        bad = copy.deepcopy(self.FILE)
        bad['file']['by_class']['source'] = {'equal': 2000}
        with self.assertRaises(ValueError):
            load_relink_file(bad)
        bad = copy.deepcopy(self.FILE)
        bad['sections']['.text']['bytes'] = 5000
        with self.assertRaises(ValueError):
            load_relink_file(bad)
        # totals derived from the classes when the summary fields are absent
        minimal = {'file': {'by_class': copy.deepcopy(self.FILE['file']['by_class'])}}
        f = load_relink_file(minimal)
        self.assertEqual((f['bytes'], f['built_bytes'], f['copied_bytes'], f['identical_bytes'], f['sections']), (4000, 2800, 600, 3400, []))

    def test_home_page_shows_the_linked_executable_beside_the_code_section_bar(self):
        page = (Path(__file__).resolve().parents[1] / 'src' / 'pages' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('S.relink', page)
        self.assertIn('id="linked"', page)
        self.assertIn('Linked executable', page)
        self.assertIn('of the code section identical', page)
        self.assertIn('built from source', page)
        self.assertIn('S.relink, FL = RL && RL.file', page)
        self.assertIn('library data addresses, thunks and fragments', page)
        self.assertLess(page.index('id="overall"'), page.index('id="linked"'))


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


    def board(self, rows, names=None):
        from site_stats import normalize_stats
        return normalize_stats({'leaderboard': rows, 'by_model': []}, names)['leaderboard']

    def test_handle_and_login_of_one_person_share_a_row(self):
        rows = self.board([{'who': 'esk', 'functions': 5, 'bytes': 50}, {'who': 'iblis-777', 'functions': 2, 'bytes': 7}],
                          {777: 'Eskeshehir'})
        self.assertEqual(rows, [{'who': 'Eskeshehir', 'functions': 7, 'bytes': 57, 'github': True}])
        # not opted in: the handle row stays, the site account stays anonymous
        rows = self.board([{'who': 'esk', 'functions': 5, 'bytes': 50}, {'who': 'iblis-777', 'functions': 2, 'bytes': 7}])
        self.assertEqual({r['who']: r['functions'] for r in rows}, {'Esk': 5, 'Anonymous': 2})

    def test_opted_in_contributor_is_shown_by_login(self):
        rows = self.board([{'who': 'iblis-555', 'functions': 3, 'bytes': 30}, {'who': 'esk', 'functions': 1, 'bytes': 4}],
                          {555: 'octo-cat'})
        self.assertEqual(rows, [{'who': 'octo-cat', 'functions': 3, 'bytes': 30, 'github': True},
                                {'who': 'Esk', 'functions': 1, 'bytes': 4}])

    def test_contributor_not_in_the_map_stays_anonymous_and_anonymous_accounts_merge(self):
        rows = self.board([{'who': 'iblis-555', 'functions': 3, 'bytes': 30}, {'who': 'iblis-777', 'functions': 2, 'bytes': 7},
                           {'who': 'iblis-888', 'functions': 1, 'bytes': 5}], {555: 'octo-cat'})
        self.assertEqual(rows, [{'who': 'octo-cat', 'functions': 3, 'bytes': 30, 'github': True},
                                {'who': 'Anonymous', 'functions': 3, 'bytes': 12}])
        # no map at all: exactly the earlier behaviour
        self.assertEqual(self.board([{'who': 'iblis-555', 'functions': 3, 'bytes': 30},
                                     {'who': 'iblis-777', 'functions': 2, 'bytes': 7}]),
                         [{'who': 'Anonymous', 'functions': 5, 'bytes': 37}])

    def test_owner_alias_is_unchanged_by_the_map(self):
        rows = self.board([{'who': 'iblis-120912283', 'functions': 1, 'bytes': 3}, {'who': 'leg', 'functions': 2, 'bytes': 20}],
                          {120912283: 'Regubreaku'})
        self.assertEqual(rows, [{'who': 'Legbreak', 'functions': 3, 'bytes': 23}])

    def test_no_github_id_reaches_the_output_and_a_rebuild_keeps_the_login(self):
        import json
        rows = self.board([{'who': 'iblis-555', 'functions': 3, 'bytes': 30}, {'who': 'iblis-777', 'functions': 2, 'bytes': 7}],
                          {555: 'octo-cat'})
        self.assertNotIn('555', json.dumps(rows)); self.assertNotIn('777', json.dumps(rows))
        self.assertEqual(self.board(copy.deepcopy(rows)), rows)   # build_site renormalises without the map

    def test_login_that_reads_like_a_handle_does_not_take_its_credit(self):
        rows = self.board([{'who': 'iblis-555', 'functions': 1, 'bytes': 9}, {'who': 'leg', 'functions': 2, 'bytes': 20},
                           {'who': 'iblis-777', 'functions': 1, 'bytes': 1}, {'who': 'iblis-999', 'functions': 1, 'bytes': 2}],
                          {555: 'legbreak', 999: 'anonymous'})
        self.assertEqual({r['who']: r['bytes'] for r in rows},
                         {'Legbreak': 20, 'legbreak (GitHub)': 9, 'Anonymous': 1, 'anonymous (GitHub)': 2})

    def test_parse_names_keeps_only_valid_entries(self):
        from site_stats import parse_names
        doc = {'names': [{'github_id': 1, 'login': 'ok-name'}, {'github_id': '2', 'login': 'two'},
                         {'github_id': 0, 'login': 'zero'}, {'github_id': True, 'login': 'flag'},
                         {'github_id': 3, 'login': '<script>'}, {'github_id': 4, 'login': '-bad'},
                         {'github_id': 5}, 'junk']}
        self.assertEqual(parse_names(doc), {1: 'ok-name', 2: 'two'})
        self.assertEqual(parse_names([{'github_id': 9, 'login': 'x'}]), {9: 'x'})
        self.assertEqual(parse_names({'error': 'nope'}), {})

    def test_build_stats_reads_a_file_and_falls_back_when_unreadable(self):
        import io, json, tempfile, contextlib
        import build_stats
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'names.json'
            p.write_text(json.dumps({'names': [{'github_id': 555, 'login': 'octo-cat'}]}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(build_stats.load_names(str(p)), {555: 'octo-cat'})
            self.assertNotIn('octo-cat', out.getvalue()); self.assertNotIn('555', out.getvalue())
            (Path(d) / 'bad.json').write_text('not json')
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(build_stats.load_names(str(Path(d) / 'bad.json')), {})
                self.assertEqual(build_stats.load_names(str(Path(d) / 'missing.json')), {})
                self.assertEqual(build_stats.load_names('http://127.0.0.1:9/api/v1/names', timeout=2), {})
            self.assertIn('stay Anonymous', err.getvalue())
        self.assertEqual(build_stats.load_names(''), {})

    def test_default_names_url_follows_the_site_address(self):
        import os
        import build_stats
        old = {k: os.environ.pop(k, None) for k in ('IBLIS_NAMES_URL', 'IBLIS_SITE_URL')}
        try:
            os.environ['IBLIS_SITE_URL'] = 'https://example.invalid/'
            self.assertEqual(build_stats.default_names_url(), 'https://example.invalid/api/v1/names')
            os.environ['IBLIS_NAMES_URL'] = '/tmp/names.json'
            self.assertEqual(build_stats.default_names_url(), '/tmp/names.json')
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_home_page_escapes_board_names(self):
        page = (Path(__file__).resolve().parents[1] / 'src' / 'pages' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('${esc(r.who)}', page)


if __name__ == '__main__':
    unittest.main()
