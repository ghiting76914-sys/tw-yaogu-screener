#!/usr/bin/env python3
"""台股妖股篩選系統 — 網頁版

  python3 server.py            # 啟動後自動開啟瀏覽器 http://localhost:8765
  python3 server.py --port 9000
"""
import argparse
import datetime as dt
import json
import os
import threading
import types
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import etf
import revenue
import snapshots
import strategy
import yaogu

WEB_DIR = os.path.join(yaogu.BASE_DIR, "web")

# 網頁端自行依分數過濾，伺服器回傳較寬鬆的名單
SERVER_MIN_SCORE = 3

_lock = threading.Lock()
_histories = {}  # 交易日 -> history，供 K 線圖使用
_markets = {}  # 代號 -> 上市/上櫃，供即時報價使用
_backtests = {}  # 回測結果快取（歷史資料不變就不用重算）


def run_screen(date_str, min_volume, small_cap, live=True):
    end = dt.datetime.strptime(date_str, "%Y%m%d").date() if date_str else dt.date.today()
    args = types.SimpleNamespace(min_score=SERVER_MIN_SCORE, min_volume=min_volume,
                                 small_cap=small_cap, lookback=yaogu.LOOKBACK, live=live,
                                 market_data=True, streaks=True)
    with _lock:
        res = yaogu.screen(end, args, log=False)
        history, trade_date = res["history"], res["trade_date"]
        _histories.clear()
        _histories[trade_date.strftime("%Y%m%d")] = history
        _markets.update({c: r["market"] for c, r in history[-1][1].items()})

        excluded = []
        picks = strategy.tomorrow_picks(history, res["ref"], small_cap,
                                        insti=res["insti"][-1], excluded=excluded,
                                        insti_hist=res["insti"])
        pre_total = []
        pre = strategy.pre_picks(history, res["ref"], small_cap, total=pre_total)

        # 明日候選、起漲前夕的股票不一定在雷達名單裡（分數或成交量未達門檻），
        # 另外算出完整明細（分數、漲幅、週轉率等），讓右側明細不缺欄位
        rev = _revenue_summary(history)
        overnight = strategy.overnight_picks(history, res["ref"])
        # 明日強勢候選中，今天 13:00 隔日沖名單也有的股票（盤中執行時就用當下的名單）
        snap = snapshots._load(trade_date.isoformat(), "preview")
        on_codes = {p["代號"] for p in (snap or {}).get("overnight", [])} | (
            {p["代號"] for p in overnight} if res["live"] and not res["live"]["final"] else set())
        for p in picks:
            p["隔日沖名單"] = p["代號"] in on_codes
        known = {r["代號"] for r in res["results"]}
        detail_args = types.SimpleNamespace(**{**vars(args), "min_volume": 0})
        details = {}
        top3_picks = ((rev or {}).get("top3") or {}).get("picks", [])
        for p in picks + pre + overnight + (rev["picks"] if rev else []) + top3_picks:
            code = p["代號"]
            if code not in known and code not in details:
                row = yaogu.analyze(code, history, res["ref"], detail_args)
                if row:
                    details[code] = row
        # 回測只用到昨天為止的資料，盤中即時資料更新不影響結果
        bt_key = (history[0][0], history[-2][0])
        if bt_key not in _backtests:
            _backtests[bt_key] = (strategy.backtest(history, insti=res["insti"]),
                                  strategy.pre_backtest(history, market=res["market"]),
                                  strategy.overnight_backtest(history))
        previous = _previous(history, res, small_cap)
    scanned = sum(1 for c in history[-1][1] if yaogu.is_common_stock(c))
    data = {"trade_date": trade_date.isoformat(), "scanned": scanned, "live": res["live"],
            "market_open": yaogu.market_open_now(), "results": res["results"],
            "picks": picks, "backtest": _backtests[bt_key][0],
            "pre": pre, "pre_backtest": _backtests[bt_key][1],
            "market": res["market"][-1], "insti_ready": res["insti"][-1] is not None,
            "insti_excluded": excluded, "details": details, "etf": _etf_summary(),
            "pre_total": pre_total[0] if pre_total else len(pre), "revenue": rev,
            "overnight": overnight, "overnight_backtest": _backtests[bt_key][2],
            "overnight_research": strategy.OVERNIGHT_RESEARCH, "previous": previous}
    try:
        snapshots.save(data)
    except Exception as e:
        print(f"名單存檔失敗：{e}")
    return data


def _previous(history, res, small_cap):
    """前一交易日名單對照；沒有存檔的名單用前一天的資料重新計算。"""
    prev_hist = history[:-1]  # 同一個物件重複使用，build_series 只需計算一次

    def slim(lst, extra=None):
        return [{"代號": p["代號"], "名稱": p["名稱"], "價格": p.get("收盤", p.get("現價")),
                 **(extra(p) if extra else {})} for p in lst]

    def recompute(kind):
        ref = res["ref"]
        if kind == "picks":
            lst = strategy.tomorrow_picks(prev_hist, ref, small_cap, insti=res["insti"][-2],
                                          insti_hist=res["insti"][:-1])
            return slim(lst, lambda p: {"進場區間": [p["計畫"]["levels"]["entry_low"],
                                                   p["計畫"]["levels"]["entry_high"]]})
        if kind == "pre":
            return slim(strategy.pre_picks(prev_hist, ref, small_cap),
                        lambda p: {"突破價": p["計畫"]["levels"]["trigger"]})
        if kind == "overnight":  # 沒有盤中存檔時，以前一天收盤資料推算（買進價為前一天收盤價）
            return slim(strategy.overnight_picks(prev_hist, ref))
        if kind == "top3":
            rev = revenue.load_revenue([d for d, _ in prev_hist])
            return slim(revenue.top3(prev_hist, rev)["picks"])
        return []

    try:
        return snapshots.previous(history, recompute)
    except Exception as e:
        print(f"前一交易日對照失敗：{e}")
        return None


def _revenue_summary(history):
    """營收動能分頁資料；抓不到時不影響其他分頁。"""
    try:
        return revenue.summary(history)
    except Exception as e:
        print(f"營收動能資料讀取失敗：{e}")
        return None


def _etf_summary():
    """0050 專區資料；抓不到時不影響其他分頁。"""
    try:
        return etf.summary()
    except Exception as e:
        print(f"0050 資料讀取失敗：{e}")
        return None


def quote(code):
    market = _markets.get(code)
    if market is None:
        return None
    return yaogu.fetch_mis([(code, market)]).get(code)


def chart_data(code, date_str):
    history = _histories.get(date_str)
    if history is None:
        return None
    bars = []
    for d, day in history:
        r = day.get(code)
        if r and r["close"] is not None and r["open"] is not None:
            bars.append({"date": d.isoformat(), "open": r["open"], "high": r["high"],
                         "low": r["low"], "close": r["close"], "volume": int(r["volume"] / 1000)})
    return bars


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, fmt, *a):
        pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path == "/api/screen":
                self.send_json(run_screen(q.get("date"),
                                          float(q.get("min_volume", 500)),
                                          float(q.get("small_cap", 10))))
            elif url.path == "/api/quote":
                q_ = quote(q.get("code", ""))
                self.send_json({"quote": q_, "market_open": yaogu.market_open_now()})
            elif url.path == "/api/chart":
                bars = chart_data(q.get("code", ""), q.get("date", "").replace("-", ""))
                if bars is None:
                    self.send_json({"error": "請先執行篩選"}, 404)
                else:
                    self.send_json({"bars": bars})
            else:
                super().do_GET()
        except Exception as e:
            self.send_json({"error": str(e)}, 500)


def main():
    p = argparse.ArgumentParser(description="台股妖股篩選系統 網頁版")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="不要自動開啟瀏覽器")
    args = p.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"妖股篩選網頁已啟動：{url}　（按 Ctrl+C 停止）")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
