#!/bin/bash
# 最後一道保險：網站上的交易日還不是今天，就通知 GitHub 更新網站與 LINE。
# 主要觸發來源是 cron-job.org（平日 15:50、16:40）與 GitHub 自己的排程；
# 由 ~/Library/LaunchAgents/com.ghiting.yaogu-trigger.plist 在平日 16:10、17:00 執行。
GH="$HOME/.local/bin/gh"
REPO="ghiting76914-sys/tw-yaogu-screener"
SITE="https://ghiting76914-sys.github.io/tw-yaogu-screener/data/latest.json"
LOG="$HOME/tw-yaogu-screener/output/trigger.log"
mkdir -p "$(dirname "$LOG")"
today="$(date +%F)"

# 網站上的交易日；中午的盤中預覽不算（live 尚未收盤時回傳 preview）
# 網路短暫不通時，等 1 分鐘重試，最多 3 次
for attempt in 1 2 3; do
  site_date=$(curl -s -m 30 "$SITE?t=$(date +%s)" | /usr/bin/python3 -c "
import json, sys
d = json.load(sys.stdin)
live = d.get('live')
print('preview' if live and not live.get('final') else d['trade_date'])" 2>/dev/null)
  running=$("$GH" run list --repo "$REPO" --workflow update.yml --limit 5 --json status \
    --jq '[.[] | select(.status != "completed")] | length' 2>>"$LOG")
  [ -n "$site_date" ] && [ -n "$running" ] && break
  sleep 60
done

if [ "$site_date" = "$today" ]; then
  echo "$(date '+%F %T') 網站已是今天的資料，略過" >> "$LOG"
elif [ "${running:-0}" -gt 0 ]; then
  echo "$(date '+%F %T') 網站還是 ${site_date:-未知}，但已有更新在執行中，略過" >> "$LOG"
else
  "$GH" workflow run update.yml --repo "$REPO" >> "$LOG" 2>&1 \
    && echo "$(date '+%F %T') 網站還是 ${site_date:-未知}，已觸發更新" >> "$LOG" \
    || echo "$(date '+%F %T') 觸發失敗" >> "$LOG"
fi
