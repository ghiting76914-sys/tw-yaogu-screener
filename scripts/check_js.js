// 檢查網頁裡的 JavaScript 有沒有語法錯誤：node scripts/check_js.js web/index.html
const s = require("fs").readFileSync(process.argv[2] || "web/index.html", "utf8");
let bad = 0;
for (const x of s.match(/<script>([\s\S]*?)<\/script>/g) || []) {
  try { new Function(x.replace(/<\/?script>/g, "")); } catch (e) { console.log("語法錯誤：", e.message); bad++; }
}
console.log(bad ? "有錯誤" : "ok");
process.exit(bad ? 1 : 0);
