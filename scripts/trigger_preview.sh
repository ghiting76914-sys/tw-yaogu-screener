#!/bin/bash
# 即時行情更新的保險（主要觸發來源是 cron-job.org），由 ~/Library/LaunchAgents/com.ghiting.yaogu-preview.plist 執行：
#   平日 12:35：12:25 之後還沒有任何更新開始，就觸發 12:30 盤中預覽（隔日沖推播）
#   平日 13:45：13:35 之後還沒有任何更新開始，就觸發 13:40 收盤後名單（精選 3 檔＋明日強勢候選，趕上盤後定價）
GH="$HOME/.local/bin/gh"
REPO="ghiting76914-sys/tw-yaogu-screener"
LOG="$HOME/tw-yaogu-screener/output/trigger.log"
mkdir -p "$(dirname "$LOG")"
if [ "$(date +%H)" -ge 13 ]; then
  since="$(date -u +%Y-%m-%d)T05:35:00Z"; label="收盤後名單"; after="13:35"   # 台灣時間 13:35
else
  since="$(date -u +%Y-%m-%d)T04:25:00Z"; label="盤中預覽"; after="12:25"     # 台灣時間 12:25
fi
# 網路短暫不通時，等 1 分鐘重試，最多 3 次
for attempt in 1 2 3; do
  started=$("$GH" run list --repo "$REPO" --workflow update.yml --limit 10 --json createdAt \
    --jq "[.[] | select(.createdAt >= \"$since\")] | length" 2>>"$LOG")
  [ -n "$started" ] && break
  sleep 60
done
if [ -z "$started" ]; then
  echo "$(date '+%F %T') $label：無法查詢 GitHub" >> "$LOG"
elif [ "$started" -gt 0 ]; then
  echo "$(date '+%F %T') $label：已經有更新在 $after 後開始，略過" >> "$LOG"
else
  "$GH" workflow run update.yml --repo "$REPO" -f preview=true >> "$LOG" 2>&1 \
    && echo "$(date '+%F %T') $label：已觸發" >> "$LOG" \
    || echo "$(date '+%F %T') $label：觸發失敗" >> "$LOG"
fi
