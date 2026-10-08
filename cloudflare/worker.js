// 妖股雷達「盤中到價提醒」用的即時報價轉接站（Cloudflare Worker）。
// 網頁放在 GitHub Pages，瀏覽器不能直接讀證交所即時行情（沒有開放跨網域），由這裡代為讀取。
// 只轉接證交所 MIS 即時報價，股票代號格式不對就拒絕，避免被拿去轉接其他網站。
// 用法：https://<名稱>.<帳號>.workers.dev/?ex_ch=tse_2330.tw|otc_6488.tw
export default {
  async fetch(request) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
    };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    const ex = new URL(request.url).searchParams.get("ex_ch") || "";
    const ok = /^(tse|otc)_[0-9A-Z]{4,6}\.tw(\|(tse|otc)_[0-9A-Z]{4,6}\.tw){0,79}$/.test(ex);
    if (!ok) return new Response("bad request", { status: 400, headers: cors });
    const upstream = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?json=1&delay=0"
      + `&ex_ch=${encodeURIComponent(ex)}&_=${Date.now()}`;
    const res = await fetch(upstream, {
      headers: { "User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/index.jsp" },
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { ...cors, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
    });
  },
};
