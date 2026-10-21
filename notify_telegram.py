#!/usr/bin/env python3
"""把推播內容同步傳到 Telegram（與 notify_line.py 同一份 site/data/latest.json、同樣的時機）。

  12:30 盤中預覽  ⚡ 隔日沖候選（⭐ 推薦、+2% 限價賣）
  13:40 收盤後    🎯 明日精選 3 檔＋明日強勢候選（前 5 檔）
  換股日          📊 營收動能名單
  0050 回檔       回檔 5%／10% 通知

Telegram 免費、沒有每月則數限制，適合用頻道群發給朋友。設定（GitHub → Settings → Secrets and variables → Actions）：
  TELEGRAM_BOT_TOKEN  向 @BotFather 建立機器人取得的金鑰
  TELEGRAM_CHAT_ID    頻道或群組的 ID（例如 -1001234567890 或 @頻道帳號；多個以逗號分隔），
                      可用 scripts/telegram_chat_id.py 查詢
沒有設定就略過。每種訊息一天只發一次（data/cache/tg_*_YYYYMMDD 標記檔）。

  python3 notify_telegram.py --dry-run   # 只印出內容
  python3 notify_telegram.py --test      # 傳一則測試訊息
"""
import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.request

import notify_line as N

API = "https://api.telegram.org/bot{token}/sendMessage"


def e(x):
    return html.escape(str(x), quote=False)


def button(label, anchor=""):
    return {"inline_keyboard": [[{"text": label, "url": f"{N.SITE_URL}{anchor}"}]]}


def overnight_msg(data):
    picks = data.get("overnight") or []
    hhmm = ((data.get("live") or {}).get("time") or "")[:5]
    R = data.get("overnight_research") or {}
    lines = [f"⚡ <b>隔日沖候選</b>｜{data['trade_date'][5:].replace('-', '/')} {hhmm} 盤中",
             "收盤前買進；明天掛「+2%賣」限價，盤中碰到就賣、沒成交收盤前賣（開盤直接賣為備案）。", ""]
    for i, p in enumerate(picks, 1):
        star = "⭐" if p.get("推薦") else ""
        lim = f"｜+2%賣 {p['限價賣出']:g}" if p.get("限價賣出") else ""
        state = "（已漲停，不建議排隊）" if p.get("已漲停") else ""
        name = f"<b>{e(p['名稱'])}</b>" if p.get("推薦") else e(p["名稱"])
        pct = f"  {p['漲跌%']:+.1f}%" if p.get("漲跌%") is not None else ""
        lines.append(f"{i}. {star}{name} {p['代號']}{pct}  現價 {p.get('現價', p.get('價格')):g}{lim}{state}")
    if not picks:
        lines.append("目前沒有符合條件的股票。")
    rec = R.get("rec_heat")
    weak = (data.get("panic") or {}).get("breadth")
    if weak is not None and weak < 30:
        lines += ["", f"⚠️ <b>大盤偏弱</b>（市場廣度 {weak}%）：這種環境隔日沖回測平均為負，建議暫停或只買 ⭐。"]
    lines += ["", "⭐ 推薦：強勢條件＋族群齊漲（同族群另有 ≥3 檔漲 5%）"
              + (f"，11 年回測平均 {rec['test']['avg']:+.1f}%、勝率 {rec['test']['win']:.0f}%" if rec else "")
              + "。資金有限時只買 ⭐、還沒漲停的。",
              "⚠️ 規則選股的觀察名單，非投資建議。"]
    return "\n".join(lines), button("看完整名單與理由", "#overnight")


def close_msg(data):
    rev = data.get("revenue") or {}
    T = rev.get("top3") or {}
    lines = []
    if T.get("picks"):
        today_buy = N.after_hours_open() and T.get("date") == data["trade_date"]
        lines += [f"🎯 <b>明日精選 3 檔</b>｜{T['date'][5:].replace('-', '/')}",
                  "<b>今天 14:00～14:30 盤後定價買進</b>（以收盤價成交），來不及就明天開盤買。" if today_buy
                  else "明天開盤買進。",
                  f"持有 {T.get('hold', 20)} 個交易日，跌破進場價 -{T.get('stop', 10)}% 停損。", ""]
        for p in T["picks"]:
            if p.get("已持有"):
                lines.append(f"#{p['排名']} <b>{e(p['名稱'])}</b> {p['代號']}  收 {p['收盤']:g}"
                             f"（已持有：{p['已持有'][5:].replace('-', '/')} 已入選、還在持有期，不用再買）")
                continue
            lines.append(f"#{p['排名']} <b>{e(p['名稱'])}</b> {p['代號']}  收 {p['收盤']:g}（{p['漲跌%']:+.2f}%）"
                         f"  營收年增 {p['營收年增%']:+.0f}%  停損約 {p['停損參考']:g}")
        lines.append("")
    picks = data.get("picks") or []
    if picks:
        lines += ["📋 <b>明日強勢候選</b>（觀察名單，回測沒有明顯優勢）"]
        for p in picks[:N.MAX_PICKS]:
            lv = p["計畫"]["levels"]
            lines.append(f"・{e(p['名稱'])} {p['代號']}  收 {p['收盤']:g}  進場 {lv['entry_low']:g}～{lv['entry_high']:g}"
                         f"  停損 {lv['stop']:g}  目標 {lv['tp1']:g}")
        if len(picks) > N.MAX_PICKS:
            lines.append(f"另有 {len(picks) - N.MAX_PICKS} 檔，請見網頁。")
        lines.append("")
    if not data.get("insti_ready"):
        lines.append("三大法人買賣超 15:00 後才公布，這份名單尚未排除法人賣超。")
    lines.append("⚠️ 規則選股，非投資建議，請自行控管風險。")
    return "\n".join(lines), button("看精選理由與 K 線")


def send(token, chat, text, markup=None):
    body = {"chat_id": chat, "text": text[:4096], "parse_mode": "HTML", "disable_web_page_preview": True}
    if markup:
        body["reply_markup"] = markup
    req = urllib.request.Request(API.format(token=token), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()).get("ok")
    except urllib.error.HTTPError as err:
        print(f"Telegram 發送失敗（{chat}）：HTTP {err.code} {err.read()[:200]!r}")
        return False


def main():
    ap = argparse.ArgumentParser(description="把推播內容同步傳到 Telegram")
    ap.add_argument("--dry-run", action="store_true", help="只印出內容，不發送")
    ap.add_argument("--test", action="store_true", help="傳一則測試訊息")
    ap.add_argument("--force", action="store_true", help="已發送過仍再發送")
    ap.add_argument("--sample", action="store_true",
                    help="把今天推播給 LINE 的內容（12:30 隔日沖、收盤後精選）以「測試」標題傳一次，並記為今天已發送")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    if not args.dry_run and (not token or not chats):
        print("尚未設定 TELEGRAM_BOT_TOKEN／TELEGRAM_CHAT_ID，略過 Telegram")
        return
    if args.test:
        ok = all(send(token, c, "✅ 妖股雷達 Telegram 通知設定成功。之後每個交易日 12:30 推播隔日沖、13:40 推播精選 3 檔。",
                      button("打開妖股雷達")) for c in chats)
        print("測試訊息已傳送" if ok else "測試訊息傳送失敗")
        sys.exit(0 if ok else 1)

    with open(N.LATEST, encoding="utf-8") as fh:
        data = json.load(fh)
    if args.sample:
        date = data["trade_date"].replace("-", "")
        head = "🧪 <b>測試</b>：以下是今天推播給 LINE 的內容（Telegram 從下一個交易日起正式同步）\n\n"
        pushed = data.get("overnight_pushed") or {}
        if pushed.get("list"):
            cur = {p["代號"]: p for p in data.get("overnight") or []}
            rows = [{**cur.get(p["代號"], {}), **p} for p in pushed["list"]]
            d2 = {**data, "overnight": rows, "live": {"time": pushed.get("time") or "12:30", "final": False}}
            text, markup = overnight_msg(d2)
            note = "（今天的存檔只有股票與推播價；漲幅、⭐ 為收盤後資料補上，可能與當時略有不同）\n\n"
            if all(send(token, c, head + note + text, markup) for c in chats):
                open(os.path.join(N.CACHE_DIR, f"tg_preview_{date}"), "w").close()
        text, markup = close_msg(data)
        if all(send(token, c, head + text, markup) for c in chats):
            open(os.path.join(N.CACHE_DIR, f"tg_sent_{date}"), "w").close()  # 今天收盤後的正式更新不再重複發送
        print("已傳送今天的推播範例到 Telegram")
        return
    live = data.get("live")
    preview = bool(live and not live.get("final"))
    date = data["trade_date"].replace("-", "")
    jobs = []  # (標記名稱, (文字, 按鈕))
    if preview:
        jobs.append((f"tg_preview_{date}", overnight_msg(data)))
    else:
        jobs.append((f"tg_sent_{date}", close_msg(data)))
        if N.revenue_due(data):
            jobs.append((f"tg_rev_{date}", (e(N.build_revenue_text(data)), button("看完整名單", "#rev"))))
        if N.panic_due(data):
            jobs.append((f"tg_panic_{date}", (e(N.build_panic_text(data)), button("看完整名單"))))
        if N.dip_due(data):
            jobs.append((f"tg_dip_{date}", (e(N.build_dip_text(data)), button("打開 ETF 專區", "#etf"))))

    for marker, (text, markup) in jobs:
        path = os.path.join(N.CACHE_DIR, marker)
        if args.dry_run:
            print(f"========== {marker} ==========\n{text}\n")
            continue
        if os.path.exists(path) and not args.force:
            print(f"{marker} 已發送過，略過")
            continue
        if all(send(token, c, text, markup) for c in chats):
            os.makedirs(N.CACHE_DIR, exist_ok=True)
            open(path, "w").close()
            print(f"已傳送 {marker} 到 Telegram（{len(chats)} 個頻道／群組）")


if __name__ == "__main__":
    main()
