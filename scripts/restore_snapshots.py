#!/usr/bin/env python3
"""每日名單存檔的備援：GitHub Actions 快取被清掉時（例如 7 天沒用），
從已發佈網站的 data/snapshots.json 把存檔補回 data/cache/snapshots/（已有的檔案不覆蓋）。
實際績效需要這些存檔，而且無法重新產生，所以每次發佈網站都會一併備份。"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from snapshots import SNAP_DIR  # noqa: E402

URL = "https://ghiting76914-sys.github.io/tw-yaogu-screener/data/snapshots.json"

try:
    with urllib.request.urlopen(URL, timeout=60) as resp:
        archive = json.loads(resp.read())
except Exception as e:
    print(f"無法取得線上存檔備份：{e}")
    archive = {}
os.makedirs(SNAP_DIR, exist_ok=True)
added = 0
for name, snap in archive.items():
    path = os.path.join(SNAP_DIR, name)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False)
        added += 1
print(f"線上備份共 {len(archive)} 個存檔，補回 {added} 個")

# 手動補登的存檔（例如存檔功能上線前已推播的名單），放在 scripts/snapshot_seed/，已有的不覆蓋
SEED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshot_seed")
for name in sorted(os.listdir(SEED_DIR)) if os.path.isdir(SEED_DIR) else []:
    path = os.path.join(SNAP_DIR, name)
    if name.endswith(".json") and not os.path.exists(path):
        with open(os.path.join(SEED_DIR, name), encoding="utf-8") as src, open(path, "w", encoding="utf-8") as fh:
            fh.write(src.read())
        print(f"補登手動存檔 {name}")
