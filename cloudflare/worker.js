// 妖股雷達的即時資料轉接站（Cloudflare Worker）。網頁放在 GitHub Pages，瀏覽器不能直接讀這些資料（沒有開放跨網域）。
//
//   /?ex_ch=tse_2330.tw|otc_6488.tw   證交所 MIS 即時報價（盤中到價提醒、搜尋卡片即時價）
//   /bigorders?symbol=2330&min=20     富果逐筆成交 → 今日均價與大單統計（搜尋卡片「盤中大單」）
//
// 富果金鑰存在 Worker 的祕密變數 FUGLE_KEY（Settings → Variables and Secrets），不會出現在網頁或程式碼裡。
// 只接受固定格式的參數，避免被拿去轉接其他網站；大單統計快取 60 秒，避免超過富果免費額度（每分鐘 60 次）。
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS" };

function json(obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });
}

async function mis(url) {
  const ex = url.searchParams.get("ex_ch") || "";
  if (!/^(tse|otc)_[0-9A-Z]{4,6}\.tw(\|(tse|otc)_[0-9A-Z]{4,6}\.tw){0,79}$/.test(ex)) {
    return new Response("bad request", { status: 400, headers: CORS });
  }
  const upstream = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0"
    + `&ex_ch=${encodeURIComponent(ex)}&_=${Date.now()}`;
  const res = await fetch(upstream, {
    headers: { "User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp" },
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

// 逐筆成交：成交價 ≥ 委賣價算「外盤（買方主動）」，≤ 委買價算「內盤（賣方主動）」，其餘不分
async function bigorders(url, env, ctx) {
  const symbol = url.searchParams.get("symbol") || "";
  const min = Math.min(Math.max(parseInt(url.searchParams.get("min") || "20", 10) || 20, 1), 999);
  if (!/^[0-9A-Z]{4,6}$/.test(symbol)) return json({ error: "bad symbol" }, 400);
  if (!env.FUGLE_KEY) return json({ error: "尚未設定 FUGLE_KEY" }, 500);

  const cache = caches.default;
  const key = new Request(`https://cache.local/bigorders/${symbol}/${min}`);
  const hit = await cache.match(key);
  if (hit) return new Response(hit.body, { headers: { ...Object.fromEntries(hit.headers), ...CORS } });

  const trades = [];
  let offset = 0, raw = null;
  for (let page = 0; page < 30; page++) {
    const r = await fetch(`https://api.fugle.tw/marketdata/v1.0/stock/intraday/trades/${symbol}?limit=500&offset=${offset}`,
      { headers: { "X-API-KEY": env.FUGLE_KEY } });
    if (!r.ok) return json({ error: `富果 HTTP ${r.status}`, detail: (await r.text()).slice(0, 200) }, 502);
    raw = await r.json();
    const data = raw.data || [];
    trades.push(...data);
    if (data.length < 500) break;
    offset += 500;
  }
  // 富果盤中逐筆成交的 size 單位就是「張」（2026-10-14 實測：size 1、累計 volume 9879 張）
  const lots = (t) => t.size || 0;
  let vol = 0, amt = 0;
  const big = { buyCount: 0, sellCount: 0, buyVol: 0, sellVol: 0, buyAmt: 0, sellAmt: 0, otherVol: 0 };
  for (const t of trades) {
    const v = lots(t), p = t.price;
    if (!(v > 0) || !(p > 0)) continue;
    vol += v; amt += v * p;
    if (v < min) continue;
    if (t.ask != null && p >= t.ask) { big.buyCount++; big.buyVol += v; big.buyAmt += v * p; }
    else if (t.bid != null && p <= t.bid) { big.sellCount++; big.sellVol += v; big.sellAmt += v * p; }
    else big.otherVol += v;
  }
  const out = {
    symbol, min, date: raw && raw.date, trades: trades.length, last: trades[0] ? trades[0].time : null,
    vwap: vol ? amt / vol : null, volume: Math.round(vol),
    big: {
      ...big, buyVol: Math.round(big.buyVol), sellVol: Math.round(big.sellVol), otherVol: Math.round(big.otherVol),
      net: Math.round(big.buyVol - big.sellVol),
      ratio: big.sellVol ? big.buyVol / big.sellVol : null,
      buyAvg: big.buyVol ? big.buyAmt / big.buyVol : null,
      sellAvg: big.sellVol ? big.sellAmt / big.sellVol : null,
    },
    sample: trades[0] || null,
  };
  const res = json(out, 200, { "Cache-Control": "max-age=60" });
  ctx.waitUntil(cache.put(key, res.clone()));
  return res;
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    if (url.pathname === "/bigorders") return bigorders(url, env, ctx);
    return mis(url);
  },
};
