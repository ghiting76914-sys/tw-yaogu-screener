#!/usr/bin/env python3
"""下載研究用的長期歷史行情（預設約 2 年，520 個交易日），存入 data/cache/。
已下載過的日期會直接使用快取，中斷後重跑可以接續。"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaogu  # noqa: E402

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 520

end = dt.date.today()
d, got, misses = end, 0, 0
while got < DAYS and misses < 15:
    if d.weekday() < 5:
        twse = yaogu.load_day("twse", d)
        tpex = yaogu.load_day("tpex", d)
        if twse and tpex:
            got += 1
            misses = 0
            if got % 20 == 0:
                print(f"{d}：已完成 {got}/{DAYS} 個交易日", flush=True)
        else:
            misses += 1
    d -= dt.timedelta(days=1)
print(f"完成：{got} 個交易日，最早 {d + dt.timedelta(days=1)}", flush=True)
