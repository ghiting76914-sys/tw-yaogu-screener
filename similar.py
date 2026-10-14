"""歷史相似情況：把股票「今天收盤後的狀態」分組，查 11 年來同一組的股票之後的表現。

分組條件（第 t 天收盤後就知道）：
  chg      當天漲跌（以參考價計）       ≤-7、-7~-3、-3~0、0~3、3~7、7~9.5%、漲停（≥ 9.5%）
  trend    月線／季線                    兩條都在下、季線上月線下、月線上季線下、兩條都在上
  dist60   距前 60 日最高價              <-15%、-15~-5%、-5~0%、突破
  ret5     近 5 日漲跌（含今天）          <-10、-10~-3、-3~3、3~10、>10%
  vr       量比（今天量 ÷ 前 20 日均量）  <0.7、0.7~1.5、1.5~3、>3
  breadth  市場廣度（流動股站上月線比例） <30%、30~70%、>70%
結果：隔天開盤買、5 個與 20 個交易日後收盤賣（含除權息、已扣成本 0.585%）。
對照表由 research/build_similar.py 用 11 年資料產生（web/similar.json），網站每天用 search.py 算出每檔股票的分組再查表。
股票範圍與研究相同：一般股、股價 ≥ 10、20 日均量 ≥ 500 張。
"""
from bisect import bisect_right

EDGES = {
    "chg": [-7, -3, 0, 3, 7],          # 再加上「漲停」一組
    "dist60": [-15, -5, 0],
    "ret5": [-10, -3, 3, 10],
    "vr": [0.7, 1.5, 3],
    "breadth": [0.3, 0.7],
}
LABELS = {
    "chg": ["跌 ≥7%", "跌 3~7%", "跌 0~3%", "漲 0~3%", "漲 3~7%", "漲 7~9.5%", "漲停"],
    "trend": ["月線、季線都在下", "季線上、月線下", "月線上、季線下", "月線、季線都在上"],
    "dist60": ["距 60 日高 >15%", "距 60 日高 5~15%", "距 60 日高 0~5%", "突破 60 日高"],
    "ret5": ["近 5 日跌 >10%", "近 5 日跌 3~10%", "近 5 日 ±3%", "近 5 日漲 3~10%", "近 5 日漲 >10%"],
    "vr": ["量縮（<0.7 倍）", "量平（0.7~1.5 倍）", "量增（1.5~3 倍）", "爆量（>3 倍）"],
    "breadth": ["大盤弱（廣度 <30%）", "大盤普通", "大盤強（廣度 >70%）"],
}
NAMES = {"chg": "當天漲跌", "trend": "均線趨勢", "dist60": "距 60 日高", "ret5": "近 5 日", "vr": "量比", "breadth": "大盤廣度"}
# 由細到粗：最細的一層樣本不夠時，退回比較粗的分組
LEVELS = [
    ("chg", "trend", "dist60", "ret5", "vr", "breadth"),
    ("chg", "trend", "dist60", "ret5", "vr"),
    ("chg", "trend", "dist60", "ret5"),
    ("chg", "trend", "dist60"),
    ("trend", "dist60"),
]
MIN_TRAIN, MIN_VAL = 300, 80


def bins(g, idx, ma20, ma60, hi60, ret5, vr, breadth):
    """g、ret5 為比例（0.05 = 5%）；idx、ma20、ma60、hi60 為同一套還原價格。回傳各條件的組別編號。"""
    return {
        "chg": 6 if g >= 0.095 else bisect_right(EDGES["chg"], g * 100),
        "trend": (2 if idx > ma20 else 0) + (1 if idx > ma60 else 0),
        "dist60": bisect_right(EDGES["dist60"], (idx / hi60 - 1) * 100),
        "ret5": bisect_right(EDGES["ret5"], ret5 * 100),
        "vr": bisect_right(EDGES["vr"], vr),
        "breadth": bisect_right(EDGES["breadth"], breadth),
    }


def key(level, b):
    return "-".join(str(b[f]) for f in level)


def lookup(table, b):
    """依序找最細、樣本足夠的一層。回傳 {level, desc, stats} 或 None。"""
    for i, level in enumerate(LEVELS):
        st = table.get(str(i), {}).get(key(level, b))
        if st and st[0] >= MIN_TRAIN and st[6] >= MIN_VAL:
            return {"level": i, "desc": [LABELS[f][b[f]] for f in level], "stats": st}
    return None
