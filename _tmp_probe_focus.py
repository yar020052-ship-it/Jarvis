from actions.hoi4_knowledge import discover_mod_root, discover_game_root
from collections import Counter
import re

for typ in ('pp_spend_priority', 'pp_spend_amount', 'diplo_action_desire', 'diplo_action_acceptance', 'prepare_for_war', 'force_build_armies', 'front_unit_request', 'invasion_unit_request', 'naval_invasion_focus'):
    ids = Counter()
    for root in (discover_game_root(), discover_mod_root()):
        if not root:
            continue
        for p in (root / 'common' / 'ai_strategy').rglob('*.txt'):
            t = p.read_text(encoding='utf-8-sig', errors='replace')
            for m in re.finditer(r'ai_strategy\s*=\s*\{([^{}]*)\}', t, re.S):
                body = m.group(1)
                ty = re.search(r'\btype\s*=\s*([A-Za-z0-9_]+)', body)
                idm = re.search(r'\bid\s*=\s*([A-Za-z0-9_.-]+)', body)
                if ty and ty.group(1) == typ:
                    ids[idm.group(1) if idm else '<none>'] += 1
    print('---', typ)
    for i, c in ids.most_common(30):
        print(f'  {i}: {c}')
