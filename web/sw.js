// 妖股雷達的 Service Worker：只用來在手機上顯示「盤中到價提醒」的系統通知。
// 不快取任何檔案（網站資料每天更新，快取反而會看到舊資料）。
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
// 點通知：回到已開啟的網站分頁，沒有就開新的
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) if ("focus" in c) return c.focus();
    return self.clients.openWindow("./");
  }));
});
