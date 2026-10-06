#!/usr/bin/env python3
"""下載每日融資融券餘額（研究用），存入 data/cache/margin_{twse|tpex}_YYYYMMDD.json。
內容為 {代號: [融資餘額, 融券餘額, 融資限額]}（單位：張）。只抓已有行情快取的交易日，已下載的略過。

  python3 research/download_margin.py twse 2024-08-12   # 上市，從指定日期起
  python3 research/download_margin.py tpex 2024-08-12   # 上櫃（與上市分開同時跑，兩個網站各自限速）
"""
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaogu  # noqa: E402
from yaogu import CACHE_DIR, fetch_json, is_common_stock, num  # noqa: E402


def parse_twse(d):
    table = next((t for t in d.get("tables", []) if "融資融券彙總" in (t.get("title") or "")), None)
    out = {}
    for r in (table or {}).get("data", []):
        code = r[0].strip()
        if is_common_stock(code):
            out[code] = [num(r[6]) or 0, num(r[12]) or 0, num(r[7]) or 0]
    return out


def parse_tpex(d):
    tables = d.get("tables") or []
    out = {}
    for r in (tables[0].get("data", []) if tables else []):
        code = r[0].strip()
        if is_common_stock(code):
            out[code] = [num(r[6]) or 0, num(r[14]) or 0, num(r[9]) or 0]
    return out


def main():
    market, start = sys.argv[1], sys.argv[2].replace("-", "")
    days = sorted(os.path.basename(f)[5:13] for f in glob.glob(os.path.join(CACHE_DIR, "twse_*.json"))
                  if os.path.basename(f)[5:13] >= start and os.path.getsize(f) > 10)
    done = 0
    for i, ymd in enumerate(reversed(days), 1):
        path = os.path.join(CACHE_DIR, f"margin_{market}_{ymd}.json")
        if os.path.exists(path):
            continue
        if market == "twse":
            rows = parse_twse(fetch_json(f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={ymd}&selectType=ALL"))
        else:
            rows = parse_tpex(fetch_json("https://www.tpex.org.tw/www/zh-tw/margin/balance"
                                         f"?date={ymd[:4]}%2F{ymd[4:6]}%2F{ymd[6:]}&response=json"))
        if rows:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
        done += 1
        if i % 50 == 0:
            print(f"{market}：{ymd} 已完成 {i}/{len(days)}", flush=True)
    print(f"{market} 完成，本次下載 {done} 天", flush=True)


if __name__ == "__main__":
    main()
