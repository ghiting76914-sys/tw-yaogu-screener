#!/usr/bin/env python3
"""下載研究用的輔助資料，存入 data/cache/：
  - 加權指數每日收盤（證交所 FMTQIK，每月一次請求）→ taiex.json
  - 三大法人買賣超（上市 T86、上櫃 dailyTrade，每日一次請求）→ insti_{twse|tpex}_YYYYMMDD.json
    內容為 {代號: [外資買賣超股數, 投信買賣超股數, 三大法人合計股數]}

只抓已有行情快取的交易日；已下載過的直接略過，中斷後重跑可以接續。
"""
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaogu  # noqa: E402
from yaogu import CACHE_DIR, fetch_json, num  # noqa: E402


def trading_days():
    days = []
    for f in glob.glob(os.path.join(CACHE_DIR, "twse_*.json")):
        ymd = os.path.basename(f)[5:13]
        if os.path.exists(os.path.join(CACHE_DIR, f"tpex_{ymd}.json")) and os.path.getsize(f) > 10:
            days.append(dt.datetime.strptime(ymd, "%Y%m%d").date())
    return sorted(days)


def download_taiex(days):
    path = os.path.join(CACHE_DIR, "taiex.json")
    taiex = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    months = sorted({(d.year, d.month) for d in days})
    this_month = (dt.date.today().year, dt.date.today().month)
    for y, m in months:
        if any(k.startswith(f"{y}{m:02d}") for k in taiex) and (y, m) != this_month:
            continue
        d = fetch_json(f"https://www.twse.com.tw/exchangeReport/FMTQIK?response=json&date={y}{m:02d}01")
        for row in d.get("data", []):
            ry, rm, rd = row[0].split("/")
            taiex[f"{int(ry) + 1911}{rm}{rd}"] = num(row[4])
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(taiex, fh)
    print(f"加權指數：{len(taiex)} 天", flush=True)


def parse_twse_insti(d):
    out = {}
    for r in d.get("data", []):
        code = r[0].strip()
        if yaogu.is_common_stock(code):
            foreign = (num(r[4]) or 0) + (num(r[7]) or 0)
            out[code] = [foreign, num(r[10]) or 0, num(r[18]) or 0]
    return out


def parse_tpex_insti(d):
    out = {}
    tables = d.get("tables") or []
    for r in (tables[0].get("data", []) if tables else []):
        code = r[0].strip()
        if yaogu.is_common_stock(code):
            out[code] = [num(r[10]) or 0, num(r[13]) or 0, num(r[23]) or 0]
    return out


def download_insti(days):
    done = 0
    for i, d in enumerate(reversed(days), 1):
        ymd = d.strftime("%Y%m%d")
        for market in ("twse", "tpex"):
            path = os.path.join(CACHE_DIR, f"insti_{market}_{ymd}.json")
            if os.path.exists(path):
                continue
            if market == "twse":
                rows = parse_twse_insti(fetch_json(
                    f"https://www.twse.com.tw/fund/T86?response=json&date={ymd}&selectType=ALLBUT0999"))
            else:
                rows = parse_tpex_insti(fetch_json(
                    "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW"
                    f"&date={d.year}%2F{d.month:02d}%2F{d.day:02d}&response=json"))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
            done += 1
        if i % 20 == 0:
            print(f"法人：{d} 已完成 {i}/{len(days)} 個交易日", flush=True)
    print(f"法人：完成（本次新下載 {done} 個檔案）", flush=True)


if __name__ == "__main__":
    days = trading_days()
    print(f"共 {len(days)} 個交易日：{days[0]} ～ {days[-1]}", flush=True)
    download_taiex(days)
    download_insti(days)
