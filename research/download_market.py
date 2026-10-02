#!/usr/bin/env python3
"""只下載單一市場的每日行情（上市 twse 或上櫃 tpex），從結束日往回到起始日。
兩個市場分屬不同網站，各開一個程式同時下載可以加快速度；已下載的日期直接略過。

  python3 research/download_market.py twse 2022-12-31 2015-01-01
  python3 research/download_market.py tpex-fast 2022-12-31 2015-01-01   # 上櫃快速版（成交量不含零股）
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaogu  # noqa: E402

def load_tpex_fast(d):
    """上櫃行情改用「不含權證」的 API：回應約 0.4 秒（原本約 10 秒）。
    價格欄位與原本完全相同，但成交量不含零股（平均約少 0.7%）。只用於研究用的歷史資料。"""
    ymd = d.strftime("%Y%m%d")
    path = os.path.join(yaogu.CACHE_DIR, f"tpex_{ymd}.json")
    if os.path.exists(path):
        return
    data = yaogu.fetch_json("https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
                            f"?date={d.year}%2F{d.month:02d}%2F{d.day:02d}&type=EW&response=json")
    table = (data.get("tables") or [{}])[0]
    rows = []
    if table.get("data"):
        f = {name.strip(): i for i, name in enumerate(table["fields"])}
        for r in table["data"]:
            val = lambda k: yaogu.num(r[f[k]].replace(" ", ""))  # noqa: E731
            code = r[f["代號"]].strip()
            if not yaogu.is_common_stock(code):
                continue
            rows.append({"code": code, "name": r[f["名稱"]].strip(), "open": val("開盤"), "high": val("最高"),
                         "low": val("最低"), "close": val("收盤"), "change": val("漲跌"),
                         "volume": val("成交股數") or 0, "amount": val("成交金額(元)") or 0,
                         "shares": val("發行股數")})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False)


market = sys.argv[1]
d = dt.date.fromisoformat(sys.argv[2])
end = dt.date.fromisoformat(sys.argv[3])
n = 0
while d >= end:
    if d.weekday() < 5:
        if market == "tpex-fast":
            load_tpex_fast(d)
        else:
            yaogu.load_day(market, d)
        n += 1
        if n % 100 == 0:
            print(f"{market}：已處理到 {d}", flush=True)
    d -= dt.timedelta(days=1)
print(f"{market} 下載完成", flush=True)
