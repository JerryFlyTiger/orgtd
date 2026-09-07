// 唯一的「即時互動」JS：倒數計時、桌面通知、完成/放棄時記錄回後端。
// 頁面本身仍是伺服器渲染，這支腳本只管計時器狀態機。
(function () {
  var el = document.getElementById("pomodoro");
  var nodeId = parseInt(el.dataset.nodeId, 10);
  var longEvery = parseInt(el.dataset.longEvery, 10);
  var durations = {
    focus: parseInt(el.dataset.focusMinutes, 10) * 60,
    short_break: parseInt(el.dataset.shortMinutes, 10) * 60,
    long_break: parseInt(el.dataset.longMinutes, 10) * 60,
  };
  var labels = { focus: "專注", short_break: "短休息", long_break: "長休息" };

  var phase = "focus";
  var remaining = durations[phase];
  var cycleCount = 0;
  var timerId = null;

  var timerDisplay = document.getElementById("timer-display");
  var phaseLabel = document.getElementById("phase-label");
  var startBtn = document.getElementById("start-btn");
  var abandonBtn = document.getElementById("abandon-btn");
  var todayCountEl = document.getElementById("today-count");
  var notifyHint = document.getElementById("notify-hint");

  function mmss(s) {
    var m = Math.floor(s / 60), sec = s % 60;
    return (m < 10 ? "0" + m : m) + ":" + (sec < 10 ? "0" + sec : sec);
  }

  function render() {
    timerDisplay.textContent = mmss(remaining);
    phaseLabel.textContent = labels[phase];
    document.title = mmss(remaining) + " · " + labels[phase];
  }

  function beep() {
    try {
      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      var osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.2, ctx.currentTime);
      osc.start();
      osc.stop(ctx.currentTime + 0.3);
    } catch (e) { /* 沒有 Web Audio 支援就靜音跳過 */ }
  }

  function postRecord(completed, plannedMinutes, actualSeconds) {
    fetch(completed ? "/pomodoro/complete" : "/pomodoro/abandon", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // CSRFProtect 會擋掉沒帶 token 的 POST；JSON 請求走這個標頭而非表單欄位。
        "X-CSRFToken": (document.querySelector('meta[name="csrf-token"]') || {}).content || "",
      },
      body: JSON.stringify({
        node_id: phase === "focus" ? nodeId : null,
        kind: phase,
        planned_minutes: plannedMinutes,
        actual_seconds: actualSeconds,
      }),
    }).catch(function () {});
  }

  function tick() {
    remaining -= 1;
    if (remaining <= 0) { finishPhase(true); return; }
    render();
  }

  function finishPhase(completed) {
    clearInterval(timerId);
    timerId = null;
    var actual = completed ? durations[phase] : durations[phase] - remaining;
    postRecord(completed, durations[phase] / 60, actual);

    if (completed) {
      beep();
      if (window.Notification && Notification.permission === "granted") {
        new Notification("番茄鐘完成", { body: labels[phase] + "結束" });
      }
      if (phase === "focus") {
        cycleCount += 1;
        todayCountEl.textContent = String(parseInt(todayCountEl.textContent, 10) + 1);
        phase = cycleCount % longEvery === 0 ? "long_break" : "short_break";
      } else {
        phase = "focus";
      }
    }
    // 放棄時停留在原本階段，remaining 重置即可重新開始這一段。

    remaining = durations[phase];
    render();
    startBtn.style.display = "inline-block";
    abandonBtn.style.display = "none";
  }

  startBtn.addEventListener("click", function () {
    if (window.Notification && Notification.permission === "default") {
      Notification.requestPermission();
    }
    notifyHint.textContent = "";
    startBtn.style.display = "none";
    abandonBtn.style.display = "inline-block";
    timerId = setInterval(tick, 1000);
  });

  abandonBtn.addEventListener("click", function () { finishPhase(false); });

  render();
})();
