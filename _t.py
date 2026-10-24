import re
rows = []
with open("_imp.log", encoding="utf-8", errors="replace") as f:
    for line in f:
        m = re.match(r"import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\S+)", line)
        if m:
            rows.append((int(m.group(1)), int(m.group(2)), m.group(3)))
rows.sort(reverse=True)
print("total lines:", len(rows))
for self_us, cum_us, name in rows[:25]:
    if cum_us > 200000:
        print(f"{cum_us/1000:9.0f}ms cum  {self_us/1000:7.0f}ms self  {name}")
