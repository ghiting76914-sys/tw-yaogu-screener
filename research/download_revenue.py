#!/usr/bin/env python3
"""下載上市櫃每月營收（公開資訊觀測站月營收彙總表），存入 data/cache/rev_{sii|otc}_YYYYMM.json。

內容為 {代號: {"rev": 當月營收, "yoy": 去年同月增減%, "mom": 上月比較增減%, "cum_yoy": 累計增減%}}，
營收單位為千元。已下載的月份直接略過，中斷後重跑可以接續。

  python3 research/download_revenue.py            # 2023/01 起到上個月
  python3 research/download_revenue.py 2024 1     # 指定起始年月
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from yaogu import CACHE_DIR, UA, num  # noqa: E402

URL = "https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{month}_0.html"
INTERVAL = 3.0

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)


def parse(page):
    out = {}
    for row in _ROW.findall(page):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in _CELL.findall(row)]
        if len(cells) < 10 or not re.fullmatch(r"\d{4}", cells[0]):
            continue
        out[cells[0]] = {"rev": num(cells[2]), "mom": num(cells[5]), "yoy": num(cells[6]),
                         "cum_yoy": num(cells[9])}
    return out


def fetch(market, year, month, retries=4):
    url = URL.format(market=market, roc=year - 1911, month=month)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
            return raw.decode("big5", errors="replace")
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  {year}/{month} {market} 失敗（{e}），重試…", file=sys.stderr)
            time.sleep(10 * (attempt + 1))


def main():
    y, m = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (2023, 1)
    today = dt.date.today()
    last = (today.replace(day=1) - dt.timedelta(days=1))  # 上個月
    if today.day <= 10:  # 每月 10 日前上個月的營收還沒公布完
        last = (last.replace(day=1) - dt.timedelta(days=1))
    while (y, m) <= (last.year, last.month):
        for market in ("sii", "otc"):
            path = os.path.join(CACHE_DIR, f"rev_{market}_{y}{m:02d}.json")
            if os.path.exists(path):
                continue
            rows = parse(fetch(market, y, m))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False)
            print(f"{y}/{m:02d} {'上市' if market == 'sii' else '上櫃'}：{len(rows)} 家", flush=True)
            time.sleep(INTERVAL)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


if __name__ == "__main__":
    main()
