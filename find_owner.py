t=open(r'actions/game_pilot.py',encoding='utf-8').read()
for i,l in enumerate(t.splitlines()):
    if 'owner input detected' in l:
        print(i+1, repr(l))
        break
