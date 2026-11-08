"""Apply the public site's contributor and model attribution to generated counts."""
from collections import defaultdict


def normalize_stats(stats):
    # iblis-120912283 is the owner's own GitHub account (Regubreaku). Other site contributors are imported as
    # iblis-<github id> and stay anonymous on the board until a login map for opted-in contributors exists.
    aliases = {'lead': 'Legbreak', 'leg': 'Legbreak', 'fable': 'Legbreak', 'iblis-120912283': 'Legbreak',
               'legbreak': 'Legbreak', 'codex': 'Nathan', 'nathan': 'Nathan'}
    for field, key in (('leaderboard', 'who'), ('by_model', 'model')):
        grouped = defaultdict(lambda: {'functions': 0, 'bytes': 0})
        for row in stats[field]:
            name = row[key]
            if key == 'who':
                name = aliases.get(name.casefold()) or ('Anonymous' if name.casefold().startswith('iblis-') else name[:1].upper() + name[1:])
            elif name == 'lead':
                name = 'claude-opus-5-5'
            grouped[name]['functions'] += row['functions']
            grouped[name]['bytes'] += row['bytes']
        stats[field] = sorted(({key: name, **counts} for name, counts in grouped.items()),
                              key=lambda row: (-row['bytes'], row[key]))
    return stats
