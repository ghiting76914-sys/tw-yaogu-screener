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
                                 market_data=True)
    with _lock:
        res = yaogu.screen(end, args, log=False)
        history, trade_date = res["history"], res["trade_date"]
        _histories.clear()
        _histories[trade_date.strftime("%Y%m%d")] = history
        _markets.update({c: r["market"] for c, r in history[-1][1].items()})

        excluded = []
        picks = strategy.tomorrow_picks(history, res["ref"], small_cap,
                                        insti=res["insti"][-1], excluded=excluded)
        pre = strategy.pre_picks(history, res["ref"], small_cap)
        # 回測只用到昨天為止的資料，盤中即時資料更新不影響結果
        bt_key = (history[0][0], history[-2][0])
        if bt_key not in _backtests:
            _backtests[bt_key] = (strategy.backtest(history, insti=res["insti"]),
                                  strategy.pre_backtest(history, market=res["market"]))
    scanned = sum(1 for c in history[-1][1] if yaogu.is_common_stock(c))
    return {"trade_date": trade_date.isoformat(), "scanned": scanned, "live": res["live"],
            "market_open": yaogu.market_open_now(), "results": res["results"],
            "picks": picks, "backtest": _backtests[bt_key][0],
            "pre": pre, "pre_backtest": _backtests[bt_key][1],
            "market": res["market"][-1], "insti_ready": res["insti"][-1] is not None,
            "insti_excluded": excluded, "etf": _etf_summary()}


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
