// 妖股雷達的即時資料轉接站（Cloudflare Worker）。網頁放在 GitHub Pages，瀏覽器不能直接讀這些資料（沒有開放跨網域）。
//
//   /?ex_ch=tse_2330.tw|otc_6488.tw   證交所 MIS 即時報價（盤中到價提醒、搜尋卡片即時價）；證交所出錯時重試，再不行改用富果報價
//   /bigorders?symbol=2330&min=25     富果逐筆成交 → 今日均價與大單統計（搜尋卡片「當沖／短線檢查」）
//
// 富果金鑰存在 Worker 的祕密變數 FUGLE_KEY（Settings → Variables and Secrets），不會出現在網頁或程式碼裡。
// 富果免費額度每分鐘 60 次、每次最多 500 筆：逐筆成交存在快取裡，之後只抓新增的成交（富果由新到舊排序），
// 30 秒內重複查詢直接用快取；切換大單門檻不再呼叫富果；額度用完時回傳上一次的結果並標示 stale。
const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, OPTIONS" };

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status, headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function misOnce(ex) {
  const res = await fetch("https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0"
    + `&ex_ch=${encodeURIComponent(ex)}&_=${Date.now()}`,
    { headers: { "User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp" } });
  const text = await res.text();
  if (!res.ok || !text.includes("msgArray")) throw new Error(`證交所 HTTP ${res.status}`);
  return text;
}

// 證交所偶爾短暫出錯（HTTP 520 等）：先重試 2 次；仍失敗且只查少數幾檔時，改用富果即時報價（轉成證交所的欄位格式）
async function fugleQuotes(ex, env) {
  const codes = [...new Set(ex.split("|").map((x) => x.slice(4, -3)))];
  const msgArray = [];
  for (const c of codes) {
    const r = await fetch(`https://api.fugle.tw/marketdata/v1.0/stock/intraday/quote/${c}`, { headers: { "X-API-KEY": env.FUGLE_KEY } });
    if (!r.ok) continue;
    const q = await r.json();
    const px = (v) => (v == null ? "-" : String(v));
    const lv = (arr) => (arr || []).map((x) => x.price).join("_") + "_";
    const lim = q.limitUpPrice ?? (q.isLimitUpPrice ? q.lastPrice : null);
    msgArray.push({
      c, n: q.name, z: px(q.lastPrice ?? q.closePrice), y: px(q.previousClose ?? q.referencePrice),
      o: px(q.openPrice), h: px(q.highPrice), l: px(q.lowPrice), u: px(lim), w: px(q.limitDownPrice),
      v: px(q.total && q.total.tradeVolume), b: lv(q.bids), a: lv(q.asks),
      t: q.lastUpdated ? new Date(q.lastUpdated / 1000 + 8 * 3600000).toISOString().slice(11, 19) : "", src: "fugle",
    });
  }
  return JSON.stringify({ msgArray, rtcode: "0000", src: "fugle" });
}

async function mis(url, env) {
  const ex = url.searchParams.get("ex_ch") || "";
  if (!/^(tse|otc)_[0-9A-Z]{4,6}\.tw(\|(tse|otc)_[0-9A-Z]{4,6}\.tw){0,79}$/.test(ex)) return json({ error: "bad request" }, 400);
  let text = null, err = null;
  if (url.searchParams.get("src") === "fugle" && env.FUGLE_KEY) text = await fugleQuotes(ex, env);  // 測試備援用
  for (let i = 0; i < 3 && !text; i++) {
    try { text = await misOnce(ex); } catch (e) { err = e; await new Promise((r) => setTimeout(r, 250 * (i + 1))); }
  }
  if (!text && env.FUGLE_KEY && ex.split("|").length <= 6) text = await fugleQuotes(ex, env);
  if (!text) throw err || new Error("證交所暫時無回應");
  return new Response(text, {
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

// 逐筆成交壓成 [價格, 張數, 方向]：方向 1 = 成交在委賣價（買方主動）、-1 = 在委買價（賣方主動）、0 = 其他
async function getTrades(symbol, env, ctx) {
  const cache = caches.default;
  const key = new Request(`https://cache.local/trades/${symbol}`);
  const hit = await cache.match(key);
  const stored = hit ? await hit.json() : null;
  if (stored && Date.now() - stored.fetchedAt < 30000) return stored;
  const fresh = [];
  let date = null, last = null, maxSerial = 0, done = false;
  try {
    for (let page = 0, offset = 0; page < 20 && !done; page++, offset += 500) {
      const r = await fetch(`https://api.fugle.tw/marketdata/v1.0/stock/intraday/trades/${symbol}?limit=500&offset=${offset}`,
        { headers: { "X-API-KEY": env.FUGLE_KEY } });
      if (!r.ok) throw new Error(r.status === 429 ? "富果查詢太頻繁（每分鐘 60 次）" : `富果 HTTP ${r.status}`);
      const raw = await r.json();
      const data = raw.data || [];
      date = raw.date;
      for (const t of data) {
        if (stored && stored.date === date && t.serial <= stored.maxSerial) { done = true; break; }
        if (!last) last = t.time;
        if (t.serial > maxSerial) maxSerial = t.serial;
        fresh.push([t.price, t.size, t.ask != null && t.price >= t.ask ? 1 : t.bid != null && t.price <= t.bid ? -1 : 0]);
      }
      if (data.length < 500) done = true;
    }
  } catch (e) {
    if (stored) return { ...stored, stale: String(e.message || e) };  // 額度用完：先用上一次的結果
    throw e;
  }
  const same = stored && stored.date === date;
  const out = {
    date, fetchedAt: Date.now(),
    last: last || (same ? stored.last : null),
    maxSerial: Math.max(maxSerial, same ? stored.maxSerial : 0),
    t: same ? fresh.concat(stored.t) : fresh,
  };
  ctx.waitUntil(cache.put(key, new Response(JSON.stringify(out), { headers: { "Cache-Control": "max-age=86400" } })));
  return out;
}

// 日線（ETF 搜尋卡片用）：富果歷史 K 線，最近一年，快取 6 小時
async function candles(url, env, ctx) {
  const symbol = url.searchParams.get("symbol") || "";
  if (!/^[0-9A-Z]{4,6}$/.test(symbol)) return json({ error: "bad symbol" }, 400);
  if (!env.FUGLE_KEY) return json({ error: "尚未設定 FUGLE_KEY" }, 500);
  const cache = caches.default;
  const day = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const key = new Request(`https://cache.local/candles/${symbol}/${day}`);
  const hit = await cache.match(key);
  if (hit) return new Response(hit.body, { headers: { ...Object.fromEntries(hit.headers), ...CORS } });
  const from = new Date(Date.now() - 370 * 86400000).toISOString().slice(0, 10);
  const r = await fetch(`https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/${symbol}?from=${from}&to=${day}&fields=open,high,low,close,volume`,
    { headers: { "X-API-KEY": env.FUGLE_KEY } });
  if (!r.ok) return json({ error: r.status === 429 ? "富果查詢太頻繁（每分鐘 60 次）" : `富果 HTTP ${r.status}` }, 502);
  const raw = await r.json();
  const data = (raw.data || []).map((c) => [c.date, c.open, c.high, c.low, c.close, c.volume]).sort((a, b) => (a[0] < b[0] ? -1 : 1));
  const res = new Response(JSON.stringify({ symbol, name: raw.name || null, data }), {
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "max-age=21600" },
  });
  ctx.waitUntil(cache.put(key, res.clone()));
  return res;
}

async function bigorders(url, env, ctx) {
  const symbol = url.searchParams.get("symbol") || "";
  const min = Math.min(Math.max(parseInt(url.searchParams.get("min") || "25", 10) || 25, 1), 999);
  if (!/^[0-9A-Z]{4,6}$/.test(symbol)) return json({ error: "bad symbol" }, 400);
  if (!env.FUGLE_KEY) return json({ error: "尚未設定 FUGLE_KEY" }, 500);
  const T = await getTrades(symbol, env, ctx);
  // 富果盤中逐筆成交的 size 單位就是「張」（2026-10-14 實測）
  let vol = 0, amt = 0;
  const b = { buyCount: 0, sellCount: 0, buyVol: 0, sellVol: 0, buyAmt: 0, sellAmt: 0, otherVol: 0 };
  for (const [p, v, f] of T.t) {
    if (!(v > 0) || !(p > 0)) continue;
    vol += v; amt += v * p;
    if (v < min) continue;
    if (f === 1) { b.buyCount++; b.buyVol += v; b.buyAmt += v * p; }
    else if (f === -1) { b.sellCount++; b.sellVol += v; b.sellAmt += v * p; }
    else b.otherVol += v;
  }
  return json({
    symbol, min, date: T.date, trades: T.t.length, last: T.last, stale: T.stale || null,
    vwap: vol ? amt / vol : null, volume: Math.round(vol),
    big: {
      ...b, net: Math.round(b.buyVol - b.sellVol),
      ratio: b.sellVol ? b.buyVol / b.sellVol : null,
      buyAvg: b.buyVol ? b.buyAmt / b.buyVol : null,
      sellAvg: b.sellVol ? b.sellAmt / b.sellVol : null,
    },
  });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });
    const url = new URL(request.url);
    try {
      if (url.pathname === "/bigorders") return await bigorders(url, env, ctx);
      if (url.pathname === "/candles") return await candles(url, env, ctx);
      return await mis(url, env);
    } catch (e) {
      return json({ error: String(e.message || e).slice(0, 120) }, 502);  // 一律帶跨網域標頭，網頁才看得到原因
    }
  },
};
