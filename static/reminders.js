// Agenda 頁面開著時的提醒輪詢：每分鐘查一次到點提醒，用桌面通知跳出。
// 頁面關著時的提醒由 notifier.py + launchd 負責，見 README。
(function () {
  var banner = document.getElementById("notify-banner");
  var enableBtn = document.getElementById("notify-enable");

  function updateBanner() {
    if (!banner) return;
    banner.style.display = window.Notification && Notification.permission === "default" ? "flex" : "none";
  }

  if (enableBtn) {
    enableBtn.addEventListener("click", function () {
      Notification.requestPermission().then(updateBanner);
    });
  }
  updateBanner();

  function poll() {
    fetch("/api/due")
      .then(function (res) { return res.json(); })
      .then(function (items) {
        if (!window.Notification || Notification.permission !== "granted") return;
        items.forEach(function (item) {
          new Notification("提醒事項到期", { body: item.title });
        });
      })
      .catch(function () {
        // 本機工具，網路波動忽略即可，下一輪再試。
      });
  }

  poll();
  setInterval(poll, 60000);
})();
