// 密碼欄的即時提示。純輔助——真正的驗證一律在伺服器端，
// 這裡只是讓人不必送出表單才知道字數不夠。
(function () {
  "use strict";

  // 下限由伺服器透過 data-password-meter 帶進來，前後端不各寫一份。
  function minLengthOf(input) {
    return parseInt(input.getAttribute("data-password-meter"), 10) || 12;
  }

  function makeHint(input) {
    var hint = document.createElement("p");
    hint.className = "password-hint";
    hint.setAttribute("aria-live", "polite");
    input.insertAdjacentElement("afterend", hint);
    return hint;
  }

  // 長度提示
  Array.prototype.forEach.call(
    document.querySelectorAll("[data-password-meter]"),
    function (input) {
      var hint = makeHint(input);

      var min = minLengthOf(input);

      function update() {
        var len = input.value.length;
        if (len === 0) {
          hint.textContent = "至少 " + min + " 個字元";
          hint.className = "password-hint";
        } else if (len < min) {
          hint.textContent = "還需要 " + (min - len) + " 個字元（目前 " + len + "）";
          hint.className = "password-hint password-hint-warn";
        } else {
          hint.textContent = "長度足夠（" + len + " 個字元）";
          hint.className = "password-hint password-hint-ok";
        }
      }

      input.addEventListener("input", update);
      update();
    }
  );

  // 兩次輸入是否一致
  Array.prototype.forEach.call(
    document.querySelectorAll("[data-password-match]"),
    function (input) {
      var other = document.getElementById(input.getAttribute("data-password-match"));
      if (!other) return;
      var hint = makeHint(input);

      function update() {
        if (input.value.length === 0) {
          hint.textContent = "";
          hint.className = "password-hint";
        } else if (input.value === other.value) {
          hint.textContent = "兩次輸入一致";
          hint.className = "password-hint password-hint-ok";
        } else {
          hint.textContent = "兩次輸入不一致";
          hint.className = "password-hint password-hint-warn";
        }
      }

      input.addEventListener("input", update);
      other.addEventListener("input", update);
    }
  );
})();
