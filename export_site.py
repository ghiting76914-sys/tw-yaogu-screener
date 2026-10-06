#!/usr/bin/env python3
"""產生靜態網站（GitHub Pages 用）：把當天篩選結果、明日候選、回測與 K 線資料預先算好存成 JSON。

  python3 export_site.py            # 收盤後的正式版，輸出到 site/
  python3 export_site.py --preview  # 盤中預覽（用即時行情）
"""
import datetime as dt
import json
import os
import shutil
import sys

import server
import yaogu

SITE_DIR = os.path.join(yaogu.BASE_DIR, "site")


def main():
    # --preview：盤中預覽，用證交所即時行情當作今天（13:00 執行，推播隔日沖）
    preview = "--preview" in sys.argv
    # 雲端主機時區不一定是台灣，產生時間一律以台灣時間顯示
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    data = server.run_screen(None, min_volume=500, small_cap=10, live=preview)
    data["generated_at"] = now.strftime("%Y-%m-%d %H:%M")
    data["market_open"] = False

    date_key = data["trade_date"].replace("-", "")
    codes = ({r["代號"] for r in data["results"]} | {p["代號"] for p in data["picks"]}
             | {p["代號"] for p in data["pre"]}
             | {p["代號"] for p in (data["revenue"] or {}).get("picks", [])}
             | {p["代號"] for p in data.get("overnight", [])})
    charts = {c: server.chart_data(c, date_key) for c in sorted(codes)}

    shutil.rmtree(SITE_DIR, ignore_errors=True)
    os.makedirs(os.path.join(SITE_DIR, "data"))
    with open(os.path.join(server.WEB_DIR, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace("<script>\nconst $", "<script>window.STATIC_SITE = true;</script>\n<script>\nconst $", 1)
    with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    for name, obj in (("latest.json", data), ("charts.json", charts)):
        with open(os.path.join(SITE_DIR, "data", name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    open(os.path.join(SITE_DIR, ".nojekyll"), "w").close()

    if preview and data.get("live"):
        print(f"盤中預覽：即時行情 {data['live']['time']}，成交量換算倍數 {data['live']['volume_scale']}")
    print(f"已產生 {SITE_DIR}：交易日 {data['trade_date']}，雷達 {len(data['results'])} 檔，"
          f"明日候選 {len(data['picks'])} 檔，起漲前夕 {len(data['pre'])} 檔，"
          f"0050 {'已更新至 ' + data['etf']['date'] if data.get('etf') else '讀取失敗'}")


if __name__ == "__main__":
    main()
