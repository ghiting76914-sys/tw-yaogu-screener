#!/usr/bin/env python3
"""下載每月換股日（11 日後第一個交易日）的本益比、股價淨值比（研究用，GVI 回測），
存入 data/cache/pe_{twse|tpex}_YYYYMMDD.json，內容為 {代號: [本益比, 股價淨值比]}（虧損時本益比為 null）。
只抓已有行情快取的交易日，已下載的略過。

  python3 research/download_pe.py twse
  python3 research/download_pe.py tpex   # 與上市分開同時跑，兩個網站各自限速
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import revenue_long as L  # noqa: E402
from yaogu import CACHE_DIR, fetch_json, is_common_stock, num  # noqa: E402


def rebalance_days():
    out, seen = [], set()
    for ymd in L.trading_days():
        if int(ymd[6:]) >= 11 and ymd[:6] not in seen:
            seen.add(ymd[:6])
            out.append(ymd)
    return out


def parse(d):
    t = d["tables"][0] if d.get("tables") else d
    fields = t.get("fields") or []
    if "本益比" not in fields or "股價淨值比" not in fields:
        return {}
    ipe, ipb = fields.index("本益比"), fields.index("股價淨值比")
    out = {}
    for r in t.get("data") or []:
        code = str(r[0]).strip()
        if is_common_stock(code):
            out[code] = [num(r[ipe]), num(r[ipb])]
    return out


def main():
    market = sys.argv[1]
    days = rebalance_days()
    done = 0
    for i, ymd in enumerate(days, 1):
        path = os.path.join(CACHE_DIR, f"pe_{market}_{ymd}.json")
        if os.path.exists(path):
            continue
        if market == "twse":
            url = f"https://www.twse.com.tw/exchangeReport/BWIBBU_d?response=json&date={ymd}&selectType=ALL"
        else:
            url = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?date={ymd[:4]}%2F{ymd[4:6]}%2F{ymd[6:]}&response=json"
        rows = parse(fetch_json(url))
        if rows:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
        else:
            print(f"{market}：{ymd} 沒有資料", flush=True)
        done += 1
        if i % 20 == 0:
            print(f"{market}：{ymd} 已完成 {i}/{len(days)}", flush=True)
    print(f"{market} 完成，本次下載 {done} 天（共 {len(days)} 個換股日）", flush=True)


if __name__ == "__main__":
    main()
