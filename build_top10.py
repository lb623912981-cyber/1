from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
lines = source.read_text(encoding="utf-8-sig").splitlines()

try:
    proxies_index = next(i for i, line in enumerate(lines) if line.strip() == "proxies:")
except StopIteration as exc:
    raise SystemExit("Missing proxies section") from exc

body = lines[proxies_index + 1:]
starts = [i for i, line in enumerate(body) if re.match(r"^- ", line)]
if not starts:
    raise SystemExit("No proxy entries found")

blocks = []
for n, start in enumerate(starts[:10]):
    end = starts[n + 1] if n + 1 < len(starts) else len(body)
    blocks.append(body[start:end])

names = []
for block in blocks:
    name_line = next((line for line in block if re.match(r"^(?:- |  )name:\s*", line)), None)
    if not name_line:
        raise SystemExit("A selected proxy has no name")
    names.append(name_line.split(":", 1)[1].strip())

output = [
    "mixed-port: 7890",
    "allow-lan: false",
    "mode: rule",
    "log-level: info",
    "proxies:",
]
for block in blocks:
    output.extend(block)

output.extend([
    "proxy-groups:",
    "- name: 节点选择",
    "  type: select",
    "  proxies:",
    "  - Telegram自动选择",
])
output.extend(f"  - {name}" for name in names)
output.extend([
    "- name: Telegram自动选择",
    "  type: url-test",
    "  url: https://telegram.org",
    "  interval: 300",
    "  tolerance: 100",
    "  lazy: false",
    "  proxies:",
])
output.extend(f"  - {name}" for name in names)
output.extend([
    "rules:",
    "- MATCH,节点选择",
    "",
])

target.write_text("\n".join(output), encoding="utf-8")
print(f"Wrote {len(blocks)} proxies to {target}")
