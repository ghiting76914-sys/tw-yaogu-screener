"""當沖「大單篩選」的紀錄：收盤後查當天有交易的當沖股票的大單買賣比，存起來給實際績效分組比較。

大單篩選（≥ 20 張的大單，買進量 ÷ 賣出量 = 買賣比）沒有歷史逐筆資料可以回測，只能從上線起累積：
每天正式更新（收盤後）時，透過 Cloudflare Worker 的 /bigorders（富果逐筆成交）查詢，存成
data/cache/snapshots/bigorders_YYYY-MM-DD.json；perf.py 依買賣比分組（≥2／0.5～2／≤0.5）統計。
富果免費額度每分鐘 60 次，每檔間隔 20 秒查詢。
"""
import json
import os
import time
import urllib.request

import snapshots

WORKER = "https://yaogu-quote.ghiting76914.workers.dev/bigorders"
MIN_LOTS = 20


def path(date):
    return os.path.join(snapshots.SNAP_DIR, f"bigorders_{date}.json")


def load(date):
    try:
        with open(path(date), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def record(date, codes, gap=20):
    """查詢 codes 當天的大單統計（已查過的略過）。只在收盤後、當天的資料呼叫。"""
    got = load(date)
    todo = [c for c in dict.fromkeys(codes) if c not in got]
    for k, code in enumerate(todo):
        if k:
            time.sleep(gap)
        try:
            req = urllib.request.Request(f"{WORKER}?symbol={code}&min={MIN_LOTS}", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                j = json.loads(r.read())
        except Exception as e:
            print(f"大單紀錄 {code} 失敗：{e}")
            continue
        if j.get("date") != date or j.get("stale"):
            print(f"大單紀錄 {code}：資料日期 {j.get('date')}、{j.get('stale') or ''}，略過")
            continue
        b = j["big"]
        got[code] = {"ratio": b["ratio"], "buy": b["buyVol"], "sell": b["sellVol"], "trades": j["trades"],
                     "buyAvg": b["buyAvg"], "sellAvg": b["sellAvg"], "vwap": j["vwap"]}
    if todo:
        os.makedirs(snapshots.SNAP_DIR, exist_ok=True)
        with open(path(date), "w", encoding="utf-8") as fh:
            json.dump(got, fh, ensure_ascii=False)
        print(f"大單紀錄 {date}：{len(got)} 檔")
    return got


def bucket(info):
    """買賣比分組：≥2 大戶買、≤0.5 大戶賣、其他中性；沒有資料回傳 None。"""
    if not info or (not info["buy"] and not info["sell"]):
        return None
    r = info["ratio"]
    return "≥2" if r is None or r >= 2 else "≤0.5" if r <= 0.5 else "0.5~2"
