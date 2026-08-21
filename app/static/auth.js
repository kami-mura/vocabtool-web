(function () {
  const msg = document.getElementById("msg");
  const miniTabs = document.querySelector(".mini-tabs");
  const loginForm = document.getElementById("form-login");
  const regForm = document.getElementById("form-register");
  const resetForm = document.getElementById("form-reset");
  const loginTab = document.getElementById("tab-login");
  const registerTab = document.getElementById("tab-register");

  /* ---------- 夜间模式 ---------- */
  function applyTheme(dark) {
    if (window.vocabTheme) window.vocabTheme.apply(dark);
    else document.documentElement.dataset.theme = dark ? "dark" : "light";
  }
  const authThemeToggle = document.getElementById("auth-theme-toggle");
  if (authThemeToggle) {
    authThemeToggle.onclick = function () {
      const dark = document.documentElement.dataset.theme !== "dark";
      if (window.vocabTheme) window.vocabTheme.setManual(dark);
      else applyTheme(dark);
    };
  }
  try {
    if (window.vocabTheme) window.vocabTheme.sync();
    else {
      const saved = localStorage.getItem("vocabtool.theme");
      applyTheme(saved
        ? saved === "dark"
        : window.matchMedia("(prefers-color-scheme: dark)").matches);
    }
  } catch (_) { /* 隐私模式等场景忽略 */ }

  function show(text, ok) {
    msg.textContent = text;
    msg.className = "msg " + (ok ? "ok" : "err");
  }

  function showPanel(panel) {
    loginForm.hidden = panel !== "login";
    regForm.hidden = panel !== "register";
    resetForm.hidden = panel !== "reset";
    miniTabs.hidden = panel === "reset";
    loginTab.classList.toggle("active", panel === "login");
    registerTab.classList.toggle("active", panel === "register");
    show("", true);
  }

  loginTab.onclick = function () { showPanel("login"); };
  registerTab.onclick = function () { showPanel("register"); };
  document.getElementById("show-reset").onclick = function () {
    document.getElementById("reset-email").value =
      document.getElementById("login-email").value;
    showPanel("reset");
  };
  document.getElementById("reset-back").onclick = function () {
    showPanel("login");
  };

  function startCountdown(button) {
    let seconds = 60;
    button.disabled = true;
    button.textContent = seconds + " 秒后重发";
    const timer = setInterval(function () {
      seconds -= 1;
      if (seconds <= 0) {
        clearInterval(timer);
        button.disabled = false;
        button.textContent = "重新发送";
      } else {
        button.textContent = seconds + " 秒后重发";
      }
    }, 1000);
  }

  async function requestCode(button, emailInput, path) {
    const email = emailInput.value.trim();
    if (!email || !email.includes("@")) {
      show("请先填写正确的邮箱", false);
      return;
    }
    button.disabled = true;
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || "验证码发送失败");
      show(data.message || "验证码已发送，请检查邮箱和垃圾邮件文件夹", true);
      startCountdown(button);
    } catch (err) {
      button.disabled = false;
      show(err.message || "验证码发送失败", false);
    }
  }

  const sendRegistrationCode = document.getElementById("send-code");
  if (sendRegistrationCode) {
    sendRegistrationCode.onclick = function () {
      requestCode(
        sendRegistrationCode,
        document.getElementById("reg-email"),
        "/api/register/request-code"
      );
    };
  }
  const sendResetCode = document.getElementById("send-reset-code");
  if (sendResetCode) {
    sendResetCode.onclick = function () {
      requestCode(
        sendResetCode,
        document.getElementById("reset-email"),
        "/api/password-reset/request-code"
      );
    };
  }

  async function submitAuth(path, email, password, code, button, busyText) {
    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = busyText;
    try {
      const res = await fetch(path, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, code: code || "" }),
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || "请求失败");
      location.replace("/");
    } catch (err) {
      const message = err instanceof TypeError
        ? "无法连接服务器，请检查本地服务是否已启动"
        : (err.message || "请求失败，请稍后重试");
      show(message, false);
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  loginForm.onsubmit = function (event) {
    event.preventDefault();
    submitAuth(
      "/api/login",
      document.getElementById("login-email").value,
      document.getElementById("login-password").value,
      "",
      document.getElementById("login-submit"),
      "登录中…"
    );
  };

  regForm.onsubmit = function (event) {
    event.preventDefault();
    submitAuth(
      "/api/register",
      document.getElementById("reg-email").value,
      document.getElementById("reg-password").value,
      document.getElementById("reg-code")
        ? document.getElementById("reg-code").value
        : "",
      regForm.querySelector('button[type="submit"]'),
      "注册中…"
    );
  };

  resetForm.onsubmit = async function (event) {
    event.preventDefault();
    const button = document.getElementById("reset-submit");
    button.disabled = true;
    button.textContent = "重置中…";
    const email = document.getElementById("reset-email").value.trim();
    try {
      const res = await fetch("/api/password-reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          code: document.getElementById("reset-code").value,
          password: document.getElementById("reset-password").value,
        }),
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || "密码重置失败");
      document.getElementById("login-email").value = email;
      document.getElementById("login-password").value = "";
      showPanel("login");
      show(data.message || "密码已重置，请使用新密码登录", true);
    } catch (err) {
      show(err.message || "密码重置失败", false);
    } finally {
      button.disabled = false;
      button.textContent = "重置密码";
    }
  };
})();
