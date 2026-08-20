(function () {
  const isLoggedIn = document.body.dataset.loggedIn === "true";
  const form = document.getElementById("landing-search-form");
  const box = document.getElementById("landing-search-result");
  if (!form || !box) return;

  /* ---------- 手机端底部导航：一个 Tab 一个主界面 ---------- */
  const mobileNav = document.getElementById("mobile-nav");
  const mobileNavButtons = document.querySelectorAll(".mobile-nav-btn[data-mobile-view]");
  const mobileMQ = window.matchMedia("(max-width: 820px)");
  let currentMobileView = null;

  const mobileSections = [];
  function registerMobileSection(id, view) {
    const el = document.getElementById(id);
    if (el) mobileSections.push({ el, view, originalHidden: el.hidden });
  }
  registerMobileSection("landing-hero", "search");
  registerMobileSection("real-review", "study");
  registerMobileSection("today-overview", "study");
  registerMobileSection("guest-demo-cards", "study");
  registerMobileSection("guest-demo-article", "article");
  registerMobileSection("real-article-panel", "article");
  registerMobileSection("manage-area", "cards");

  function isMobileLayout() {
    return mobileMQ.matches;
  }

  /* 手机端与桌面端学习区顺序各自独立：
     桌面端数据卡（今日学习）在学习卡片上方，手机端学习卡片在最上方。 */
  function arrangeLearningOrder() {
    const realReview = document.getElementById("real-review");
    const todayOverview = document.getElementById("today-overview");
    if (!realReview || !todayOverview) return;
    if (isMobileLayout()) {
      if (realReview.nextElementSibling !== todayOverview) {
        realReview.insertAdjacentElement("afterend", todayOverview);
      }
    } else if (todayOverview.nextElementSibling !== realReview) {
      realReview.insertAdjacentElement("beforebegin", todayOverview);
    }
  }

  function restoreMobileSections() {
    mobileSections.forEach(({ el, originalHidden }) => {
      el.hidden = originalHidden;
    });
  }

  function applyMobileView(view, scrollTop) {
    if (!mobileNav || !isMobileLayout()) {
      restoreMobileSections();
      currentMobileView = null;
      return;
    }
    currentMobileView = view;
    mobileSections.forEach(({ el, view: sectionView, originalHidden }) => {
      el.hidden = sectionView !== view || originalHidden;
    });
    mobileNavButtons.forEach((btn) => {
      const active = btn.dataset.mobileView === view;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-current", active ? "page" : "false");
    });
    // 卡片 tab：默认展开生词库面板。
    if (view === "cards" && isLoggedIn && realShowManagePanelOnly) {
      const visiblePanel = document.querySelector(".manage-panel:not([hidden])");
      if (!visiblePanel) realShowManagePanelOnly("real-library", true);
    }
    if (view === "study" && isLoggedIn) loadTodayOverview();
    if (scrollTop) window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function initMobileNav() {
    if (!mobileNav) return;
    mobileNavButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        applyMobileView(btn.dataset.mobileView, true);
      });
    });
    const defaultView = isLoggedIn ? "study" : "search";
    const applyCurrent = () => applyMobileView(currentMobileView || defaultView, false);
    const onMqChange = (event) => {
      arrangeLearningOrder();
      if (event.matches) {
        applyCurrent();
      } else {
        restoreMobileSections();
        currentMobileView = null;
        mobileNavButtons.forEach((btn) => btn.classList.remove("active"));
      }
    };
    if (typeof mobileMQ.addEventListener === "function") {
      mobileMQ.addEventListener("change", onMqChange);
    } else {
      mobileMQ.addListener(onMqChange);
    }
    arrangeLearningOrder();
    if (isMobileLayout()) applyCurrent();
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* 括号标注只用于 AI 消歧；发音/高亮只用括号前的单词 */
  function plainTargetWord(word) {
    return String(word || "")
      .replace(/\s*[（(【\[][^)）\]】]*[)）\]】]/g, "")
      .trim();
  }

  /* 常见不规则词形（与后端 vocab._IRREGULAR 对齐）：变体 -> 词头 */
  const IRREGULAR_FORMS = {
    "me": "i", "my": "i", "him": "he", "his": "he", "her": "she", "hers": "she",
    "its": "it", "us": "we", "our": "we", "ours": "we", "them": "they",
    "their": "they", "theirs": "they", "whom": "who", "whose": "who",
    "myself": "i", "yourself": "you", "yourselves": "you", "himself": "he",
    "herself": "she", "itself": "it", "ourselves": "we", "themselves": "they",
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be",
    "been": "be", "being": "be", "has": "have", "had": "have", "having": "have",
    "does": "do", "did": "do", "done": "do", "doing": "do",
    "went": "go", "gone": "go", "going": "go",
    "said": "say", "says": "say", "saying": "say",
    "got": "get", "gotten": "get", "gets": "get", "getting": "get",
    "made": "make", "makes": "make", "making": "make",
    "knew": "know", "known": "know", "knows": "know", "knowing": "know",
    "thought": "think", "thinks": "think", "thinking": "think",
    "took": "take", "taken": "take", "takes": "take", "taking": "take",
    "saw": "see", "seen": "see", "sees": "see", "seeing": "see",
    "came": "come", "comes": "come", "coming": "come",
    "found": "find", "finds": "find", "finding": "find",
    "gave": "give", "given": "give", "gives": "give", "giving": "give",
    "told": "tell", "tells": "tell", "telling": "tell",
    "became": "become", "becomes": "become", "becoming": "become",
    "left": "leave", "leaves": "leave", "leaving": "leave",
    "felt": "feel", "feels": "feel", "feeling": "feel",
    "brought": "bring", "brings": "bring", "bringing": "bring",
    "began": "begin", "begun": "begin", "begins": "begin", "beginning": "begin",
    "kept": "keep", "keeps": "keep", "keeping": "keep",
    "held": "hold", "holds": "hold", "holding": "hold",
    "wrote": "write", "written": "write", "writes": "write", "writing": "write",
    "stood": "stand", "stands": "stand", "standing": "stand",
    "heard": "hear", "hears": "hear", "hearing": "hear",
    "meant": "mean", "means": "mean", "meaning": "mean",
    "met": "meet", "meets": "meet", "meeting": "meet",
    "ran": "run", "runs": "run", "running": "run",
    "paid": "pay", "pays": "pay", "paying": "pay",
    "sat": "sit", "sits": "sit", "sitting": "sit",
    "spoke": "speak", "spoken": "speak", "speaks": "speak", "speaking": "speak",
    "led": "lead", "leads": "lead", "leading": "lead",
    "grew": "grow", "grown": "grow", "grows": "grow", "growing": "grow",
    "lost": "lose", "loses": "lose", "losing": "lose",
    "fell": "fall", "fallen": "fall", "falls": "fall", "falling": "fall",
    "sent": "send", "sends": "send", "sending": "send",
    "built": "build", "builds": "build", "building": "build",
    "understood": "understand", "understands": "understand", "understanding": "understand",
    "drew": "draw", "drawn": "draw", "draws": "draw", "drawing": "draw",
    "broke": "break", "broken": "break", "breaks": "break", "breaking": "break",
    "spent": "spend", "spends": "spend", "spending": "spend",
    "rose": "rise", "risen": "rise", "rises": "rise", "rising": "rise",
    "drove": "drive", "driven": "drive", "drives": "drive", "driving": "drive",
    "bought": "buy", "buys": "buy", "buying": "buy",
    "wore": "wear", "worn": "wear", "wears": "wear", "wearing": "wear",
    "chose": "choose", "chosen": "choose", "chooses": "choose", "choosing": "choose",
    "flew": "fly", "flown": "fly", "flies": "fly", "flying": "fly",
    "caught": "catch", "catches": "catch", "catching": "catch",
    "ate": "eat", "eaten": "eat", "eats": "eat", "eating": "eat",
    "slept": "sleep", "sleeps": "sleep", "sleeping": "sleep",
    "swam": "swim", "swum": "swim", "swims": "swim", "swimming": "swim",
    "sang": "sing", "sung": "sing", "sings": "sing", "singing": "sing",
    "taught": "teach", "teaches": "teach", "teaching": "teach",
    "sold": "sell", "sells": "sell", "selling": "sell",
    "fought": "fight", "fights": "fight", "fighting": "fight",
    "threw": "throw", "thrown": "throw", "throws": "throw", "throwing": "throw",
    "won": "win", "wins": "win", "winning": "win",
    "rode": "ride", "ridden": "ride", "rides": "ride", "riding": "ride",
    "children": "child", "men": "man", "women": "woman",
    "feet": "foot", "teeth": "tooth", "mice": "mouse"
  };

  function targetSurfaceForms(word) {
    const w = String(word || "").trim().toLowerCase();
    if (!/^[a-z]+$/.test(w)) return [w];
    const heads = new Set([w]);
    for (const [variant, head] of Object.entries(IRREGULAR_FORMS)) {
      if (variant === w) heads.add(head);
    }
    let base = w;
    if (base.endsWith("ies") && base.length > 4) heads.add(base.slice(0, -3) + "y");
    if (base.endsWith("ied") && base.length > 4) heads.add(base.slice(0, -3) + "y");
    if (base.endsWith("es") && base.length > 3) heads.add(base.slice(0, -2));
    if (base.endsWith("s") && !base.endsWith("ss") && base.length > 3) heads.add(base.slice(0, -1));
    if (base.endsWith("ing") && base.length > 5) {
      let b = base.slice(0, -3);
      if (b.length > 1 && b[b.length - 1] === b[b.length - 2]) b = b.slice(0, -1);
      heads.add(b);
      heads.add(b + "e");
    }
    if (base.endsWith("ed") && base.length > 4) {
      let b = base.slice(0, -2);
      if (b.length > 1 && b[b.length - 1] === b[b.length - 2]) b = b.slice(0, -1);
      heads.add(b);
      heads.add(b + "e");
    }
    const forms = new Set();
    for (const head of heads) {
      forms.add(head);
      for (const [variant, h] of Object.entries(IRREGULAR_FORMS)) {
        if (h === head) forms.add(variant);
      }
      forms.add(head + "s");
      forms.add(head + "ed");
      forms.add(head + "ing");
      if (/(s|x|z|ch|sh)$/.test(head)) forms.add(head + "es");
      if (head.endsWith("e") && head.length > 2) {
        forms.add(head + "d");
        forms.add(head.slice(0, -1) + "ing");
      }
      if (head.endsWith("y") && head.length > 2 && !/[aeiou]/.test(head[head.length - 2])) {
        forms.add(head.slice(0, -1) + "ies");
        forms.add(head.slice(0, -1) + "ied");
      }
      if (
        head.length >= 3 &&
        !/[aeiouwxy]/.test(head[head.length - 1]) &&
        /[aeiou]/.test(head[head.length - 2]) &&
        !/[aeiou]/.test(head[head.length - 3])
      ) {
        forms.add(head + head[head.length - 1] + "ed");
        forms.add(head + head[head.length - 1] + "ing");
      }
    }
    return [...forms].sort((a, b) => b.length - a.length);
  }

  function escapeRegExp(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function highlightTargetForms(html, phrase) {
    if (!phrase || /\s/.test(phrase)) return html;
    const forms = targetSurfaceForms(phrase);
    if (!forms.length) return html;
    const pattern = new RegExp(
      "(?<![A-Za-z0-9])(?:" + forms.map(escapeRegExp).join("|") + ")(?![A-Za-z0-9])",
      "gi"
    );
    return html.replace(pattern, '<span class="target-word">$&</span>');
  }

  function renderMarkdown(s, phrase) {
    let html = escapeHtml(s).replace(/\*\*(.+?)\*\*/g, '<span class="target-word">$1</span>');
    // 短语兜底高亮：后端没加 ** 时，把整条短语也标出来（只处理含空格的短语）。
    if (phrase && /\s/.test(phrase)) {
      const esc = escapeHtml(phrase);
      const escapedRe = esc.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const alreadyBolded = new RegExp("\\*\\*" + escapedRe + "\\*\\*", "i").test(s);
      if (!alreadyBolded && html.includes(esc)) {
        const re = new RegExp(
          "(?<![A-Za-z0-9])" + escapedRe + "(?![A-Za-z0-9])",
          "gi"
        );
        html = html.replace(re, '<span class="target-word">$&</span>');
      }
    }
    // 旧卡兜底：后端没存 ** 时，按目标词及其自然变形在页面端动态高亮。
    if (!html.includes("target-word")) {
      html = highlightTargetForms(html, phrase);
    }
    return html;
  }

  function renderInlineMarkdown(s) {
    return escapeHtml(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\n])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/`([^`\n]+)`/g, "<code>$1</code>");
  }

  function renderRichText(text) {
    const lines = String(text || "").split("\n");
    let html = "";
    let listTag = "";
    const closeList = () => {
      if (listTag) {
        html += "</" + listTag + ">";
        listTag = "";
      }
    };
    for (const raw of lines) {
      const line = raw.trim();
      if (/^[-*•]\s+/.test(line)) {
        if (listTag !== "ul") { closeList(); html += "<ul>"; listTag = "ul"; }
        html += "<li>" + renderInlineMarkdown(line.replace(/^[-*•]\s+/, "")) + "</li>";
        continue;
      }
      if (/^\d+[.)]\s+/.test(line)) {
        if (listTag !== "ol") { closeList(); html += "<ol>"; listTag = "ol"; }
        html += "<li>" + renderInlineMarkdown(line.replace(/^\d+[.)]\s+/, "")) + "</li>";
        continue;
      }
      closeList();
      if (/^###\s+/.test(line)) { html += "<h4>" + renderInlineMarkdown(line.replace(/^###\s+/, "")) + "</h4>"; continue; }
      if (/^##\s+/.test(line)) { html += "<h3>" + renderInlineMarkdown(line.replace(/^##\s+/, "")) + "</h3>"; continue; }
      if (/^#\s+/.test(line)) { html += "<h2>" + renderInlineMarkdown(line.replace(/^#\s+/, "")) + "</h2>"; continue; }
      if (/^>\s+/.test(line)) { html += "<blockquote>" + renderInlineMarkdown(line.replace(/^>\s+/, "")) + "</blockquote>"; continue; }
      html += line ? "<p>" + renderInlineMarkdown(line) + "</p>" : "";
    }
    closeList();
    return html;
  }

  function newActionId() {
    return window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  }

  /* ---------- 双击查词 ---------- */
  const floatingLookup = document.getElementById("floating-lookup");
  const floatingLookupBody = document.getElementById("floating-lookup-body");
  let floatingQuery = "";
  let floatingCloseTimer = null;

  function closeFloatingLookup() {
    floatingQuery = "";
    floatingLookup.hidden = true;
    floatingLookupBody.innerHTML = "";
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-close-floating-lookup]")) {
      closeFloatingLookup();
      return;
    }
    if (floatingLookup.hidden || e.target.closest("#floating-lookup")) return;
    clearTimeout(floatingCloseTimer);
    floatingCloseTimer = setTimeout(closeFloatingLookup, 300);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !floatingLookup.hidden) closeFloatingLookup();
  });

  async function lookupFloating(text, x, y) {
    clearTimeout(floatingCloseTimer);
    if (floatingQuery === text) return;
    floatingQuery = text;
    floatingLookup.hidden = false;
    floatingLookup.setAttribute("aria-busy", "true");
    floatingLookupBody.innerHTML =
      '<div class="reader-lookup-loading">正在查询 <strong>' + escapeHtml(text) + "</strong>…</div>";
    // 固定在屏幕中央（由 CSS 定位），不再跟随点击坐标。
    floatingLookup.style.left = "";
    floatingLookup.style.top = "";
    floatingLookup.style.transform = "";
    const isTerm =
      (/^[A-Za-z][A-Za-z'’\-]*([ ][A-Za-z][A-Za-z'’\-]*){0,4}$/.test(text) && text.length <= 40) ||
      (/^[A-Za-z][A-Za-z'’\- ]*\(\s*[A-Za-z.]{1,8}\s*\)$/.test(text) && text.length <= 40) ||
      (text.replace(/ /g, "").length <= 12 && /^[\u4e00-\u9fff、/·\- ]+$/.test(text));
    try {
      let html;
      if (isTerm || !isLoggedIn) {
        const res = await fetch("/api/lookups", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "查询失败");
        const lookup = data.lookup || {};
        const displayWord =
          (data.spelling_note && data.spelling_note.corrected) || lookup.query || text;
        html =
          '<div class="lookup-head">' +
          '<div class="lookup-word"><mark class="word-highlight">' + escapeHtml(displayWord) + "</mark>" +
          ' <button class="demo-audio lookup-audio" data-real-audio="' + escapeHtml(displayWord) +
          '" type="button" aria-label="朗读发音">▶</button></div>' +
          (lookup.ngsl_rank
            ? '<div class="search-rank">NGSL 排名 #' + Number(lookup.ngsl_rank) + "</div>"
            : "") +
          "</div>" +
          '<div class="lookup-explanation">' +
          renderLookupExplanation(lookup.explanation || data.ai_error || "暂无解释") +
          "</div>" + lookupCardActionHtml(lookup);
        warmUpAudioTexts([displayWord].concat(lookupSentenceTexts(lookup.explanation)));
      } else {
        const res = await fetch("/api/lookups/question", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: text }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "查询失败");
        html = '<div class="qa-rich">' + renderRichText(data.answer || "") + "</div>" +
          (data.lookup ? lookupCardActionHtml(data.lookup) : "");
      }
      floatingLookupBody.innerHTML =
        '<div class="lookup-answer card">' + html +
        '<div class="lookup-actions" style="margin-top:12px">' +
        '<button class="small" data-close-floating-lookup type="button">关闭</button></div></div>';
    } catch (err) {
      floatingQuery = "";
      floatingLookupBody.innerHTML =
        '<div class="lookup-explanation muted">' + escapeHtml(err.message) + "</div>";
    } finally {
      floatingLookup.setAttribute("aria-busy", "false");
    }
  }

  document.addEventListener("dblclick", (e) => {
    const target = e.target;
    if (target === searchInput && target.selectionStart != null) {
      const selected = target.value
        .substring(target.selectionStart, target.selectionEnd)
        .replace(/\s+/g, " ")
        .trim();
      if (selected && selected.length <= 200) {
        lookupFloating(selected, e.clientX, e.clientY);
        return;
      }
    }
    if (e.target.closest("input, textarea, select, button, a")) return;
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    const pad = 4;
    if (
      e.clientX < rect.left - pad ||
      e.clientX > rect.right + pad ||
      e.clientY < rect.top - pad ||
      e.clientY > rect.bottom + pad
    ) {
      return;
    }
    const text = selection.toString().replace(/\s+/g, " ").trim();
    if (!text || text.length > 200) return;
    lookupFloating(text, e.clientX, e.clientY);
  });

  /* ---------- 登录后：个人菜单 / 每日新学习 / 退出 ---------- */
  function applyLandingTheme(dark) {
    if (window.vocabTheme) window.vocabTheme.apply(dark);
    else document.documentElement.dataset.theme = dark ? "dark" : "light";
  }
  const accountThemeToggle = document.getElementById("account-theme-toggle");
  if (accountThemeToggle) {
    accountThemeToggle.onclick = () => {
      const dark = document.documentElement.dataset.theme !== "dark";
      if (window.vocabTheme) window.vocabTheme.setManual(dark);
      else applyLandingTheme(dark);
    };
  }
  try {
    if (window.vocabTheme) window.vocabTheme.sync();
    else {
      const saved = localStorage.getItem("vocabtool.theme");
      applyLandingTheme(saved
        ? saved === "dark"
        : window.matchMedia("(prefers-color-scheme: dark)").matches);
    }
  } catch (_) { /* 隐私模式等场景忽略 */ }

  const accountPanel = document.getElementById("account-menu-panel");
  const accountApiProvider = document.getElementById("account-api-provider");
  const accountApiKeyInput = document.getElementById("account-api-key-input");
  const accountApiKeySave = document.getElementById("account-api-key-save");
  const accountApiKeyDelete = document.getElementById("account-api-key-delete");
  const accountApiKeyStatus = document.getElementById("account-api-key-status");
  let accountApiKeyLoaded = false;
  let configuredApiProviderLabel = "";

  async function loadAccountApiKeyStatus(force) {
    if (!isLoggedIn || !accountApiKeyStatus || (accountApiKeyLoaded && !force)) return;
    try {
      const res = await fetch("/api/ai-credentials");
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "状态加载失败");
      accountApiKeyLoaded = true;
      configuredApiProviderLabel = data.provider_label || "";
      if (data.configured && accountApiProvider && data.provider) {
        accountApiProvider.value = data.provider;
      }
      accountApiKeyStatus.textContent = data.configured
        ? "已配置 " + configuredApiProviderLabel + " Key" + (data.key_hint ? "（尾号 " + data.key_hint + "）" : "")
        : "未配置，当前使用网站免费额度";
      accountApiKeyStatus.className = "account-api-key-status" + (data.configured ? " ok" : "");
      if (accountApiKeyDelete) accountApiKeyDelete.hidden = !data.configured;
    } catch (err) {
      accountApiKeyStatus.textContent = err.message || "状态加载失败";
      accountApiKeyStatus.className = "account-api-key-status error";
    }
  }

  if (accountApiKeySave) {
    accountApiKeySave.onclick = async () => {
      const apiKey = (accountApiKeyInput && accountApiKeyInput.value || "").trim();
      const provider = accountApiProvider && accountApiProvider.value || "deepseek";
      if (!/^[\x21-\x7e]{10,256}$/.test(apiKey)) {
        accountApiKeyStatus.textContent = "请输入有效的 API Key";
        accountApiKeyStatus.className = "account-api-key-status error";
        return;
      }
      accountApiKeySave.disabled = true;
      try {
        const res = await fetch("/api/ai-credentials", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider: provider, api_key: apiKey }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "保存失败");
        accountApiKeyInput.value = "";
        accountApiKeyLoaded = false;
        await loadAccountApiKeyStatus(true);
      } catch (err) {
        accountApiKeyStatus.textContent = err.message || "保存失败";
        accountApiKeyStatus.className = "account-api-key-status error";
      } finally {
        accountApiKeySave.disabled = false;
      }
    };
  }
  if (accountApiKeyDelete) {
    accountApiKeyDelete.onclick = async () => {
      if (!window.confirm("删除已保存的 " + (configuredApiProviderLabel || "AI") + " API Key？")) return;
      const res = await fetch("/api/ai-credentials", { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        accountApiKeyStatus.textContent = data.detail || "删除失败";
        accountApiKeyStatus.className = "account-api-key-status error";
        return;
      }
      accountApiKeyLoaded = false;
      await loadAccountApiKeyStatus(true);
    };
  }
  // 事件委托：无论按钮何时渲染都能响应，兼容缓存的新旧页面结构。
  document.addEventListener("click", (e) => {
    if (e.target.closest("#account-menu-toggle")) {
      if (!isLoggedIn) {
        location.href = "/login";
        return;
      }
      if (accountPanel) {
        accountPanel.hidden = !accountPanel.hidden;
        if (!accountPanel.hidden) loadAccountApiKeyStatus();
      }
      return;
    }
    if (accountPanel && !accountPanel.hidden && !e.target.closest("#account-menu")) {
      accountPanel.hidden = true;
    }
  });
  const homeLogout = document.getElementById("home-logout");
  if (homeLogout) {
    homeLogout.onclick = async () => {
      try {
        await fetch("/api/logout", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        });
      } catch (_) { /* 忽略 */ }
      location.href = "/";
    };
  }

  /* ---------- 个人：词频分层词汇量小测 ---------- */
  const vocabularyTestModal = document.getElementById("vocabulary-test-modal");
  const vocabularyTestQuestion = document.getElementById("vocabulary-test-question");
  const vocabularyTestResult = document.getElementById("vocabulary-test-result");
  const vocabularyTestLoading = document.getElementById("vocabulary-test-loading");
  const vocabularyTestStatus = document.getElementById("vocabulary-test-status");
  let vocabularyTestQuestions = [];
  let vocabularyTestAnswers = [];
  let vocabularyTestLevel = 5000;

  function closeVocabularyTest() {
    if (vocabularyTestModal) vocabularyTestModal.hidden = true;
  }

  function renderVocabularyTestQuestion() {
    const progress = document.getElementById("vocabulary-test-progress");
    const options = document.getElementById("vocabulary-test-options");
    if (progress) progress.textContent = vocabularyTestLevel + " 词档";
    if (!options) return;
    options.innerHTML = vocabularyTestQuestions.map((item, index) =>
      '<label><input type="checkbox" data-vocabulary-test-index="' + index + '">' +
      '<span lang="en">' + escapeHtml(item.word) + "</span></label>"
    ).join("");
  }

  async function loadVocabularyTestLevel() {
    vocabularyTestLoading.textContent = "正在准备题目…";
    vocabularyTestLoading.hidden = false;
    vocabularyTestQuestion.hidden = true;
    const res = await fetch("/api/words/vocabulary-test?level=" + vocabularyTestLevel);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "题目加载失败");
    vocabularyTestQuestions = data.questions || [];
    if (vocabularyTestQuestions.length !== 5) throw new Error("题目数量不完整");
    vocabularyTestLoading.hidden = true;
    vocabularyTestQuestion.hidden = false;
    renderVocabularyTestQuestion();
  }

  async function startVocabularyTest() {
    if (!vocabularyTestModal) return;
    vocabularyTestModal.hidden = false;
    if (accountPanel) accountPanel.hidden = true;
    vocabularyTestLoading.textContent = "正在准备题目…";
    vocabularyTestLoading.hidden = false;
    vocabularyTestQuestion.hidden = true;
    vocabularyTestResult.hidden = true;
    vocabularyTestStatus.textContent = "";
    vocabularyTestQuestions = [];
    vocabularyTestAnswers = [];
    vocabularyTestLevel = 5000;
    try {
      await loadVocabularyTestLevel();
    } catch (err) {
      vocabularyTestLoading.hidden = true;
      vocabularyTestStatus.textContent = "无法开始测试：" + err.message;
    }
  }

  async function submitVocabularyTestLevel() {
    const submit = document.getElementById("vocabulary-test-submit-level");
    if (submit) submit.disabled = true;
    const checked = new Set(
      Array.from(document.querySelectorAll("[data-vocabulary-test-index]:checked"))
        .map((input) => Number(input.dataset.vocabularyTestIndex))
    );
    const levelAnswers = vocabularyTestQuestions.map((item, index) => ({
      word: item.word,
      known: checked.has(index),
    }));
    vocabularyTestAnswers.push(...levelAnswers);
    const knownCount = checked.size;
    const canMoveUp = knownCount === 5 && vocabularyTestLevel < 21000;
    const canMoveDown = knownCount <= 2 && vocabularyTestLevel > 1000;
    if (canMoveUp || canMoveDown) {
      vocabularyTestLevel += canMoveUp ? 1000 : -1000;
      try {
        await loadVocabularyTestLevel();
      } catch (err) {
        vocabularyTestLoading.hidden = true;
        vocabularyTestStatus.textContent = "无法继续测试：" + err.message;
      } finally {
        if (submit) submit.disabled = false;
      }
      return;
    }
    vocabularyTestQuestion.hidden = true;
    vocabularyTestLoading.hidden = false;
    vocabularyTestLoading.textContent = "正在计算结果…";
    try {
      const res = await fetch("/api/words/vocabulary-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers: vocabularyTestAnswers }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "结果保存失败");
      document.getElementById("vocabulary-test-estimate").textContent = Number(data.known_rank).toLocaleString();
      document.getElementById("vocabulary-test-result-detail").textContent =
        "已保存到个人词汇量（本次回答 " + data.question_count + " 题）";
      const profileInput = document.getElementById("real-profile-known-rank");
      if (profileInput) profileInput.value = Number(data.known_rank);
      vocabularyTestLoading.hidden = true;
      vocabularyTestResult.hidden = false;
    } catch (err) {
      vocabularyTestLoading.hidden = true;
      vocabularyTestStatus.textContent = "计算失败：" + err.message;
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  const vocabularyTestOpen = document.getElementById("account-vocabulary-test");
  if (vocabularyTestOpen) vocabularyTestOpen.onclick = startVocabularyTest;
  const vocabularyTestClose = document.getElementById("vocabulary-test-close");
  if (vocabularyTestClose) vocabularyTestClose.onclick = closeVocabularyTest;
  const vocabularyTestSubmitLevel = document.getElementById("vocabulary-test-submit-level");
  if (vocabularyTestSubmitLevel) vocabularyTestSubmitLevel.onclick = submitVocabularyTestLevel;
  const vocabularyTestRestart = document.getElementById("vocabulary-test-restart");
  if (vocabularyTestRestart) vocabularyTestRestart.onclick = startVocabularyTest;
  if (vocabularyTestModal) {
    vocabularyTestModal.addEventListener("click", (event) => {
      if (event.target === vocabularyTestModal) closeVocabularyTest();
    });
  }
  function setReviewStatus(message) {
    const status = document.getElementById("real-review-status");
    if (status) {
      status.textContent = message;
      status.hidden = !message;
    }
  }

  async function saveRealDailyNewLimit(value) {
    if (!Number.isInteger(value) || value < 0 || value > 200) {
      setReviewStatus("每日新卡数量必须是 0–200 的整数");
      return false;
    }
    try {
      const res = await fetch("/api/cards/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_cards_per_day: value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "保存失败");
      const reviewInput = document.getElementById("real-review-daily-new-limit");
      if (reviewInput) reviewInput.value = value;
      setReviewStatus("每日新学习数量已保存");
      return true;
    } catch (err) {
      setReviewStatus("保存失败：" + err.message);
      return false;
    }
  }

  /* ---------- 登录后：今日记忆卡片（最多 4 张，按队列顺序） ---------- */
  let realReviewQueue = [];
  let realReviewHadCards = false;
  let realReviewCelebrated = false;
  let realReviewRemainingTotal = 0;
  let realReviewRemaining = {};
  let realReviewCanExtraNew = false;
  let realReviewTotalCards = 0;
  let realReviewLoaded = false;
  let realTodayStats = null;
  let todayDashboardData = null;
  let todayOverviewTimer = null;
  let realReviewHistory = [];
  let realReviewLastRatingAt = 0;
  let realReviewCanUndo = false;
  // 队列本地版本号：每次评分/撤回/掩埋/加学等本地改动都递增。
  // loadRealReview 发起时记录版本，返回时若版本已变化则丢弃，
  // 防止“撤回后的旧服务端快照”覆盖用户刚评完分的新队列。
  let realReviewQueueVersion = 0;
  // 制卡/生成文章/词库操作完成时想刷新复习队列，但如果用户正在复习，
  // 直接重拉队列会把“刚评过的学习卡”按服务端顺序顶回队首，造成乱跳。
  // 这里挂起刷新，等队列学完后再拉取。
  let realReviewRefreshPending = false;
  // 正在等待服务器确认的评分数：有请求在途时不算“学完”，
  // 不能放气球/显示恭喜完成。
  let realReviewInFlight = 0;
  let heldReviewAction = null;
  function requestReviewRefresh() {
    if (realReviewQueue.length > 0) {
      realReviewRefreshPending = true;
      return;
    }
    loadRealReview();
  }
  function flushReviewRefreshIfIdle() {
    if (realReviewRefreshPending && realReviewQueue.length === 0) {
      realReviewRefreshPending = false;
      loadRealReview();
    }
  }
  let realUndoStatusTimer = null;
  const realReviewAgainCounts = new Map();

  function celebrateBalloons() {
    if (
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return;
    }
    if (document.getElementById("celebrate-canvas")) return;
    const canvas = document.createElement("canvas");
    canvas.id = "celebrate-canvas";
    canvas.style.cssText =
      "position:fixed;inset:0;width:100%;height:100%;z-index:9999;pointer-events:none;";
    document.body.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };
    resize();
    window.addEventListener("resize", resize);
    const colors = ["#4f63d8", "#6d7fe8", "#ffd166", "#ef476f", "#06d6a0", "#ff8c42", "#9b5de5", "#f4a261"];
    const total = 26;
    const spawnWindow = 2000;
    const lifetime = 2200;
    const balloons = [];
    for (let i = 0; i < total; i++) {
      balloons.push({
        x: 30 + Math.random() * (canvas.width - 60),
        y: canvas.height + 24,
        r: 24 + Math.random() * 18,
        vy: 1.6 + Math.random() * 1.8,
        phase: Math.random() * Math.PI * 2,
        sway: 0.6 + Math.random() * 0.9,
        swing: 8 + Math.random() * 10,
        color: colors[Math.floor(Math.random() * colors.length)],
        born: performance.now(),
      });
    }
    let frameId = null;
    function frame() {
      const now = performance.now();
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (let i = balloons.length - 1; i >= 0; i--) {
        const balloon = balloons[i];
        const age = now - balloon.born;
        const delay = (i / total) * spawnWindow;
        if (age < delay) continue;
        const t = age - delay;
        if (t > lifetime + 400) {
          balloons.splice(i, 1);
          continue;
        }
        const alpha = t < 300 ? t / 300 : t > lifetime ? 1 - (t - lifetime) / 400 : 1;
        if (alpha <= 0) {
          balloons.splice(i, 1);
          continue;
        }
        const y = balloon.y - balloon.vy * t;
        const x = balloon.x + Math.sin((t / 1000) * balloon.sway * Math.PI * 2 + balloon.phase) * balloon.swing;
        const r = balloon.r;
        ctx.globalAlpha = alpha;
        ctx.fillStyle = balloon.color;
        ctx.beginPath();
        ctx.ellipse(x, y, r, r * 1.25, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.moveTo(x - 4, y + r * 1.1);
        ctx.lineTo(x + 4, y + r * 1.1);
        ctx.lineTo(x, y + r * 1.1 + 6);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = balloon.color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, y + r * 1.1 + 6);
        ctx.quadraticCurveTo(
          x + Math.sin(t / 400) * 3,
          y + r * 1.1 + 13,
          x,
          y + r * 1.1 + 22
        );
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      if (balloons.length) {
        frameId = requestAnimationFrame(frame);
      } else {
        canvas.remove();
        window.removeEventListener("resize", resize);
      }
    }
    frameId = requestAnimationFrame(frame);
  }

  function speakingRows(back) {
    return String(back || "")
      .split(/\s*\|\|\s*/)
      .map((raw) => raw.trim())
      .filter(Boolean)
      .map((expression) => {
        const numbered = expression.replace(/^\s*\d+[.)、]\s*/, "");
        const dashIndex = numbered.indexOf("——");
        if (dashIndex < 0) return { en: numbered, note: "" };
        return {
          en: numbered.slice(0, dashIndex).trim(),
          note: numbered.slice(dashIndex + 2).trim(),
        };
      });
  }

  let realAudio = null;
  let realAudioButton = null;
  let realAudioPending = null;
  let realAudioToken = { n: 0 };
  const realAudioPrefetched = new Set();
  let guestUtterance = null;

  /* iOS/安卓首次触摸时解锁音频输出：之后异步 play() 不再被浏览器拦截，
     否则生成音频往往要几秒，用户手势失效后 play() 会被静默拒绝。 */
  let mobileAudioUnlocked = false;
  function unlockMobileAudio() {
    if (mobileAudioUnlocked) return;
    mobileAudioUnlocked = true;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      const ctx = new AC();
      const buffer = ctx.createBuffer(1, 1, 22050);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.start(0);
      setTimeout(() => { try { ctx.close(); } catch (_) { /* 忽略 */ } }, 250);
    } catch (_) { /* 不支持时忽略 */ }
  }
  document.addEventListener("pointerdown", unlockMobileAudio, { passive: true });
  document.addEventListener("touchstart", unlockMobileAudio, { passive: true });

  function audioBaseLabel(button) {
    if (!button) return "";
    if (button.dataset.audioLabel === undefined) {
      button.dataset.audioLabel = button.innerHTML;
    }
    return button.dataset.audioLabel;
  }

  function setAudioButton(button, state) {
    if (!button) return;
    const base = audioBaseLabel(button);
    if (state === "playing") {
      button.innerHTML = base.replace("▶", "⏸");
    } else if (state === "generating") {
      button.innerHTML = "生成中…";
    } else {
      button.innerHTML = base;
    }
  }

  function resetRealAudioButton(button) {
    if (button) {
      button.disabled = false;
      setAudioButton(button, "idle");
    }
  }

  function stopRealAudio() {
    realAudioToken.n += 1;
    if (realAudioPending) {
      resetRealAudioButton(realAudioPending.button);
      realAudioPending = null;
    }
    if (realAudio) {
      realAudio.pause();
      if (realAudioButton) {
        resetRealAudioButton(realAudioButton);
        realAudioButton = null;
      }
      realAudio = null;
    }
  }

  async function playRealAudio(text, button) {
    if (!text) return;
    // 同一个文本已在播放/暂停：点击切换播放与暂停，随时可停。
    if (realAudio && !realAudioPending && realAudio.dataset.text === text) {
      if (realAudio.paused) {
        realAudio.play().catch(() => {});
        if (button) setAudioButton(button, "playing");
      } else {
        realAudio.pause();
        if (button) setAudioButton(button, "idle");
      }
      return;
    }
    stopRealAudio();
    const token = ++realAudioToken.n;
    realAudioPending = { text, token, button };
    // 在用户点击手势内同步创建 Audio 元素，尽量保住移动端播放许可。
    const audio = new Audio();
    audio.preload = "auto";
    audio.setAttribute("playsinline", "");
    audio.setAttribute("webkit-playsinline", "");
    audio.dataset.text = text;
    if (button) {
      button.disabled = true;
      setAudioButton(button, "generating");
    }
    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json().catch(() => ({}));
      if (!realAudioPending || realAudioPending.token !== token) return;
      realAudioPending = null;
      resetRealAudioButton(button);
      if (!res.ok || !data.url) return;
      audio.src = data.url;
      // 挂到 DOM 并 load()：iOS 上更可靠的播放许可与解码路径。
      audio.load();
      if (!audio.parentNode) document.body.appendChild(audio);
      audio.onended = () => {
        if (realAudio === audio) {
          realAudio = null;
          if (realAudioButton) {
            resetRealAudioButton(realAudioButton);
            realAudioButton = null;
          }
        }
        try {
          audio.remove();
        } catch (_) { /* 忽略 */ }
      };
      realAudio = audio;
      realAudioButton = button || null;
      audio.play()
        .then(() => { if (button) setAudioButton(button, "playing"); })
        .catch(() => {
          if (realAudio === audio) realAudio = null;
          if (realAudioButton) {
            resetRealAudioButton(realAudioButton);
            realAudioButton = null;
          }
        });
    } catch (_) {
      if (realAudioPending && realAudioPending.token === token) {
        realAudioPending = null;
        resetRealAudioButton(button);
      }
    }
  }

  function prefetchRealAudio() {
    const texts = [];
    // 现在一次只显示一张卡：只预取当前这张卡的音频，
    // 避免整个队列一次性触发大量 TTS 生成。
    const card = realReviewQueue[0];
    if (!card) return;
    if (card.card_type === "speaking") {
      speakingRows(card.back).forEach((row) => {
        if (row.en && !realAudioPrefetched.has(row.en)) {
          realAudioPrefetched.add(row.en);
          texts.push(row.en);
        }
      });
    } else {
      const target = plainTargetWord(card.word);
      if (target && !realAudioPrefetched.has(target)) {
        realAudioPrefetched.add(target);
        texts.push(target);
      }
      const sentence = card.context || card.front;
      if (sentence && sentence !== card.word && !realAudioPrefetched.has(sentence)) {
        realAudioPrefetched.add(sentence);
        texts.push(sentence);
      }
    }
    if (texts.length) {
      fetch("/api/tts/prefetch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texts }),
      }).catch(() => {});
    }
  }

  /* 预下载当前卡片的音频到浏览器缓存：点击播放时无需等待生成/下载。
     与 realAudioPrefetched（只提交后台生成）分开：这里真的把 mp3
     拉到浏览器，配合 /tts-audio/ 的 immutable 缓存头实现秒播。 */
  const realAudioWarmed = new Set();

  /* 预下载音频到浏览器缓存：点击播放时无需等待生成/下载。
     已生成过的文本直接跳过，配合 /tts-audio/ 的 immutable 缓存头实现秒播。 */
  async function warmUpAudioTexts(texts) {
    for (const text of texts) {
      if (!text || realAudioWarmed.has(text)) continue;
      realAudioWarmed.add(text);
      try {
        const res = await fetch("/api/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.url) continue;
        const warm = new Audio();
        warm.preload = "auto";
        warm.src = data.url;
        warm.load();
      } catch (_) { /* 预加载失败不影响点击播放 */ }
    }
  }

  async function warmUpRealAudio() {
    const card = realReviewQueue[0];
    if (!card) return;
    const texts = [];
    if (card.card_type === "speaking") {
      speakingRows(card.back).forEach((row) => {
        if (row.en) texts.push(row.en);
      });
    } else {
      const target = plainTargetWord(card.word || card.front);
      if (target) texts.push(target);
      const sentence = card.context || card.front;
      if (sentence && sentence !== target) texts.push(sentence);
    }
    await warmUpAudioTexts(texts);
  }

  function realReviewCardHtml(card) {
    const isSpeaking = card.card_type === "speaking";
    const target = plainTargetWord(card.word);
    const previews = card.rating_previews || {};
    const previewLabel = (rating, fallback) => {
      const label = previews[rating] && previews[rating].label;
      return label || fallback;
    };
    const sentence = card.context || card.front;
    // Cloze/口语卡正面不放音频：Cloze 挖空（例句会读答案）、口语（回答会剧透答案）。
    const hasFrontAudio = card.card_type !== "cloze" && !isSpeaking;
    let frontButtons = "";
    if (hasFrontAudio) {
      frontButtons =
        '<button class="demo-audio" data-real-audio="' + escapeHtml(target) +       '" type="button">▶</button>';
      if (sentence && sentence !== target) {
        frontButtons +=
          '<button class="demo-audio" data-real-audio="' + escapeHtml(sentence) +       '" type="button">▶</button>';
      }
    }
    let frontInner;
    if (isSpeaking) {
      frontInner = escapeHtml(card.front);
    } else {
      frontInner = renderMarkdown(card.front, target);
      if (!frontInner.includes("target-word")) {
        if (card.card_type === "general") {
          frontInner = '<mark class="word-highlight">' + frontInner + "</mark>";
        } else if (card.card_type === "cloze") {
          frontInner = frontInner.replace(/(_{2,})/g, '<mark class="word-highlight">$1</mark>');
        }
      }
    }
    let frontHtml;
    if (card.card_type === "reading") {
      // 阅读卡正面：目标词一行（带播放按钮），下方直接显示完整例句，
      // 不再需要点击“例句”按钮展开（例句就是学习内容，直接可见）。
      frontHtml =
        '<div class="reading-word-row">' +
        '<mark class="word-highlight">' + escapeHtml(target) + "</mark>" +
        ' <button class="demo-audio" data-real-audio="' + escapeHtml(target) +
        '" type="button">▶</button></div>' +
        (sentence && sentence !== target
          ? '<div class="reading-sentence-wrap">' +
            '<div class="reading-sentence-content">' +
            '<p class="demo-front-text">' + frontInner + "</p>" +
            '<div class="demo-audio-row"><button class="demo-audio" data-real-audio="' +
            escapeHtml(sentence) + '" type="button">▶</button></div>' +
            "</div></div>"
          : "");
    } else {
      frontHtml =
        '<p class="demo-front-text">' + frontInner + "</p>" +
        (!isSpeaking && frontButtons ? '<div class="demo-audio-row">' + frontButtons + "</div>" : "");
    }
    let backInner;
    if (isSpeaking) {
      backInner = '<div class="card-answer">' + speakingRows(card.back).map((row) =>
        '<div class="speaking-expression">' +
        '<button class="small" data-real-audio="' + escapeHtml(row.en) + '" type="button">▶</button>' +
        '<div class="speaking-expression-text"><span class="speaking-en">' + escapeHtml(row.en) + "</span>" +
        (row.note ? '<span class="speaking-note">' + escapeHtml(row.note) + "</span>" : "") +
        "</div></div>"
      ).join("") + "</div>";
    } else {
      backInner = '<div class="card-answer">' + renderMarkdown(card.back, target) + "</div>";
      if (card.card_type === "cloze") {
        // Cloze 卡音频放背面：翻面后答案已可见，听单词/例句不剧透。
        backInner +=
          '<div class="demo-audio-row">' +
          '<button class="demo-audio" data-real-audio="' + escapeHtml(target) +       '" type="button">▶</button>';
        if (sentence && sentence !== target) {
          backInner +=
            '<button class="demo-audio" data-real-audio="' + escapeHtml(sentence) +       '" type="button">▶</button>';
        }
        backInner += "</div>";
      }
    }
    const showBury = (realReviewAgainCounts.get(card.id) || 0) >= 3;
    const backHtml = backInner +
      (showBury
        ? '<div class="real-card-actions">' +
          '<button class="small" data-real-bury="' + card.id + '" type="button">不想学</button>' +
          "</div>"
        : "") +
      '<div class="demo-rating">' +
      '<button class="rating again" data-real-rating="again" data-real-card="' + card.id + '" type="button"><b>Again</b><small>1m</small></button>' +
      '<button class="rating hard" data-real-rating="hard" data-real-card="' + card.id + '" type="button"><b>Hard</b><small>1m</small></button>' +
      '<button class="rating good" data-real-rating="good" data-real-card="' + card.id + '" type="button"><b>Good</b><small>' + escapeHtml(previewLabel("good", "2d")) + "</small></button>" +
      '<button class="rating easy" data-real-rating="easy" data-real-card="' + card.id + '" type="button"><b>Easy</b><small>' + escapeHtml(previewLabel("easy", "3d")) + "</small></button>" +
      "</div>";
    return (
      '<div class="demo-card home-review-card' +
      (card.card_type === "general" ? " general-card" : "") +
      '" data-real-card="' + card.id +
      '" data-review-signature="' + realCardSignature(card) + '">' +
      '<div class="demo-card-inner">' +
      '<div class="demo-card-face front">' + frontHtml + "</div>" +
      '<div class="demo-card-face back">' + backHtml + "</div>" +
      "</div>" +
      '<button class="demo-flip" data-real-flip type="button">翻转</button>' +
      "</div>"
    );
  }

  function emptyReviewCardHtml() {
    return (
      '<div class="demo-card review-placeholder" aria-hidden="true">' +
      '<div class="demo-card-inner">' +
      '<div class="demo-card-face front"></div>' +
      "</div></div>"
    );
  }

  // 渲染完整性签名：头部卡片内容（含学习状态）没变时不需要重建 DOM。
  // 重建会闪动，还会丢掉翻面状态与正在播放的音频按钮状态。
  function realCardSignature(card) {
    return (
      String(card.id) + "|" + String(card.queue_kind || "") + "|" +
      (card.is_learning ? "L" : "R") + "|" +
      (card.session_repeat ? "S" : "") +
      (Number(card.session_correct_streak) || 0)
    );
  }

  function renderRealReview() {
    const box = document.getElementById("real-review-cards");
    if (!box) return;
    recalcRealReviewRemaining();
    const maxCards = 1;
    const cards = realReviewQueue.slice(0, maxCards);
    const placeholders = 0;
    // 队列清空且有卡片时，服务端允许继续学新卡（等价 can_extra_new）。
    if (realReviewQueue.length === 0 && realReviewTotalCards > 0) {
      realReviewCanExtraNew = true;
    }
    // 头部卡片未变化（比如评分响应没改变队列头部）时跳过 innerHTML 重建，
    // 避免卡片闪动和翻面/音频状态丢失。
    const head = cards[0];
    const headEl = box.querySelector(".home-review-card");
    const rebuild =
      !headEl ||
      cards.length !== box.childElementCount ||
      !head ||
      headEl.dataset.reviewSignature !== realCardSignature(head);
    if (rebuild) {
      box.innerHTML =
        cards.map(realReviewCardHtml).join("") +
        Array.from({ length: placeholders }, emptyReviewCardHtml).join("");
      // 新渲染的卡片一律回到正面，避免任何残留的翻面状态。
      box.querySelectorAll(".home-review-card.flipped").forEach((el) => {
        el.classList.remove("flipped");
      });
    }
    // 没有学习任务时不显示卡片与卡片管理行。
    box.hidden = cards.length === 0;
    syncReviewManageRow();
    if (
      realReviewInFlight === 0 &&
      cards.length === 0 &&
      realReviewQueue.length === 0 &&
      realReviewRemainingTotal === 0 &&
      realReviewHadCards &&
      !realReviewCelebrated
    ) {
      realReviewCelebrated = true;
      celebrateBalloons();
    }
    const empty = document.getElementById("real-review-empty");
    const allDone =
      realReviewInFlight === 0 &&
      realReviewQueue.length === 0 &&
      realReviewRemainingTotal === 0 &&
      realReviewCanExtraNew &&
      // 新用户没有任何卡片时不显示"恭喜完成"框（还没学过任何卡）。
      realReviewTotalCards > 0;
    if (empty) empty.hidden = !allDone;
    const emptyNone = document.getElementById("real-review-empty-none");
    if (emptyNone) {
      emptyNone.hidden = !(
        realReviewLoaded &&
        realReviewQueue.length === 0 &&
        realReviewTotalCards === 0
      );
    }
    updateRemainingBadge();
    updateTodayOverview();
    // 评分写库期间不要同时触发 TTS 限流计数写库；SQLite 只有一个写者。
    if (realReviewInFlight === 0) {
      prefetchRealAudio();
      warmUpRealAudio();
    }
    updateRealUndoButton();
  }

  function updateRealUndoButton() {
    const button = document.getElementById("real-review-undo");
    if (button) {
      // 本地历史必须还在 15 分钟窗口内才可撤回；服务端 can_undo 仍可作为兜底。
      const recentLocalUndo =
        realReviewHistory.length > 0 &&
        Date.now() - realReviewLastRatingAt < 14 * 60 * 1000;
      button.disabled = !(recentLocalUndo || realReviewCanUndo);
    }
  }

  function recalcRealReviewRemaining() {
    const byKind = { new: 0, due: 0, again: 0 };
    realReviewQueue.forEach((item) => {
      const kind = item.queue_kind === "new"
        ? "new"
        : item.queue_kind === "again" ? "again" : "due";
      byKind[kind] += 1;
    });
    realReviewRemaining = byKind;
    realReviewRemainingTotal = realReviewQueue.length;
  }

  function updateRemainingBadge() {
    const totalEl = document.getElementById("real-review-remaining-total");
    const newEl = document.getElementById("real-review-remaining-new");
    const learningEl = document.getElementById("real-review-remaining-learning");
    const dueEl = document.getElementById("real-review-remaining-due");
    if (!totalEl) return;
    const rem = realReviewRemaining || {};
    const fresh = Number(rem.new || 0);
    const learning = Number(rem.again || 0);
    const due = Number(rem.due || 0);
    totalEl.textContent = String(fresh + learning + due);
    newEl.textContent = String(fresh);
    learningEl.textContent = String(learning);
    dueEl.textContent = String(due);
  }

  function syncReviewManageRow() {
    const manageRow = document.querySelector(".real-review-manage-row");
    // 有可撤回的评分时保留管理行（撤回入口不能因为队列清空而消失）；
    // 用户已有卡片时也必须保留：桌面端管理区标签已隐藏，
    // "卡片管理 ▾"菜单是唯一的卡片管理入口，学完今天任务后不能消失。
    if (manageRow) {
      manageRow.hidden =
        realReviewQueue.length === 0 &&
        realReviewHistory.length === 0 &&
        !realReviewCanUndo &&
        !(realReviewTotalCards > 0);
    }
  }

  // 撤回后与服务端对齐时，被撤回的卡保持队首展示：它是用户上一张评分的卡，
  // 不能因为服务端按到期时间/卡片 id 重新排序而被顶走。
  function moveCardToHead(queue, id) {
    const index = queue.findIndex((q) => q.id === id);
    if (index > 0) {
      const [item] = queue.splice(index, 1);
      queue.unshift(item);
    }
    return queue;
  }

  async function loadRealReview(preserveOnError = false, preferredHeadId = null) {
    const loadVersion = realReviewQueueVersion;
    try {
      // 队列加载必须有超时：评分链上可能 await 本函数（如卡片不在本地队列、
      // 409 后重拉对齐），若 /api/cards 挂起会让整条评分链无限等待，
      // 后续所有评分点击都排队、看起来“点了没反应”。
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      let res;
      try {
        res = await fetch("/api/cards", {
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timer);
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      // 如果等待期间用户已经评分/撤回/加学，本地队列已更新，
      // 这次服务端快照可能已过期，直接丢弃，避免旧快照顶回旧卡/旧 revision。
      if (loadVersion !== realReviewQueueVersion) return;
      // 队列顺序以服务端为准：服务端按“到期复习 → 今日新学 → 学习中的卡”
      // 固定排序，手机端与电脑端看到的是同一份队列。
      // 不在本地按历史顺序重排，避免各设备本地缓存导致队列不一致。
      realReviewQueue = Array.isArray(data.queue) ? data.queue : [];
      if (preferredHeadId != null) {
        moveCardToHead(realReviewQueue, preferredHeadId);
      }
      realReviewHadCards = realReviewQueue.length > 0;
      realReviewTotalCards = Number(data.total_cards) || 0;
      realReviewLoaded = true;
      realReviewCelebrated = false;
      const remaining = data.remaining_counts || {};
      realReviewRemaining = remaining;
      realReviewCanUndo = Boolean(data.can_undo);
      realReviewRemainingTotal =
        Number(remaining.due || 0) +
        Number(remaining.new || 0) +
        Number(remaining.again || 0);
      realReviewCanExtraNew = Boolean(data.can_extra_new);
      const reviewDailyInput = document.getElementById("real-review-daily-new-limit");
      if (reviewDailyInput && data.new_cards_per_day !== undefined) {
        reviewDailyInput.value = data.new_cards_per_day;
      }
      const stats = data.today_stats || {};
      realTodayStats = stats;
      updateTodayOverview();
      setReviewStatus("");
    } catch (err) {
      if (preserveOnError) return;
      realReviewQueue = [];
      realReviewLoaded = false;
      setReviewStatus("加载失败：" + err.message);
    }
    renderRealReview();
  }

  /* ---------- 默认皮肤：今日学习概览 ---------- */
  function updateTodayOverview() {
    const wrap = document.getElementById("today-overview");
    if (!wrap || wrap.hidden) return;
    const dueEl = document.getElementById("today-overview-due");
    const newEl = document.getElementById("today-overview-new");
    const studiedEl = document.getElementById("today-overview-studied");
    const ringFill = document.getElementById("today-overview-ring-fill");
    const ringText = document.getElementById("today-overview-ring-text");
    const titleEl = document.getElementById("today-overview-title");
    const subEl = document.getElementById("today-overview-sub");
    const extraEl = document.getElementById("today-overview-extra");
    const daysEl = document.getElementById("today-overview-days");
    if (!dueEl || !newEl || !studiedEl || !ringFill || !ringText || !titleEl || !subEl) return;

    const stats = realTodayStats || {};
    const studied = Number(stats.unique_cards) || 0;
    const rem = realReviewRemaining || {};
    const remDue = Number(rem.due || 0) + Number(rem.again || 0);
    const remNew = Number(rem.new || 0);
    const remaining = Math.max(0, realReviewRemainingTotal || 0);
    const canExtra =
      realReviewInFlight === 0 &&
      realReviewCanExtraNew &&
      realReviewTotalCards > 0 &&
      remaining <= 0;
    const totalPlan = studied + remDue + remNew;
    const pct = totalPlan > 0 ? Math.min(100, Math.round((studied / totalPlan) * 100)) : 0;

    ringFill.style.strokeDashoffset = String(276.5 * (1 - pct / 100));
    ringText.textContent = pct + "%";
    // “复习 / 新学”显示今天实际完成的统计，而不是剩余队列数。
    dueEl.textContent = String(Number(stats.reviews) || 0);
    newEl.textContent = String(Number(stats.new_learned) || 0);
    studiedEl.textContent = String(studied);
    if (extraEl) extraEl.hidden = !canExtra;
    if (daysEl) {
      const days = todayDashboardData ? Number(todayDashboardData.consecutive_study_days) || 0 : null;
      daysEl.textContent = days === null ? "–" : days + "天";
    }

    let title = "今天也要见面 5 分钟";
    let sub = "先解决到期卡片，再学一点新词";
    if (realReviewTotalCards === 0) {
      title = "从第一张卡片开始";
      sub = "查一个想学的词，AI 会帮你做成学习卡片";
    } else if (canExtra) {
      title = "今天的学习完成啦 🎉";
      sub = "可以再做一点新卡，或者读一篇 AI 短文";
    } else if (remaining > 0) {
      title = "今天还有 " + remaining + " 张卡片";
      sub = "先把到期卡片过一遍，再学新词";
    }
    titleEl.textContent = title;
    subEl.textContent = sub;
  }

  async function loadTodayOverview() {
    const wrap = document.getElementById("today-overview");
    if (!wrap || wrap.hidden) return;
    try {
      const res = await fetch("/api/dashboard", {
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) return;
      todayDashboardData = await res.json();
      updateTodayOverview();
    } catch (_) { /* 概览失败不打扰学习 */ }
  }

  function wireTodayOverviewButtons() {
    const start = document.getElementById("today-overview-start");
    if (start) {
      start.addEventListener("click", () => {
        if (isMobileLayout()) applyMobileView("study", false);
        const target = document.getElementById("real-review") ||
          document.getElementById("guest-demo-cards");
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    const add = document.getElementById("today-overview-add");
    if (add) {
      add.addEventListener("click", () => {
        if (realShowManagePanel) realShowManagePanel("real-add-card");
      });
    }
    const article = document.getElementById("today-overview-article");
    if (article) {
      article.addEventListener("click", () => {
        if (isMobileLayout()) applyMobileView("article", false);
        const target = document.getElementById("real-article-panel") ||
          document.getElementById("guest-demo-article");
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    const extra = document.getElementById("today-overview-extra");
    if (extra) {
      extra.addEventListener("click", () => {
        if (isMobileLayout()) applyMobileView("study", false);
        if (realExtraPanel) realExtraPanel.hidden = false;
        const target = document.getElementById("real-review");
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
  }

  let reviewActionChain = Promise.resolve();
  const reviewRetryDelays = [250, 750];
  // 评分链看门狗：当确有评分请求在途且已远超预期时长仍未完成时，
  // 说明链被挂起的请求卡死（如网络黑洞、服务器无响应），此时不再
  // 把新点击追加到队列尾部等待——直接丢弃旧链并重拉队列对齐，
  // 保证评分按钮点击永远立即生效，而不是“点了没反应”。
  const REVIEW_CHAIN_STALL_MS = 12000;
  // 在途评分请求的最早开始时间；仅当 realReviewInFlight > 0 时有效。
  // 基于在途任务而非“上次入队时间”判断，避免正常慢速评分被误判卡死。
  let reviewChainBusySince = 0;

  function reviewErrorMessage(data, fallback) {
    if (data && typeof data.detail === "string") return data.detail;
    if (data && data.detail && typeof data.detail.message === "string") {
      return data.detail.message;
    }
    return fallback;
  }

  function waitForReviewRetry(delay) {
    return new Promise((resolve) => window.setTimeout(resolve, delay));
  }

  async function submitReviewWithRetry(payload) {
    let lastError = null;
    for (let attempt = 0; attempt <= reviewRetryDelays.length; attempt += 1) {
      if (attempt > 0) {
        const status = document.getElementById("real-undo-status");
        if (status) status.textContent = "正在保存（自动重试 " + attempt + "/2）…";
        await waitForReviewRetry(reviewRetryDelays[attempt - 1]);
      }
      // 评分请求必须带超时：网络/服务器长时间无响应时主动失败，
      // 否则 reviewActionChain 串行链会被挂起请求永久卡住，
      // 之后所有评分点击都会排队、看起来“点了没反应”。
      // 单次 8s：正常评分请求毫秒级返回，8s 足以覆盖慢网络；
      // 超过即失败，配合幂等 action_id 自动重试，不无限等待。
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 8000);
      try {
        const res = await fetch("/api/cards/reviews/batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          const status = document.getElementById("real-undo-status");
          if (status && status.textContent.startsWith("正在保存")) status.textContent = "";
          return data;
        }
        const error = new Error(reviewErrorMessage(data, "保存失败"));
        error.status = res.status;
        error.code = data.code || "";
        lastError = error;
        if (![502, 503, 504].includes(res.status)) throw error;
      } catch (error) {
        lastError = error;
        // 超时（AbortError）没有 status，与 502/503/504 一样走自动重试；
        // 服务端有 action_id 幂等，重试不会把同一次评分应用两遍。
        if (error.status && ![502, 503, 504].includes(error.status)) throw error;
      } finally {
        clearTimeout(timer);
      }
      if (attempt === reviewRetryDelays.length) throw lastError;
    }
    throw lastError || new Error("保存失败");
  }

  function rateRealReviewCard(button) {
    const id = Number(button.dataset.realCard);
    const rating = button.dataset.realRating;
    if (!id || !rating) return;
    // 暂留的最后一张卡等待确认时，相同评分视为重复点击直接忽略；
    // 换成其他评分（如困难后改点认识）仍排队执行，避免点不动。
    if (heldReviewAction && heldReviewAction.id === id && heldReviewAction.rating === rating) {
      return;
    }
    // 评分链看门狗：当有评分请求在途且已远超预期时长（网络黑洞、
    // 服务器长时间无响应），链已被挂起请求卡死，之后所有点击都会
    // 排队“点了没反应”。此时不再把新点击追加到队列尾部，而是
    // 丢弃旧链直接执行本次评分；旧请求会因服务端 revision 冲突
    // 返回 409，由评分成功路径重拉队列对齐，不会重复计分。
    const chainStallMs =
      realReviewInFlight > 0 && reviewChainBusySince > 0
        ? Date.now() - reviewChainBusySince
        : 0;
    if (chainStallMs > REVIEW_CHAIN_STALL_MS) {
      const status = document.getElementById("real-undo-status");
      if (status) {
        status.textContent = "上次评分未响应，正在同步队列…";
        clearTimeout(realUndoStatusTimer);
        realUndoStatusTimer = setTimeout(() => {
          status.textContent = "";
        }, 6000);
      }
      reviewActionChain = Promise.resolve();
      loadRealReview(true);
    }
    // 连续点击时串行处理：先完成上一次评分，再处理下一次，
    // 避免多个响应乱序把已评过的卡重新顶回队首。
    reviewActionChain = reviewActionChain
      .catch(() => undefined)
      .then(() => rateRealReviewCardNow(id, rating));
  }

  function shouldRepeatReviewToday(rating, card) {
    // “良好/简单”都表示这次已经认识，绝不能因服务端的学习状态或
    // 到期时间边界让同一张卡立刻重新出现。
    // 是否今天回队由服务端 repeat_now 决定，前端不再用 due_at 自行推断。
    if (!card || !["again", "hard"].includes(rating)) return false;
    return Boolean(card.repeat_now);
  }

  async function rateRealReviewCardNow(id, rating) {
    const index = realReviewQueue.findIndex((card) => card.id === id);
    if (index < 0) {
      // 按钮对应的卡已不在本地队列（可能已被其他操作/标签页处理过）：
      // 不能静默忽略，否则点击看起来“没反应”；以服务端为准重拉队列对齐。
      // 失败时保留现有队列，不把正常的学习界面清空。
      await loadRealReview(true);
      return;
    }
    const card = realReviewQueue[index];
    // “重来”在任何状态下都会立即回队；学习/新卡点“困难”也会回队
    // （FSRS 学习步骤为 0 秒，与 docs/调度算法说明.md 一致）。
    // 如果这是最后一张，不能乐观移除后又被服务器“顶回来”造成闪动，
    // 要等服务器确认后再决定去留。
    const willRelearn =
      rating === "again" ||
      (rating === "hard" && (card.is_learning || card.state === "new"));
    const holdCard = willRelearn && realReviewQueue.length === 1;
    if (holdCard) {
      heldReviewAction = { id, rating };
      // 最后一张卡点重来/困难：立即把卡片平滑翻回正面给出反馈，
      // 不等服务器响应（避免等待期间卡片无反应、确认后又瞬翻卡顿）。
      // 卡片留在队列里，服务器确认后只更新队列数据，不会白屏闪动。
      const heldEl = document.querySelector("#real-review-cards .home-review-card");
      if (heldEl) heldEl.classList.remove("flipped");
    }
    // 记录在途请求开始时间（首个在途请求）；看门狗据此判断链是否卡死。
    // 任务完成时 realReviewInFlight 归零，下次新任务重新计时。
    if (realReviewInFlight === 0) reviewChainBusySince = Date.now();
    realReviewInFlight += 1;
    realReviewQueueVersion += 1;
    try {
      if (rating === "again") {
        realReviewAgainCounts.set(card.id, (realReviewAgainCounts.get(card.id) || 0) + 1);
      }
      // 先乐观更新本地队列，点击立即换卡，不等待网络。
      if (!holdCard) {
        if (willRelearn) {
          // 重来/困难卡今天还要学：直接移到队尾，剩余数保持不变，
          // 不会出现“N -> N-1 -> N”的跳动。
          realReviewQueue.splice(index, 1);
          realReviewQueue.push({
            ...card,
            queue_kind: "again",
            session_repeat: true,
            session_correct_streak: 0,
            _againToday: false,
          });
        } else {
          realReviewQueue.splice(index, 1);
        }
        renderRealReview();
      }
      // 同一次点击的自动重试必须复用 action_id 与 revision，确保服务端幂等。
      const payload = {
        ratings: [{
          card_id: id,
          rating,
          session_repeat: Boolean(card.session_repeat),
          action_id: newActionId(),
          expected_revision: Number(card.revision) || 0,
        }],
      };
      const data = await submitReviewWithRetry(payload);
      if (data.errors && data.errors.length) {
        const error = new Error((data.errors[0] && data.errors[0].detail) || "保存失败");
        error.status = Number(data.errors[0] && data.errors[0].status) || 0;
        throw error;
      }
      // 服务器必须返回被评卡片，否则不能决定它是毕业还是回队；
      // 此时按失败处理，重拉队列与服务器对齐。
      if (!data.cards || !data.cards[0] || !data.cards[0].card) {
        throw new Error("服务器没有返回评分结果");
      }
      // 保存成功后才记入撤回历史，避免失败时本地记录与服务器不一致。
      realReviewHistory.push({ card, index });
      if (realReviewHistory.length > 50) realReviewHistory.shift();
      realReviewLastRatingAt = Date.now();
      updateRealUndoButton();
      syncReviewManageRow();
      if (data.today_stats) {
        realTodayStats = data.today_stats;
      } else if (!card._studiedToday) {
        const cur = Object.assign({}, realTodayStats || {});
        cur.unique_cards = (Number(cur.unique_cards) || 0) + 1;
        realTodayStats = cur;
      }
      const fresh = data.cards && data.cards[0] && data.cards[0].card;
      // 是否今天回队由服务端 repeat_now 决定。
      if (shouldRepeatReviewToday(rating, fresh)) {
        // FSRS 仍把它留在学习队列：无论本地是否已有副本，
        // 都移除后追加到队尾，保证刚评过的卡不会弹回队首。
        const repeatItem = {
          ...fresh,
          queue_kind: "again",
          session_repeat: true,
          session_correct_streak: 0,
          _studiedToday: true,
          _againToday: false,
        };
        const freshDupIndex = realReviewQueue.findIndex((item) => item.id === id);
        if (freshDupIndex >= 0) realReviewQueue.splice(freshDupIndex, 1);
        realReviewQueue.push(repeatItem);
      } else if (willRelearn) {
        // 服务端认为它今天不再回队（例如毕业或下次复习 >= 1 天）：
        // 移除本地暂留/乐观副本。
        const removeIndex = realReviewQueue.findIndex((item) => item.id === id);
        if (removeIndex >= 0) realReviewQueue.splice(removeIndex, 1);
      }
      renderRealReview();
      flushReviewRefreshIfIdle();
      loadTodayOverview();
      updateTodayOverview();
    } catch (err) {
      const status = document.getElementById("real-undo-status");
      if (status) {
        status.textContent = "评分失败：" + err.message + "，请重试";
        clearTimeout(realUndoStatusTimer);
        realUndoStatusTimer = setTimeout(() => {
          status.textContent = "";
        }, 6000);
      }
      if (err.status === 409) {
        // 卡片已在其他页面被评分：以服务器为准，重拉队列对齐。
        await loadRealReview();
        return;
      }
      // 其余失败（网络/500 等）：把卡恢复回原位并保留（不重拉队列），
      // 避免"评分成功感却卡片又出现"的错觉；用户可直接重试。
      if (!holdCard && index >= 0) {
        const dupIndex = realReviewQueue.findIndex((item) => item.id === id);
        if (willRelearn) {
          // 移除“重来/困难”的队尾乐观副本，把原卡放回原位置。
          if (dupIndex >= 0) realReviewQueue.splice(dupIndex, 1);
          realReviewQueue.splice(Math.min(index, realReviewQueue.length), 0, card);
        } else if (dupIndex < 0) {
          realReviewQueue.splice(Math.min(index, realReviewQueue.length), 0, card);
        }
        renderRealReview();
      }
      return;
    }
    finally {
      realReviewInFlight -= 1;
      // 在途请求全部结束时清除看门狗计时，下次评分重新开始。
      if (realReviewInFlight <= 0) {
        realReviewInFlight = 0;
        reviewChainBusySince = 0;
      }
      heldReviewAction = null;
      // 最后一张卡毕业时，成功路径渲染发生在 in-flight 归零之前，
      // “今日完成”会被暂时压住；归零后补一次空队列渲染。
      // 队列仍有卡时不重复渲染，避免卡片闪动。
      if (realReviewQueue.length === 0) renderRealReview();
      if (realReviewInFlight === 0 && realReviewQueue.length > 0) {
        prefetchRealAudio();
        warmUpRealAudio();
      }
      flushReviewRefreshIfIdle();
    }
  }

  async function buryRealCard(button) {
    const id = Number(button.dataset.realBury);
    const index = realReviewQueue.findIndex((card) => card.id === id);
    if (index < 0) return;
    try {
      const res = await fetch("/api/cards/" + id + "/bury", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "操作失败");
      realReviewQueueVersion += 1;
      realReviewQueue.splice(index, 1);
      renderRealReview();
    } catch (err) {
      const status = document.getElementById("real-undo-status");
      if (status) status.textContent = "操作失败：" + err.message;
    }
  }

  async function undoRealReview() {
    const recentLocalUndo =
      realReviewHistory.length > 0 &&
      Date.now() - realReviewLastRatingAt < 14 * 60 * 1000;
    if (!recentLocalUndo && !realReviewCanUndo) return;
    const button = document.getElementById("real-review-undo");
    if (button) button.disabled = true;
    const status = document.getElementById("real-undo-status");
    try {
      const res = await fetch("/api/cards/reviews/undo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "撤回失败");
      if (realReviewHistory.length) realReviewHistory.pop();
      realReviewCanUndo = Boolean(data.can_undo);
      const restored = data.card;
      if (restored) {
        // 撤回本身也是本地队列改动：让在途的旧 loadRealReview 失效，
        // 避免它把撤回前/评分后的旧快照再覆盖回来。
        realReviewQueueVersion += 1;
        const queueKind = restored.due_at == null
          ? "new"
          : restored.is_learning ? "again" : "due";
        const item = {
          ...restored,
          queue_kind: queueKind,
          session_repeat: Boolean(restored.is_learning),
          session_correct_streak: 0,
        };
        // 撤回的卡放回队首立即展示：它正是用户上一张评分的卡，否则按
        // 服务端分区排序插入后，页面上显示的是队列里的下一张卡。
        realReviewQueue = realReviewQueue.filter((q) => q.id !== restored.id);
        realReviewQueue.unshift(item);
        realReviewRemainingTotal = (realReviewRemainingTotal || 0) + 1;
        renderRealReview();
        // 后台与服务端对齐：若恢复的卡已不在今天队列（如到期时间在未来），
        // 以服务端为准更新，保证刷新前后看到同一份队列；同步后撤回的卡
        // 仍保持队首展示（preferredHeadId），不被服务端排序顶走。
        loadRealReview(true, restored.id);
      } else {
        await loadRealReview(true);
      }
      loadTodayOverview();
      if (status) status.textContent = "撤回成功";
      clearTimeout(realUndoStatusTimer);
      realUndoStatusTimer = setTimeout(() => {
        if (status) status.textContent = "";
      }, 2500);
    } catch (err) {
      if (status) status.textContent = "撤回失败：" + err.message;
    } finally {
      updateRealUndoButton();
    }
  }

  const realUndo = document.getElementById("real-review-undo");
  if (realUndo) realUndo.onclick = undoRealReview;

  function setAnkiStatus(message) {
    const status = document.getElementById("real-anki-status");
    if (status) status.textContent = message;
  }

  const realAnkiFile = document.getElementById("real-anki-file");
  const realAnkiImport = document.getElementById("real-anki-import");
  if (realAnkiImport && realAnkiFile) {
    realAnkiImport.onclick = () => realAnkiFile.click();
    realAnkiFile.onchange = async () => {
      const file = realAnkiFile.files && realAnkiFile.files[0];
      if (!file) return;
      if (!file.name.toLowerCase().endsWith(".apkg")) {
        setAnkiStatus("请选择 .apkg 文件");
        realAnkiFile.value = "";
        return;
      }
      realAnkiImport.disabled = true;
      setAnkiStatus("正在校验并合并 Anki 卡片…");
      try {
        const res = await fetch(
          "/api/cards/anki/import?filename=" + encodeURIComponent(file.name),
          { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file }
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "导入失败");
        const summary = [
          "新增 " + Number(data.created || 0) + " 张",
          "合并 " + Number(data.updated || 0) + " 张",
          "保留复习历史 " + Number(data.histories || 0) + " 条",
        ];
        if (data.progress_kept) summary.push("保留较新的本地进度 " + Number(data.progress_kept) + " 张");
        if (data.conflicts) summary.push("跳过冲突 " + Number(data.conflicts) + " 张");
        setAnkiStatus("Anki 导入完成：" + summary.join("，"));
        realReviewQueue = [];
        await loadRealReview();
      } catch (err) {
        setAnkiStatus("Anki 导入失败：" + err.message);
      } finally {
        realAnkiImport.disabled = false;
        realAnkiFile.value = "";
      }
    };
  }

  const realAnkiExport = document.getElementById("real-anki-export");
  if (realAnkiExport) {
    realAnkiExport.onclick = async () => {
      realAnkiExport.disabled = true;
      setAnkiStatus("正在导出卡片与学习进度…");
      try {
        const res = await fetch("/api/cards/anki/export");
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || "导出失败");
        }
        const blob = await res.blob();
        const disposition = res.headers.get("Content-Disposition") || "";
        const match = disposition.match(/filename="([^"]+)"/i);
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = match ? match[1] : "vocabtool.apkg";
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
        setAnkiStatus("导出完成；导入 Anki 时请选择保留学习进度");
      } catch (err) {
        setAnkiStatus("Anki 导出失败：" + err.message);
      } finally {
        realAnkiExport.disabled = false;
      }
    };
  }

  /* ---------- 卡片管理菜单（卡片下方） ---------- */
  function closeRealReviewManage() {
    const menu = document.getElementById("real-review-manage-menu");
    const toggle = document.getElementById("real-review-manage-toggle");
    if (menu) menu.hidden = true;
    if (toggle) toggle.setAttribute("aria-expanded", "false");
  }

  const realReviewManageToggle = document.getElementById("real-review-manage-toggle");
  const realReviewManageMenu = document.getElementById("real-review-manage-menu");
  if (realReviewManageToggle && realReviewManageMenu) {
    realReviewManageToggle.onclick = (e) => {
      e.stopPropagation();
      const show = realReviewManageMenu.hidden;
      realReviewManageMenu.hidden = !show;
      realReviewManageToggle.setAttribute("aria-expanded", show ? "true" : "false");
    };
    document.addEventListener("click", (e) => {
      if (!realReviewManageMenu.hidden && !e.target.closest(".review-manage")) {
        closeRealReviewManage();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !realReviewManageMenu.hidden) {
        closeRealReviewManage();
        if (realReviewManageToggle) realReviewManageToggle.focus();
      }
    });
    realReviewManageMenu.addEventListener("click", (e) => {
      if (e.target.closest("button")) closeRealReviewManage();
    });
  }

  const realReviewDeleteFirst = document.getElementById("real-review-delete-first");
  if (realReviewDeleteFirst) {
    realReviewDeleteFirst.onclick = async () => {
      if (!realReviewQueue.length) return;
      const card = realReviewQueue[0];
      if (!confirm("确定删除这张卡？" + (card.word ? "（" + card.word + "）" : "") + "对应的复习记录也会删除。")) return;
      try {
        const res = await fetch("/api/cards/" + card.id, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "删除失败");
        realReviewQueue = realReviewQueue.filter((item) => item.id !== card.id);
        realReviewHistory = [];
        renderRealReview();
        requestReviewRefresh();
        loadRealBrowser();
      } catch (err) {
        setReviewStatus("删除失败：" + err.message);
      }
    };
  }

  const realManageLibrary = document.getElementById("real-manage-library");
  if (realManageLibrary) {
    realManageLibrary.onclick = () => {
      if (realShowManagePanel) realShowManagePanel("real-library");
      loadRealProfile();
    };
  }
  const realManageAddCard = document.getElementById("real-manage-add-card");
  if (realManageAddCard) {
    realManageAddCard.onclick = () => {
      if (realShowManagePanel) realShowManagePanel("real-add-card");
    };
  }
  const realManageBrowser = document.getElementById("real-manage-browser");
  if (realManageBrowser) {
    realManageBrowser.onclick = () => {
      if (realShowManagePanel) realShowManagePanel("real-card-browser");
    };
  }
  const realReviewRefreshSentences = document.getElementById("real-review-refresh-sentences");
  if (realReviewRefreshSentences) {
    realReviewRefreshSentences.onclick = async () => {
      setReviewStatus("正在更新阅读卡例句…");
      try {
        const res = await fetch("/api/cards/refresh-sentences", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "更新失败");
        const updated = Number(data.updated) || 0;
        setReviewStatus(updated > 0 ? "已更新 " + updated + " 张阅读卡例句" : "暂无可更新的阅读卡");
        if (updated > 0) {
          requestReviewRefresh();
          loadRealBrowser();
        }
      } catch (err) {
        setReviewStatus("更新失败：" + err.message);
      }
    };
  }

  /* ---------- 彩蛋：连点顶部 V 图标 5 次，边牧跑过屏幕 ---------- */
  function triggerLogoEasterEgg() {
    const dog = document.createElement("div");
    dog.className = "easter-dog";
    dog.innerHTML = '<span class="easter-dog-emoji">🐕</span>';
    document.body.appendChild(dog);
    setTimeout(function () {
      dog.remove();
    }, 7200);
  }

  function wireLogoEasterEgg() {
    const logo = document.querySelector(".topbar-logo");
    if (!logo) return;
    let clicks = 0;
    let lastClick = 0;
    logo.addEventListener("click", function (event) {
      const now = Date.now();
      if (now - lastClick > 3000) clicks = 0;
      lastClick = now;
      clicks += 1;
      if (clicks < 5) return;
      clicks = 0;
      event.preventDefault();
      triggerLogoEasterEgg();
    });
  }

  const realReviewGoAdd = document.getElementById("real-review-go-add");
  if (realReviewGoAdd) {
    realReviewGoAdd.onclick = () => {
      if (realShowManagePanel) realShowManagePanel("real-add-card");
    };
  }

  const realReviewSaveDaily = document.getElementById("real-review-save-daily-new");
  if (realReviewSaveDaily) {
    realReviewSaveDaily.onclick = () => {
      const input = document.getElementById("real-review-daily-new-limit");
      saveRealDailyNewLimit(Number(input.value));
    };
  }
  const realReviewDailyInput = document.getElementById("real-review-daily-new-limit");
  if (realReviewDailyInput) {
    realReviewDailyInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && realReviewSaveDaily) realReviewSaveDaily.click();
    });
  }

  /* 任意位置的发音按钮（data-real-audio）：复习卡、查词结果、浮动面板等。
     事件冒泡到 document 统一处理，避免每个容器重复绑定。 */
  document.addEventListener("click", (e) => {
    const audioButton = e.target.closest("[data-real-audio]");
    if (audioButton) {
      playRealAudio(audioButton.dataset.realAudio, audioButton);
      return;
    }
  });

  const realReviewCards = document.getElementById("real-review-cards");
  if (realReviewCards) {
    realReviewCards.addEventListener("click", (e) => {
      const buryButton = e.target.closest("[data-real-bury]");
      if (buryButton) {
        buryRealCard(buryButton);
        return;
      }
      const ratingButton = e.target.closest("[data-real-rating]");
      if (ratingButton) {
        rateRealReviewCard(ratingButton);
        return;
      }
      // 只有翻转按钮可以翻面；点击卡片空白处不翻面。
      const flipButton = e.target.closest("[data-real-flip]");
      if (flipButton) {
        const cardEl = flipButton.closest(".home-review-card");
        if (cardEl) cardEl.classList.toggle("flipped");
        return;
      }
      // 手机端没有翻转按钮：点击卡片空白区域翻面。
      if (isMobileLayout() && !e.target.closest("button, a, audio, .demo-audio-row")) {
        const cardEl = e.target.closest(".home-review-card");
        if (cardEl && !cardEl.classList.contains("flipped")) {
          cardEl.classList.add("flipped");
        } else if (cardEl) {
          cardEl.classList.remove("flipped");
        }
      }
    });
  }

  const realExtraOpen = document.getElementById("real-extra-open");
  const realExtraPanel = document.getElementById("real-extra-panel");
  if (realExtraOpen && realExtraPanel) {
    realExtraOpen.onclick = () => {
      realExtraPanel.hidden = !realExtraPanel.hidden;
    };
  }
  const realExtraStart = document.getElementById("real-extra-start");
  if (realExtraStart) {
    realExtraStart.onclick = async () => {
      const count = Math.min(100, Math.max(1, Number(document.getElementById("real-extra-count").value) || 5));
      const cardType = document.getElementById("real-extra-type").value;
      const status = document.getElementById("real-extra-status");
      realExtraStart.disabled = true;
      if (status) status.textContent = "加载中…";
      const params = new URLSearchParams({ extra_new: String(count) });
      if (cardType && cardType !== "all") params.set("card_type", cardType);
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      try {
        const res = await fetch("/api/cards?" + params.toString(), {
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "暂时不能继续学新卡");
        realReviewQueueVersion += 1;
        realReviewQueue = Array.isArray(data.queue) ? data.queue : [];
        realReviewHadCards = realReviewQueue.length > 0;
        realReviewTotalCards = Number(data.total_cards) || realReviewTotalCards;
        const remaining = data.remaining_counts || {};
        realReviewRemaining = remaining;
        realReviewRemainingTotal =
          Number(remaining.due || 0) +
          Number(remaining.new || 0) +
          Number(remaining.again || 0);
        realReviewCanExtraNew = Boolean(data.can_extra_new);
        realReviewCanUndo = Boolean(data.can_undo);
        if (status) status.textContent = "";
        if (realExtraPanel) realExtraPanel.hidden = true;
        renderRealReview();
      } catch (err) {
        if (status) {
          status.textContent = err.name === "AbortError"
            ? "加载超时，请重试"
            : err.message;
        }
      } finally {
        clearTimeout(timer);
        realExtraStart.disabled = false;
      }
    };
  }

  /* ---------- 登录后：DeepSeek 今日短文（只保留最近一组） ---------- */
  let articleGenerating = false;
  let articleGenerationError = "";

  function renderRealArticle(chapters) {
    const items = chapters.length ? chapters : [{}];
    document.getElementById("real-article-body").innerHTML = items.map(
      (item, index) => {
        const paragraphs = Array.isArray(item.paragraphs) ? item.paragraphs : [];
        const targetWords = Array.isArray(item.target_words) ? item.target_words : [];
        const targetCount = Number(item.target_count) || targetWords.length;
        const articleWords = Number(item.word_count) || 0;
        const title = item.article_title || item.title || ("今日短文 " + (index + 1));
        const multi = items.length > 1;
        return '<section class="ai-article-chapter">' +
          '<div class="ai-article-chapter-head">' +
          '<button type="button" class="ai-article-chapter-toggle" aria-expanded="true" data-title="' +
            escapeHtml(title) + '">' +
            (multi ? '<span class="ai-article-chapter-arrow">▾ </span>' : "") +
            escapeHtml(title) +
            ' <span class="ai-article-target-count">（目标 ' + targetCount +
            " 词 · 全文约 " + articleWords + " 词）</span>" +
          "</button>" +
          '<span class="ai-article-head-actions">' +
            '<button type="button" class="ai-article-words small" data-words-toggle>目标单词</button>' +
            '<button type="button" class="ai-article-cloze small" data-cloze-toggle>Cloze</button>' +
          "</span>" +
          "</div>" +
          '<div class="article-toggle-row">' +
            '<button type="button" class="ai-article-collapse small" data-article-collapse>▾ 收起</button>' +
          "</div>" +
          (targetWords.length
            ? '<div class="ai-article-targets" hidden>' +
              targetWords.map(
                (w) => '<span class="ai-article-target-chip">' + escapeHtml(w) + "</span>"
              ).join("") +
              "</div>"
            : "") +
          '<div class="ai-article-chapter-body">' +
          paragraphs.map((p) => "<p>" + p + "</p>").join("") +
          "</div>" +
          "</section>";
      }
    ).join("");
  }

  function toClozeHtml(html) {
    return html.replace(
      /<mark class="article-word">([\s\S]*?)<\/mark>/g,
      (match, word) =>
        '<button type="button" class="cloze-blank" data-answer="' +
        escapeHtml(word) + '">______</button>'
    );
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-article-collapse]");
    if (!btn) return;
    const section = btn.closest(".ai-article-chapter");
    if (!section) return;
    const body = section.querySelector(".ai-article-chapter-body");
    const targets = section.querySelector(".ai-article-targets");
    if (!body) return;
    const hidden = (body.hidden = !body.hidden);
    if (hidden && targets) targets.hidden = true;
    btn.textContent = hidden ? "▸ 展开" : "▾ 收起";
    const toggle = section.querySelector(".ai-article-chapter-toggle");
    if (toggle) {
      toggle.setAttribute("aria-expanded", String(!hidden));
      const arrow = toggle.querySelector(".ai-article-chapter-arrow");
      if (arrow) arrow.textContent = hidden ? "▸ " : "▾ ";
    }
  });

  document.addEventListener("click", (e) => {
    const toggle = e.target.closest(".ai-article-chapter-toggle");
    if (!toggle) return;
    const section = toggle.closest(".ai-article-chapter");
    const body = section && section.querySelector(".ai-article-chapter-body");
    const targets = section && section.querySelector(".ai-article-targets");
    if (!body) return;
    const hidden = (body.hidden = !body.hidden);
    if (hidden && targets) targets.hidden = true;
    toggle.setAttribute("aria-expanded", String(!hidden));
    const arrow = toggle.querySelector(".ai-article-chapter-arrow");
    if (arrow) arrow.textContent = hidden ? "▸ " : "▾ ";
    const collapseBtn = section.querySelector("[data-article-collapse]");
    if (collapseBtn) collapseBtn.textContent = hidden ? "▸ 展开" : "▾ 收起";
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-words-toggle]");
    if (!btn) return;
    const section = btn.closest(".ai-article-chapter");
    const targets = section && section.querySelector(".ai-article-targets");
    if (!targets) return;
    targets.hidden = !targets.hidden;
    btn.classList.toggle("active", !targets.hidden);
  });

  document.addEventListener("click", (e) => {
    const clozeBtn = e.target.closest("[data-cloze-toggle]");
    if (!clozeBtn) return;
    const section = clozeBtn.closest(".ai-article-chapter");
    const body = section && section.querySelector(".ai-article-chapter-body");
    if (!body) return;
    if (body.dataset.cloze === "1") {
      delete body.dataset.cloze;
      body.innerHTML = body._originalHtml || body.innerHTML;
      clozeBtn.classList.remove("active");
      clozeBtn.textContent = "Cloze";
    } else {
      if (!body._originalHtml) body._originalHtml = body.innerHTML;
      body.dataset.cloze = "1";
      body.innerHTML = toClozeHtml(body._originalHtml);
      clozeBtn.classList.add("active");
      clozeBtn.textContent = "退出 Cloze";
    }
  });

  document.addEventListener("click", (e) => {
    const blank = e.target.closest(".cloze-blank");
    if (!blank) return;
    if (blank.dataset.revealed === "1") {
      blank.textContent = "______";
      delete blank.dataset.revealed;
      blank.classList.remove("revealed");
    } else {
      blank.textContent = blank.dataset.answer || "";
      blank.dataset.revealed = "1";
      blank.classList.add("revealed");
    }
  });

  async function loadRealArticle() {
    const result = document.getElementById("real-article-result");
    const placeholder = document.getElementById("real-article-placeholder");
    if (!result) return false;
    try {
      const res = await fetch("/api/cards/article/latest", {
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return false;
      const generation = data.generation || {};
      if (generation.state === "generating") {
        const status = document.getElementById("real-article-status");
        if (status && generation.detail) status.textContent = generation.detail;
        return false;
      }
      if (generation.state === "failed") {
        articleGenerationError = generation.error || "文章生成失败，请重试";
        return false;
      }
      const article = data.article;
      if (!article) {
        result.hidden = true;
        if (placeholder) placeholder.hidden = false;
        return false;
      }
      result.hidden = false;
      if (placeholder) placeholder.hidden = true;
      const chapters = Array.isArray(article.chapters) && article.chapters.length
        ? article.chapters
        : [article];
      renderRealArticle(chapters);
      return true;
    } catch (_) {
      return false;
    }
  }

  async function waitForRealArticle(timeoutSeconds) {
    const deadline = Date.now() + timeoutSeconds * 1000;
    while (Date.now() < deadline) {
      if (await loadRealArticle()) return true;
      if (articleGenerationError) return false;
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
    return false;
  }

  const generateArticle = document.getElementById("real-article-generate");
  if (generateArticle) {
    generateArticle.onclick = async () => {
      if (articleGenerating) return;
      const status = document.getElementById("real-article-status");
      const result = document.getElementById("real-article-result");
      const placeholder = document.getElementById("real-article-placeholder");
      articleGenerating = true;
      generateArticle.disabled = true;
      status.hidden = false;
      status.textContent = "AI 正在生成今日短文…";
      result.hidden = true;
      if (placeholder) placeholder.hidden = false;
      try {
        const res = await fetch("/api/cards/article", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "无法启动今日短文生成");
        articleGenerationError = "";
        status.textContent = data.detail || "文章正在后台生成，可继续使用其他功能…";
        const recovered = await waitForRealArticle(10 * 60);
        if (!recovered) {
          throw new Error(articleGenerationError || "生成超时，请重试");
        }
        status.hidden = true;
        requestReviewRefresh();
      } catch (err) {
        status.hidden = false;
        status.textContent = "生成失败：" + err.message;
      } finally {
        articleGenerating = false;
        generateArticle.disabled = false;
      }
    };
  }

  /* ---------- 登录后：我的词库（三态 easy/mid/hard） ---------- */
  const realWordSelection = new Set();
  let realWordFilter = "all";

  function setRealWordStatus(text) {
    const el = document.getElementById("real-word-status");
    if (!el) return;
    el.textContent = text;
    el.hidden = false;
    clearTimeout(realWordStatusTimer);
    realWordStatusTimer = setTimeout(() => {
      el.hidden = true;
    }, 3000);
  }
  let realWordStatusTimer = null;

  async function loadRealProfile() {
    const input = document.getElementById("real-profile-known-rank");
    if (!input) return;
    try {
      const res = await fetch("/api/words/ngsl-profile", {
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      input.value = Number(data.known_rank) || 3000;
    } catch (err) {
      const status = document.getElementById("real-profile-status");
      if (status) status.textContent = "词汇量加载失败：" + err.message;
    }
  }

  async function saveRealProfile() {
    const input = document.getElementById("real-profile-known-rank");
    const status = document.getElementById("real-profile-status");
    if (!input || !status) return;
    const rank = Number(input.value);
    if (!Number.isInteger(rank) || rank < 0 || rank > 31000) {
      status.textContent = "请输入 0 - 31000 之间的整数";
      return;
    }
    const button = document.getElementById("real-profile-save");
    if (button) button.disabled = true;
    try {
      const res = await fetch("/api/words/ngsl-profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ known_rank: rank }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "保存失败");
      input.value = Number(data.known_rank) || rank;
      status.textContent = "已保存";
    } catch (err) {
      status.textContent = "保存失败：" + err.message;
    } finally {
      if (button) button.disabled = false;
    }
  }
  const realProfileSave = document.getElementById("real-profile-save");
  if (realProfileSave) realProfileSave.onclick = saveRealProfile;

  // 请求序号守卫 + 搜索防抖：输入快时慢响应不能覆盖新结果，
  // 也不能每个按键都发一次请求（照抄复习队列 realReviewQueueVersion 的模式）。
  let realWordsLoadVersion = 0;
  async function loadRealWords() {
    const search = document.getElementById("real-library-search");
    const query = search ? search.value.trim() : "";
    const params = new URLSearchParams({ limit: "120", status: realWordFilter });
    if (query) params.set("q", query);
    const loadVersion = ++realWordsLoadVersion;
    try {
      const res = await fetch("/api/words?" + params.toString(), {
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      if (loadVersion !== realWordsLoadVersion) return;
      const total = document.getElementById("real-word-total");
      if (total) total.textContent = "共 " + (Number(data.count) || 0) + " 个词";
      renderRealWords(data.words || []);
    } catch (err) {
      if (loadVersion !== realWordsLoadVersion) return;
      const box = document.getElementById("real-word-results");
      if (box) box.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
    }
  }

  function renderRealWords(words) {
    const box = document.getElementById("real-word-results");
    if (!box) return;
    if (!words.length) {
      box.innerHTML = '<div class="empty-state">词库里还没有单词。查词后可以把想学的词加入这里。</div>';
      return;
    }
    box.innerHTML = words.map((item) => {
      const meta = [
        item.rank ? "NGSL #" + item.rank : "不在 NGSL 词表",
        item.occurrences ? "出现 " + item.occurrences + " 次" : "尚未出现在文章中",
        item.corpus_count ? item.corpus_count + " 篇文章" : "",
        item.mid ? "已制卡" : "",
      ].filter(Boolean).join(" · ");
      const statusButtons = item.mid
        ? '<span class="word-status-badge word-status-mid">Mid（已制卡）</span>'
        : '<button type="button" class="word-status-btn' + (item.status === "easy" ? " active" : "") +
          '" data-word-status="easy" data-status-word="' + escapeHtml(item.word) + '">Easy</button>' +
          '<button type="button" class="word-status-btn' + (item.status === "hard" ? " active" : "") +
          '" data-word-status="hard" data-status-word="' + escapeHtml(item.word) + '">Hard（生词库）</button>';
      return (
        '<article class="word-result status-' + item.status + '">' +
        '<label class="check-row word-batch-check" title="选择用于批量操作">' +
        '<input type="checkbox" data-select-word="' + escapeHtml(item.word) + '">' +
        "</label>" +
        '<div class="word-result-main"><div class="word-title-row"><h3>' +
        '<button class="word-link" data-real-lookup="' + escapeHtml(item.word) + '" type="button">' +
        escapeHtml(item.word) + "</button></h3></div>" +
        '<div class="meta">' + escapeHtml(meta) + "</div></div>" +
        '<div class="word-result-actions">' + statusButtons +
        '<button class="small danger" data-delete-word="' +
        escapeHtml(item.word) + '" type="button">移出</button>' +
        "</div></article>"
      );
    }).join("");
    box.querySelectorAll("input[data-select-word]").forEach((input) => {
      input.checked = realWordSelection.has(input.dataset.selectWord);
    });
    updateRealWordBatch();
  }

  const realWordFilters = document.querySelectorAll("[data-word-status-filter]");
  realWordFilters.forEach((button) => {
    button.addEventListener("click", () => {
      realWordFilter = button.dataset.wordStatusFilter;
      realWordFilters.forEach((b) => b.classList.toggle("active", b === button));
      loadRealWords();
    });
  });

  const realLibrarySearch = document.getElementById("real-library-search");
  if (realLibrarySearch) {
    let searchDebounceTimer = 0;
    realLibrarySearch.addEventListener("input", () => {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => loadRealWords(), 250);
    });
  }
  const realWordResults = document.getElementById("real-word-results");
  if (realWordResults) {
    realWordResults.addEventListener("click", async (e) => {
      const lookupBtn = e.target.closest("[data-real-lookup]");
      if (lookupBtn) {
        lookupFloating(lookupBtn.dataset.realLookup, e.clientX, e.clientY);
        return;
      }
      const deleteBtn = e.target.closest("[data-delete-word]");
      if (deleteBtn) {
        await deleteRealWord(deleteBtn);
        return;
      }
      const statusBtn = e.target.closest("[data-word-status]");
      if (statusBtn) {
        const word = statusBtn.dataset.statusWord;
        const status = statusBtn.dataset.wordStatus;
        try {
          const res = await fetch("/api/words/batch-status", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ words: [word], status }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "标记失败");
          await loadRealWords();
        } catch (err) {
          setRealWordStatus(err.message);
        }
      }
    });
    realWordResults.addEventListener("change", (e) => {
      const checkbox = e.target.closest("input[data-select-word]");
      if (!checkbox) return;
      const word = checkbox.dataset.selectWord;
      if (checkbox.checked) realWordSelection.add(word);
      else realWordSelection.delete(word);
      updateRealWordBatch();
    });
  }

  function updateRealWordBatch() {
    const bar = document.getElementById("real-word-batch");
    const count = document.getElementById("real-word-batch-count");
    const all = document.getElementById("real-word-batch-all");
    if (bar) bar.hidden = realWordSelection.size === 0;
    if (count) count.textContent = "已选 " + realWordSelection.size + " 个";
    if (all) {
      const visible = [
        ...document.querySelectorAll("#real-word-results input[data-select-word]"),
      ];
      const checked = visible.filter((input) => input.checked).length;
      all.checked = visible.length > 0 && checked === visible.length;
      all.indeterminate = checked > 0 && checked < visible.length;
    }
  }

  async function deleteRealWord(button) {
    const word = button.dataset.deleteWord;
    if (!confirm("将「" + word + "」移出词库？")) {
      return;
    }
    try {
      const res = await fetch("/api/words/" + encodeURIComponent(word), {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "删除失败");
      realWordSelection.delete(word);
      setRealWordStatus("已将「" + word + "」移出词库");
      await loadRealWords();
    } catch (err) {
      const box = document.getElementById("real-word-results");
      if (box) box.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
    }
  }

  const realWordBatchAll = document.getElementById("real-word-batch-all");
  if (realWordBatchAll) {
    realWordBatchAll.onchange = () => {
      document.querySelectorAll("#real-word-results input[data-select-word]").forEach((input) => {
        const word = input.dataset.selectWord;
        if (realWordBatchAll.checked) {
          input.checked = true;
          realWordSelection.add(word);
        } else {
          input.checked = false;
          realWordSelection.delete(word);
        }
      });
      updateRealWordBatch();
    };
  }
  const realWordBatchDelete = document.getElementById("real-word-batch-delete");
  if (realWordBatchDelete) {
    realWordBatchDelete.onclick = async () => {
      if (!realWordSelection.size) return;
      if (!confirm("确定将选中的 " + realWordSelection.size + " 个词移出词库？")) return;
      realWordBatchDelete.disabled = true;
      try {
        const res = await fetch("/api/words/delete-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ words: [...realWordSelection] }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "批量删除失败");
        realWordSelection.clear();
        updateRealWordBatch();
        await Promise.all([loadRealWords(), loadRealBrowser()]);
        requestReviewRefresh();
        setRealWordStatus("已将 " + data.deleted + " 个词移出词库");
      } catch (err) {
        setRealWordStatus(err.message);
      } finally {
        realWordBatchDelete.disabled = false;
      }
    };
  }

  async function markSelectedWords(status) {
    if (!realWordSelection.size) return;
    try {
      const res = await fetch("/api/words/batch-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ words: [...realWordSelection], status }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "批量标记失败");
      setRealWordStatus("已将 " + data.updated + " 个词标记为 " + status);
      realWordSelection.clear();
      updateRealWordBatch();
      await loadRealWords();
    } catch (err) {
      setRealWordStatus(err.message);
    }
  }
  const realWordBatchEasy = document.getElementById("real-word-batch-easy");
  if (realWordBatchEasy) {
    realWordBatchEasy.onclick = () => markSelectedWords("easy");
  }
  const realWordBatchHard = document.getElementById("real-word-batch-hard");
  if (realWordBatchHard) {
    realWordBatchHard.onclick = () => markSelectedWords("hard");
  }
  const realWordBatchClear = document.getElementById("real-word-batch-clear");
  if (realWordBatchClear) {
    realWordBatchClear.onclick = () => {
      realWordSelection.clear();
      document.querySelectorAll("#real-word-results input[data-select-word]").forEach((input) => {
        input.checked = false;
      });
      updateRealWordBatch();
    };
  }

  /* ---------- 批量标记 Easy（内置词表 / 粘贴） ---------- */
  const realWordMarkEasyPanel = document.getElementById("real-word-mark-easy");
  const realWordMarkEasyOpen = document.getElementById("real-word-batch-easy-open");
  const realWordMarkEasyClose = document.getElementById("real-word-mark-easy-close");
  if (realWordMarkEasyOpen && realWordMarkEasyPanel) {
    realWordMarkEasyOpen.onclick = () => {
      realWordMarkEasyPanel.hidden = false;
      loadRealWordMarkEasyLists();
    };
  }
  if (realWordMarkEasyClose && realWordMarkEasyPanel) {
    realWordMarkEasyClose.onclick = () => {
      realWordMarkEasyPanel.hidden = true;
    };
  }
  const realWordMarkEasyListToggle = document.getElementById("real-word-mark-easy-list-toggle");
  const realWordMarkEasyListFields = document.getElementById("real-word-mark-easy-list-fields");
  const realWordMarkEasyNgslToggle = document.getElementById("real-word-mark-easy-ngsl-toggle");
  const realWordMarkEasyNgslFields = document.getElementById("real-word-mark-easy-ngsl-fields");
  const realWordMarkEasyPasteToggle = document.getElementById("real-word-mark-easy-paste-toggle");
  const realWordMarkEasyPasteFields = document.getElementById("real-word-mark-easy-paste-fields");
  if (realWordMarkEasyListToggle && realWordMarkEasyListFields) {
    realWordMarkEasyListToggle.onchange = () => {
      realWordMarkEasyListFields.hidden = !realWordMarkEasyListToggle.checked;
    };
  }
  if (realWordMarkEasyNgslToggle && realWordMarkEasyNgslFields) {
    realWordMarkEasyNgslToggle.onchange = () => {
      realWordMarkEasyNgslFields.hidden = !realWordMarkEasyNgslToggle.checked;
    };
  }
  if (realWordMarkEasyPasteToggle && realWordMarkEasyPasteFields) {
    realWordMarkEasyPasteToggle.onchange = () => {
      realWordMarkEasyPasteFields.hidden = !realWordMarkEasyPasteToggle.checked;
    };
  }

  let realWordMarkEasyListsLoaded = false;
  async function loadRealWordMarkEasyLists() {
    const select = document.getElementById("real-word-mark-easy-list-id");
    if (!select || realWordMarkEasyListsLoaded) return;
    try {
      const res = await fetch("/api/wordlists", {
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      realWordMarkEasyListsLoaded = true;
      select.innerHTML = (data.lists || [])
        .map((l) => `<option value="${l.id}">${l.name}（${l.count}）</option>`)
        .join("");
    } catch (err) {
      select.innerHTML = '<option value="">词表加载失败</option>';
    }
  }

  const realWordMarkEasyRun = document.getElementById("real-word-mark-easy-run");
  if (realWordMarkEasyRun) {
    realWordMarkEasyRun.onclick = async () => {
      const statusEl = document.getElementById("real-word-mark-easy-status");
      const words = new Set();
      if (realWordMarkEasyListToggle && realWordMarkEasyListToggle.checked) {
        const listId = document.getElementById("real-word-mark-easy-list-id");
        if (!listId || !listId.value) {
          if (statusEl) statusEl.textContent = "请选择词表";
          return;
        }
        try {
          const res = await fetch("/api/card-studio/targets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: "builtin",
              list_id: listId.value,
              from_rank: 1,
              to_rank: 31000,
              count: 5000,
              randomize: false,
              include_unknown: true,
              card_type: "general",
            }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "提取词表失败");
          (data.words || []).forEach((item) => words.add(item.word));
        } catch (err) {
          if (statusEl) statusEl.textContent = err.message;
          return;
        }
      }
      if (realWordMarkEasyNgslToggle && realWordMarkEasyNgslToggle.checked) {
        const fromInput = document.getElementById("real-word-mark-easy-ngsl-from");
        const toInput = document.getElementById("real-word-mark-easy-ngsl-to");
        const fromRank = Number(fromInput && fromInput.value) || 1;
        const toRank = Number(toInput && toInput.value) || 3000;
        try {
          const res = await fetch("/api/card-studio/targets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: "ngsl",
              from_rank: fromRank,
              to_rank: toRank,
              count: 5000,
              randomize: false,
              include_unknown: true,
              card_type: "general",
            }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "提取 NGSL 失败");
          (data.words || []).forEach((item) => words.add(item.word));
        } catch (err) {
          if (statusEl) statusEl.textContent = err.message;
          return;
        }
      }
      if (realWordMarkEasyPasteToggle && realWordMarkEasyPasteToggle.checked) {
        const paste = document.getElementById("real-word-mark-easy-paste");
        (paste.value || "")
          .split(/\n|,|，/)
          .map((s) => s.trim().toLowerCase())
          .filter(Boolean)
          .forEach((w) => words.add(w));
      }
      if (!words.size) {
        if (statusEl) statusEl.textContent = "请勾选词表或粘贴单词";
        return;
      }
      realWordMarkEasyRun.disabled = true;
      const chunk = [...words];
      const countText = (data, prefix) =>
        prefix + data.updated + " 个词" +
        (data.skipped_existing
          ? "（已 Easy 的 " + data.skipped_existing + " 个跳过）"
          : "");
      try {
        if (statusEl) statusEl.textContent = "正在标记 " + chunk.length + " 个词…";
        const res = await fetch("/api/words/batch-status", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ words: chunk, status: "easy" }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "批量标记失败");
        if (statusEl) {
          statusEl.textContent = "已将 " + countText(data, "");
        }
        await loadRealWords();
      } catch (err) {
        if (statusEl) statusEl.textContent = err.message;
      } finally {
        realWordMarkEasyRun.disabled = false;
      }
    };
  }

  /* ---------- 登录后：学习卡片 ---------- */
  let realBrowseOffset = 0;
  const realBrowseLimit = 30;
  const realBrowserSelection = new Set();

  function renderBrowserBatchBar() {
    const bar = document.getElementById("real-browser-batch");
    const count = document.getElementById("real-browser-batch-count");
    if (bar) bar.hidden = realBrowserSelection.size === 0;
    if (count) count.textContent = "已选 " + realBrowserSelection.size + " 张";
    const all = document.getElementById("real-browser-batch-all");
    if (all) {
      const boxes = document.querySelectorAll(
        "#real-browser-results input[data-select-browser-card]"
      );
      all.checked =
        boxes.length > 0 &&
        Array.from(boxes).every((box) => box.checked);
    }
  }

  function setBrowserSelection(id, checked) {
    if (checked) realBrowserSelection.add(id);
    else realBrowserSelection.delete(id);
    renderBrowserBatchBar();
  }

  async function loadRealBrowser() {
    const results = document.getElementById("real-browser-results");
    if (!results) return;
    const params = new URLSearchParams({
      q: document.getElementById("real-browser-query").value.trim(),
      state: document.getElementById("real-browser-state").value,
      card_type: document.getElementById("real-browser-type").value,
      sort: document.getElementById("real-browser-sort")
        ? document.getElementById("real-browser-sort").value
        : "time",
      limit: String(realBrowseLimit),
      offset: String(realBrowseOffset),
    });
    try {
      const res = await fetch("/api/cards/browse?" + params.toString(), {
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      const totalPages = Math.max(1, Math.ceil(data.total / data.limit));
      const currentPage = Math.min(totalPages, Math.floor(data.offset / data.limit) + 1);
      const totalEl = document.getElementById("real-browser-total");
      if (totalEl) {
        const filtered =
          params.get("q") ||
          params.get("state") !== "all" ||
          params.get("card_type") !== "all";
        totalEl.textContent =
          "共 " + data.total + " 张卡片" + (filtered ? "（当前筛选）" : "");
      }
      document.getElementById("real-browser-page").textContent = currentPage + " / " + totalPages;
      document.getElementById("real-browser-prev").disabled = data.offset <= 0;
      document.getElementById("real-browser-next").disabled = data.offset + data.limit >= data.total;
      if (!data.cards.length) {
        results.innerHTML = '<div class="empty-state">没有符合条件的卡片。</div>';
        return;
      }
      results.innerHTML = data.cards.map((card) => {
        const front =
          (card.defaults ? '<span class="hint">' + escapeHtml(card.defaults) + "</span><br>" : "") +
          renderMarkdown(card.front, card.word);
        const back = card.card_type === "speaking"
          ? escapeHtml(card.back).replace(/\s*\|\|\s*/g, "<br>")
          : renderMarkdown(card.back, card.word);
        return (
          '<div class="browser-card-wrap">' +
          '<label class="browser-select-check" data-browser-select-wrap="' + card.id + '" title="选择这张卡">' +
          '<input type="checkbox" data-select-browser-card="' + card.id + '"' +
          (realBrowserSelection.has(card.id) ? " checked" : "") +
          ' aria-label="选择 ' + escapeHtml(card.word) + '"></label>' +
          '<details class="browser-card"><summary>' +
          "<strong>" + escapeHtml(card.word) +
          (card.buried ? ' <span class="browser-buried">不想学</span>' : "") + "</strong>" +
          "<span>" + escapeHtml(card.deck || card.card_type) + " · " +
          (card.ngsl_rank ? "NGSL #" + Number(card.ngsl_rank) : "不在 NGSL 词表") + " · " +
          (card.next_review_date ? "待复习 " + card.next_review_date : "新卡") + "</span></summary>" +
          '<div class="browser-card-content"><div><b>正面</b><p>' + front + "</p></div>" +
          '<div><b>背面</b><p>' + back + "</p></div>" +
          '<div class="browser-card-actions">' +
          (card.buried
            ? '<button class="small" data-real-unbury="' + card.id + '" type="button">恢复这张卡</button>'
            : '<button class="small" data-real-bury="' + card.id + '" type="button">不想学</button>') +
          (card.card_type === "reading"
            ? '<button class="small" data-real-refresh-sentence="' + card.id + '" type="button">换句</button>'
            : "") +
          '<button class="small danger" data-real-delete-card="' + card.id +
          '" data-real-delete-word="' + escapeHtml(card.word) + '" type="button">删除这张卡</button>' +
          "</div></div></details></div>"
        );
      }).join("");
      renderBrowserBatchBar();
    } catch (err) {
      results.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
    }
  }

  const realBrowserSearch = document.getElementById("real-browser-search");
  if (realBrowserSearch) {
    realBrowserSearch.onclick = () => { realBrowseOffset = 0; loadRealBrowser(); };
  }
  const realBrowserQuery = document.getElementById("real-browser-query");
  if (realBrowserQuery) {
    realBrowserQuery.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        realBrowseOffset = 0;
        loadRealBrowser();
      }
    });
  }
  const realBrowserState = document.getElementById("real-browser-state");
  if (realBrowserState) {
    realBrowserState.onchange = () => { realBrowseOffset = 0; loadRealBrowser(); };
  }
  const realBrowserType = document.getElementById("real-browser-type");
  if (realBrowserType) {
    realBrowserType.onchange = () => { realBrowseOffset = 0; loadRealBrowser(); };
  }
  const realBrowserSort = document.getElementById("real-browser-sort");
  if (realBrowserSort) {
    realBrowserSort.onchange = () => { realBrowseOffset = 0; loadRealBrowser(); };
  }
  const realBrowserPrev = document.getElementById("real-browser-prev");
  if (realBrowserPrev) {
    realBrowserPrev.onclick = () => {
      realBrowseOffset = Math.max(0, realBrowseOffset - realBrowseLimit);
      loadRealBrowser();
    };
  }
  const realBrowserNext = document.getElementById("real-browser-next");
  if (realBrowserNext) {
    realBrowserNext.onclick = () => {
      realBrowseOffset += realBrowseLimit;
      loadRealBrowser();
    };
  }
  const realBrowserBatchAll = document.getElementById("real-browser-batch-all");
  if (realBrowserBatchAll) {
    realBrowserBatchAll.onchange = () => {
      document
        .querySelectorAll("#real-browser-results input[data-select-browser-card]")
        .forEach((box) => {
          box.checked = realBrowserBatchAll.checked;
          const id = Number(box.dataset.selectBrowserCard);
          if (realBrowserBatchAll.checked) realBrowserSelection.add(id);
          else realBrowserSelection.delete(id);
        });
      renderBrowserBatchBar();
    };
  }
  const realBrowserBatchClear = document.getElementById("real-browser-batch-clear");
  if (realBrowserBatchClear) {
    realBrowserBatchClear.onclick = () => {
      realBrowserSelection.clear();
      document
        .querySelectorAll("#real-browser-results input[data-select-browser-card]")
        .forEach((box) => {
          box.checked = false;
        });
      renderBrowserBatchBar();
    };
  }
  const realBrowserSelectAll = document.getElementById("real-browser-select-all");
  if (realBrowserSelectAll) {
    realBrowserSelectAll.onclick = async () => {
      realBrowserSelectAll.disabled = true;
      try {
        const params = new URLSearchParams({
          q: document.getElementById("real-browser-query").value.trim(),
          state: document.getElementById("real-browser-state").value,
          card_type: document.getElementById("real-browser-type").value,
          limit: "2000",
          offset: "0",
          ids_only: "true",
        });
        const res = await fetch("/api/cards/browse?" + params.toString(), {
          headers: { "Content-Type": "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "加载失败");
        (data.ids || []).forEach((id) => realBrowserSelection.add(Number(id)));
        renderBrowserBatchBar();
        document
          .querySelectorAll("#real-browser-results input[data-select-browser-card]")
          .forEach((box) => {
            box.checked = realBrowserSelection.has(Number(box.dataset.selectBrowserCard));
          });
        const count = document.getElementById("real-browser-batch-count");
        const total = Number(data.total) || 0;
        if (count && total > (data.ids || []).length) {
          count.textContent =
            "已选 " + realBrowserSelection.size + " 张（当前筛选共 " + total + " 张，已选前 2000 张）";
        }
      } catch (err) {
        const results = document.getElementById("real-browser-results");
        if (results) {
          results.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
        }
      } finally {
        realBrowserSelectAll.disabled = false;
      }
    };
  }
  const realBrowserBatchDelete = document.getElementById("real-browser-batch-delete");
  if (realBrowserBatchDelete) {
    realBrowserBatchDelete.onclick = async () => {
      if (!realBrowserSelection.size) return;
      if (!confirm("确定删除选中的 " + realBrowserSelection.size + " 张卡片？")) return;
      realBrowserBatchDelete.disabled = true;
      try {
        const res = await fetch("/api/cards/delete-batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ card_ids: Array.from(realBrowserSelection) }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "删除失败");
        realBrowserSelection.clear();
        renderBrowserBatchBar();
        loadRealBrowser();
        requestReviewRefresh();
      } catch (err) {
        const results = document.getElementById("real-browser-results");
        if (results) {
          results.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
        }
      } finally {
        realBrowserBatchDelete.disabled = false;
      }
    };
  }
  async function loadSentenceRefreshPreference() {
    try {
      const res = await fetch("/api/cards/sentence-refresh-preference");
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      const select = document.getElementById("real-sentence-refresh-interval");
      if (select) select.value = String(data.interval || 0);
    } catch (err) {
      // 加载失败保持默认关闭即可
    }
  }

  async function saveSentenceRefreshPreference() {
    const select = document.getElementById("real-sentence-refresh-interval");
    const status = document.getElementById("real-sentence-refresh-status");
    if (!select) return;
    const interval = parseInt(select.value, 10) || 0;
    try {
      const res = await fetch("/api/cards/sentence-refresh-preference", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interval }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "保存失败");
      if (status) status.textContent = "已保存";
    } catch (err) {
      if (status) status.textContent = err.message || "保存失败";
    }
  }

  async function refreshSentencesNow() {
    const status = document.getElementById("real-sentence-refresh-status");
    const button = document.getElementById("real-sentence-refresh-now");
    if (button) button.disabled = true;
    try {
      const res = await fetch("/api/cards/refresh-sentences", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "刷新失败");
      if (status) status.textContent = "已换 " + (data.updated || 0) + " 张";
      loadRealBrowser();
    } catch (err) {
      if (status) status.textContent = err.message || "刷新失败";
    } finally {
      if (button) button.disabled = false;
    }
  }

  const realSentenceRefreshSave = document.getElementById("real-sentence-refresh-save");
  if (realSentenceRefreshSave) {
    realSentenceRefreshSave.onclick = saveSentenceRefreshPreference;
  }
  const realSentenceRefreshNow = document.getElementById("real-sentence-refresh-now");
  if (realSentenceRefreshNow) {
    realSentenceRefreshNow.onclick = refreshSentencesNow;
  }

  const realBrowserResults = document.getElementById("real-browser-results");
  if (realBrowserResults) {
    realBrowserResults.addEventListener("click", async (e) => {
      const bury = e.target.closest("[data-real-bury]");
      if (bury) {
        try {
          const res = await fetch("/api/cards/" + bury.dataset.realBury + "/bury", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "操作失败");
          loadRealBrowser();
          requestReviewRefresh();
        } catch (err) {
          realBrowserResults.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
        }
        return;
      }
      const unbury = e.target.closest("[data-real-unbury]");
      if (unbury) {
        try {
          const res = await fetch("/api/cards/" + unbury.dataset.realUnbury + "/unbury", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "操作失败");
          loadRealBrowser();
          requestReviewRefresh();
        } catch (err) {
          realBrowserResults.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
        }
        return;
      }
      const refreshSentence = e.target.closest("[data-real-refresh-sentence]");
      if (refreshSentence) {
        try {
          const res = await fetch("/api/cards/" + refreshSentence.dataset.realRefreshSentence + "/refresh-sentence", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "换句失败");
          loadRealBrowser();
        } catch (err) {
          realBrowserResults.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
        }
        return;
      }
      const del = e.target.closest("[data-real-delete-card]");
      if (!del) return;
      if (!confirm("确定删除这张卡？" + (del.dataset.realDeleteWord ? "（" + del.dataset.realDeleteWord + "）" : ""))) return;
      try {
        const res = await fetch("/api/cards/" + del.dataset.realDeleteCard, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "删除失败");
        realBrowserSelection.delete(Number(del.dataset.realDeleteCard));
        renderBrowserBatchBar();
        loadRealBrowser();
        requestReviewRefresh();
      } catch (err) {
        realBrowserResults.innerHTML = '<div class="empty-state">' + escapeHtml(err.message) + "</div>";
      }
    });
    realBrowserResults.addEventListener("change", (e) => {
      const box = e.target.closest("input[data-select-browser-card]");
      if (!box) return;
      setBrowserSelection(Number(box.dataset.selectBrowserCard), box.checked);
    });
  }

  /* ---------- 登录后：首页新增卡（不跳转） ---------- */
  let realCardType = "";
  const realCardTypes = document.getElementById("real-card-types");

  function setWizardStep(step) {
    const indicators = {
      1: document.getElementById("card-wizard-step-2"),
      2: document.getElementById("card-wizard-step-3"),
    };
    Object.entries(indicators).forEach(([num, el]) => {
      if (!el) return;
      el.classList.toggle("active", step >= Number(num));
    });
    const stepEls = {
      1: document.getElementById("card-step-1"),
      2: document.getElementById("card-step-2"),
      3: document.getElementById("card-step-3"),
    };
    Object.entries(stepEls).forEach(([num, el]) => {
      if (el) el.hidden = Number(num) > step;
    });
  }

  function setRealCardType(type) {
    realCardType = type;
    if (realCardTypes) {
      realCardTypes.querySelectorAll("button").forEach((b) =>
        b.classList.toggle("selected", b.dataset.cardType === type)
      );
    }
    const hint = document.getElementById("real-card-step1-hint");
    if (hint) hint.hidden = true;
    setWizardStep(2);
  }
  if (realCardTypes) {
    realCardTypes.addEventListener("click", (e) => {
      const button = e.target.closest("button[data-card-type]");
      if (!button) return;
      setRealCardType(button.dataset.cardType);
    });
  }

  const realCardStep2Back = document.getElementById("real-card-step2-back");
  if (realCardStep2Back) {
    realCardStep2Back.onclick = () => {
      realCardType = "";
      if (realCardTypes) {
        realCardTypes.querySelectorAll("button").forEach((b) =>
          b.classList.remove("selected")
        );
      }
      const hint = document.getElementById("real-card-step1-hint");
      if (hint) hint.hidden = false;
      setWizardStep(1);
    };
  }
  const realCardStep3Back = document.getElementById("real-card-step3-back");
  if (realCardStep3Back) {
    realCardStep3Back.onclick = () => setWizardStep(2);
  }

  const realCardSource = document.getElementById("real-card-source");
  const realCardSourceText = document.getElementById("real-card-source-text");
  const realCardCorpusHint = document.getElementById("real-card-corpus-hint");
  const realCardFileFields = document.getElementById("real-card-file-fields");
  const realCardFile = document.getElementById("real-card-file");
  const realCardNgslSourceFields = document.getElementById("real-card-ngsl-source-fields");
  const realCardTopicFields = document.getElementById("real-card-topic-fields");
  const realNeedsFields = document.getElementById("real-needs-fields");
  const realCardListFields = document.getElementById("real-card-list-fields");
  const realCardNgslFilterRow = document.getElementById("real-card-ngsl-filter-row");
  const realCardNgslFilter = document.getElementById("real-card-ngsl-filter");
  const realCardNgslFilterFields = document.getElementById("real-card-ngsl-filter-fields");
  const realCardNgslFilterHint = document.getElementById("real-card-ngsl-filter-hint");

  let realCardListsLoaded = false;
  let realCardLists = null;
  async function loadRealCardLists(force) {
    if (realCardListsLoaded && !force) return;
    realCardListsLoaded = false;
    const select = document.getElementById("real-card-list-id");
    if (!select) return;
    try {
      const res = await fetch("/api/wordlists", {
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      realCardLists = data.lists || [];
      realCardListsLoaded = true;
      select.innerHTML = realCardLists
        .map((l) => `<option value="${l.id}">${l.name}（${l.count}）</option>`)
        .join("");
    } catch (err) {
      select.innerHTML = '<option value="">词表加载失败</option>';
    }
  }

  function renderRealCardSourceFields() {
    if (!realCardSource) return;
    const source = realCardSource.value;
    if (realCardSourceText) {
      realCardSourceText.hidden = source !== "corpus" && source !== "wordlist";
      realCardSourceText.placeholder =
        source === "corpus"
          ? "粘贴文章或文本，自动提取目标单词…"
          : "每行一个单词或短语";
    }
    if (realCardCorpusHint) realCardCorpusHint.hidden = source !== "corpus";
    if (realCardFileFields) realCardFileFields.hidden = source !== "file";
    if (realCardNgslSourceFields) realCardNgslSourceFields.hidden = source !== "ngsl";
    if (realCardTopicFields) realCardTopicFields.hidden = source !== "topic";
    if (realNeedsFields) realNeedsFields.hidden = source !== "needs";
    if (realCardListFields) realCardListFields.hidden = source !== "builtin";
    if (realCardNgslFilterRow) realCardNgslFilterRow.hidden = source === "needs";
    if (realCardNgslFilter) {
      if (source === "ngsl") {
        realCardNgslFilter.checked = true;
        realCardNgslFilter.disabled = true;
      } else {
        if (realCardNgslFilter.disabled) realCardNgslFilter.checked = false;
        realCardNgslFilter.disabled = false;
      }
    }
    if (realCardNgslFilterHint) {
      realCardNgslFilterHint.textContent = source === "ngsl"
        ? "NGSL 词库本身始终按照排名范围选词"
        : "开启后只保留 NGSL 中位于该排名范围的词";
    }
    renderRealCardNgslFilterFields();
    if (source === "builtin") {
      loadRealCardLists();
    }
    if (source === "needs") {
      if (realCardType !== "speaking") setRealCardType("speaking");
      loadRealSpeakingNeeds();
    }
  }

  function renderRealCardNgslFilterFields() {
    if (!realCardNgslFilter || !realCardNgslFilterFields) return;
    realCardNgslFilterFields.hidden = !realCardNgslFilter.checked;
  }

  function realCardNgslOptions(source) {
    const enabled = source === "ngsl" || Boolean(realCardNgslFilter && realCardNgslFilter.checked);
    return {
      enabled,
      fromRank: enabled ? Number(document.getElementById("real-card-rank-from").value) || 1 : 1,
      toRank: enabled ? Number(document.getElementById("real-card-rank-to").value) || 31000 : 31000,
      includeUnknown: !enabled,
    };
  }

  // 提取结果统一送后端处理：既做 NGSL 筛选（勾选时），也把已有同类型
  // 卡片的词去重。此前仅在勾选 NGSL 筛选时才走后端，未勾选时粘贴词表
  // 和 AI 主题词会原样保留已有同类型卡的词。
  async function refineRealCardTargets(words, source) {
    const options = realCardNgslOptions(source);
    const res = await fetch("/api/card-studio/targets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "wordlist",
        text: words.join("\n"),
        from_rank: options.enabled ? options.fromRank : 1,
        to_rank: options.enabled ? options.toRank : 31000,
        count: 5000,
        include_unknown: !options.enabled,
        ngsl_filter: options.enabled,
        card_type: realCardType,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "目标词筛选失败");
    return (data.words || []).map((item) => item.word);
  }

  // 口语表达需求去重：口语卡的 word 是 need-id、front 才是需求原文，
  // 因此走 expressions 来源与已有同类型卡的 front 比对。
  async function refineRealCardExpressions(fronts) {
    const res = await fetch("/api/card-studio/targets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source: "expressions",
        text: fronts.join("\n"),
        count: 5000,
        card_type: realCardType,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "表达需求去重失败");
    return (data.words || []).map((item) => item.word);
  }

  if (realCardNgslFilter) {
    realCardNgslFilter.onchange = renderRealCardNgslFilterFields;
  }

  if (realCardSource) {
    realCardSource.onchange = renderRealCardSourceFields;
  }
  renderRealCardSourceFields();

  /* ---------- 口语素材（内置表达需求集） ---------- */
  let realNeeds = null;
  let realNeedsLoaded = false;

  async function loadRealSpeakingNeeds(force) {
    if (realNeedsLoaded && !force) return;
    realNeedsLoaded = false;
    const box = document.getElementById("real-needs-list");
    if (box) box.innerHTML = '<span class="hint">正在加载口语素材…</span>';
    try {
      const res = await fetch("/api/card-studio/needs", {
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加载失败");
      realNeeds = data;
      realNeedsLoaded = true;
      renderRealSpeakingNeeds();
    } catch (err) {
      if (box) {
        box.innerHTML =
          '<span class="hint">口语素材加载失败，请刷新页面重试；也可以用“单词表”粘贴需求（每行一个）。</span>';
      }
    }
  }

  function renderRealSpeakingNeeds() {
    const box = document.getElementById("real-needs-list");
    if (!box || !realNeeds) return;
    const categories = realNeeds.categories || [];
    box.innerHTML = categories.map((group, groupIndex) =>
      '<div class="speaking-need-group">' +
      '<label class="speaking-category-row">' +
      '<input type="checkbox" data-category-check="' + groupIndex + '">' +
      '<span class="speaking-category-name">' + escapeHtml(group.name) +
      ' <span class="hint">' + group.needs.length + " 条</span></span>" +
      '<button type="button" class="speaking-category-toggle small" data-category-expand="' + groupIndex + '" aria-expanded="false">展开</button>' +
      "</label>" +
      '<div class="speaking-need-items" data-category-items="' + groupIndex + '" hidden>' +
      group.needs.map((need) =>
        '<label class="speaking-need-item">' +
        '<input type="checkbox" data-need-front="' + escapeHtml(need.front) + '"' +
        (need.has_card ? " disabled" : "") + ">" +
        '<span>' + escapeHtml(need.front) +
        (need.has_card ? ' <em class="hint">已制卡</em>' : "") +
        "</span></label>"
      ).join("") +
      "</div></div>"
    ).join("");
    box.querySelectorAll("input[data-need-front]").forEach((input) => {
      input.addEventListener("change", () => {
        updateRealSpeakingNeedsCount();
        refreshRealCategoryChecks();
      });
    });
    box.querySelectorAll("input[data-category-check]").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const index = Number(checkbox.dataset.categoryCheck);
        const group = categories[index];
        if (!group) return;
        const inputs = [...box.querySelectorAll("input[data-need-front]:not(:disabled)")];
        const groupInputs = inputs.filter((input) =>
          group.needs.some((need) => need.front === input.dataset.needFront)
        );
        groupInputs.forEach((input) => {
          input.checked = checkbox.checked;
        });
        updateRealSpeakingNeedsCount();
        refreshRealCategoryChecks();
      });
    });
    box.querySelectorAll("button[data-category-expand]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        const index = Number(button.dataset.categoryExpand);
        setRealCategoryExpanded(index, button.getAttribute("aria-expanded") !== "true");
      });
    });
    const search = document.getElementById("real-needs-search");
    if (search) {
      search.addEventListener("input", () => applyRealSpeakingNeedsSearch(search.value));
    }
    updateRealSpeakingNeedsCount();
    refreshRealCategoryChecks();
  }

  function refreshRealCategoryChecks() {
    const box = document.getElementById("real-needs-list");
    if (!box || !realNeeds) return;
    (realNeeds.categories || []).forEach((group, index) => {
      const checkbox = box.querySelector('input[data-category-check="' + index + '"]');
      if (!checkbox) return;
      const groupInputs = [...box.querySelectorAll("input[data-need-front]:not(:disabled)")]
        .filter((input) => group.needs.some((need) => need.front === input.dataset.needFront));
      const checked = groupInputs.filter((input) => input.checked);
      checkbox.checked = groupInputs.length > 0 && checked.length === groupInputs.length;
      checkbox.indeterminate = checked.length > 0 && checked.length < groupInputs.length;
    });
  }

  function applyRealSpeakingNeedsSearch(query) {
    const box = document.getElementById("real-needs-list");
    if (!box) return;
    const q = String(query || "").trim().toLowerCase();
    box.querySelectorAll(".speaking-need-item").forEach((item) => {
      item.hidden = Boolean(q) && !item.textContent.toLowerCase().includes(q);
    });
    box.querySelectorAll(".speaking-need-group").forEach((group, index) => {
      const items = [...group.querySelectorAll(".speaking-need-item")];
      const visible = items.some((item) => !item.hidden);
      group.hidden = !visible;
      if (q && visible) setRealCategoryExpanded(index, true);
    });
  }

  function setRealCategoryExpanded(index, expanded) {
    const box = document.getElementById("real-needs-list");
    if (!box) return;
    const button = box.querySelector('button[data-category-expand="' + index + '"]');
    const items = box.querySelector('div[data-category-items="' + index + '"]');
    if (!button || !items) return;
    items.hidden = !expanded;
    button.textContent = expanded ? "收起" : "展开";
    button.setAttribute("aria-expanded", String(expanded));
  }

  function updateRealSpeakingNeedsCount() {
    const box = document.getElementById("real-needs-list");
    const count = box
      ? [...box.querySelectorAll("input[data-need-front]:checked")].length
      : 0;
    const el = document.getElementById("real-needs-count");
    if (el) el.textContent = "已选 " + count + " 个";
  }

  function selectedRealSpeakingFronts() {
    const box = document.getElementById("real-needs-list");
    if (!box) return [];
    return [...box.querySelectorAll("input[data-need-front]:checked")]
      .map((input) => input.dataset.needFront);
  }

  const realNeedsSelectAll = document.getElementById("real-needs-select-all");
  if (realNeedsSelectAll) {
    realNeedsSelectAll.onclick = () => {
      const box = document.getElementById("real-needs-list");
      if (!box) return;
      box.querySelectorAll("input[data-need-front]:not(:disabled)").forEach((input) => {
        input.checked = true;
      });
      const categories = (realNeeds && realNeeds.categories) || [];
      categories.forEach((_group, index) => setRealCategoryExpanded(index, true));
      updateRealSpeakingNeedsCount();
      refreshRealCategoryChecks();
    };
  }
  const realNeedsClear = document.getElementById("real-needs-clear");
  if (realNeedsClear) {
    realNeedsClear.onclick = () => {
      const box = document.getElementById("real-needs-list");
      if (!box) return;
      box.querySelectorAll("input[data-need-front]:checked").forEach((input) => {
        input.checked = false;
      });
      updateRealSpeakingNeedsCount();
      refreshRealCategoryChecks();
    };
  }
  const realNeedsRandom = document.getElementById("real-needs-random");
  if (realNeedsRandom) {
    realNeedsRandom.onclick = () => {
      const box = document.getElementById("real-needs-list");
      if (!box) return;
      const status = document.getElementById("real-card-status");
      const countInput = document.getElementById("real-needs-random-count");
      const count = Math.max(1, Math.min(50, Number(countInput.value) || 10));
      const available = [...box.querySelectorAll("input[data-need-front]:not(:disabled)")];
      if (!available.length) {
        if (status) status.textContent = "口语素材里没有可选条目";
        return;
      }
      for (let index = available.length - 1; index > 0; index--) {
        const other = Math.floor(Math.random() * (index + 1));
        [available[index], available[other]] = [available[other], available[index]];
      }
      box.querySelectorAll("input[data-need-front]:checked").forEach((input) => {
        input.checked = false;
      });
      available.slice(0, Math.min(count, available.length)).forEach((input) => {
        input.checked = true;
      });
      const categories = (realNeeds && realNeeds.categories) || [];
      categories.forEach((_group, index) => setRealCategoryExpanded(index, true));
      updateRealSpeakingNeedsCount();
      refreshRealCategoryChecks();
      if (status) status.textContent = "已随机选择 " + Math.min(count, available.length) + " 个表达需求";
    };
  }

  const realCardExtract = document.getElementById("real-card-extract");
  if (realCardExtract) {
    realCardExtract.onclick = async () => {
      const source = realCardSource ? realCardSource.value : "wordlist";
      const target = document.getElementById("real-card-words");
      const status = document.getElementById("real-card-status");
      realCardExtract.disabled = true;
      if (status) status.textContent = "提取中…";
      try {
        let words = [];
        const ngslOptions = realCardNgslOptions(source);
        if (source === "corpus") {
          const text = (realCardSourceText.value || "").trim();
          if (!text) throw new Error("请先粘贴文本");
          const res = await fetch("/api/card-studio/targets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: "corpus",
              text,
              from_rank: ngslOptions.fromRank,
              to_rank: ngslOptions.toRank,
              count: 5000,
              include_unknown: ngslOptions.includeUnknown,
              ngsl_filter: ngslOptions.enabled,
              card_type: realCardType,
            }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "提取失败");
          words = (data.words || []).map((item) => item.word);
        } else if (source === "file") {
          if (!realCardFile || !realCardFile.files || !realCardFile.files[0]) {
            throw new Error("请先选择文件");
          }
          const file = realCardFile.files[0];
          const params = new URLSearchParams({
            filename: file.name,
            from_rank: String(ngslOptions.fromRank),
            to_rank: String(ngslOptions.toRank),
            count: "5000",
            include_unknown: String(ngslOptions.includeUnknown),
            card_type: realCardType,
          });
          const res = await fetch("/api/card-studio/targets-file?" + params.toString(), {
            method: "POST",
            headers: { "Content-Type": file.type || "application/octet-stream" },
            body: file,
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "提取失败");
          words = (data.words || []).map((item) => item.word);
        } else if (source === "wordlist") {
          words = (realCardSourceText.value || "")
            .split("\n").map((s) => s.trim()).filter(Boolean);
          if (!words.length) throw new Error("请先输入单词");
          words = await refineRealCardTargets(words, source);
        } else if (source === "needs") {
          words = selectedRealSpeakingFronts();
          if (!words.length) throw new Error("请先勾选表达需求");
          words = await refineRealCardExpressions(words);
        } else if (source === "topic") {
          const topic = document.getElementById("real-card-topic").value.trim();
          const count = Number(document.getElementById("real-card-topic-count").value) || 20;
          if (!topic) throw new Error("请输入主题");
          const res = await fetch("/api/words/topic", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ topic, count }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "生成失败");
          words = data.words || [];
          words = await refineRealCardTargets(words, source);
        } else if (source === "builtin") {
          const listInput = document.getElementById("real-card-list-id");
          if (!listInput || !listInput.value) throw new Error("请选择词表");
          const count = Math.min(5000, Number(document.getElementById("real-card-list-count").value) || 20);
          const randomInput = document.getElementById("real-card-list-random");
          const randomize = Boolean(randomInput && randomInput.checked);
          const res = await fetch("/api/card-studio/targets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: "builtin",
              list_id: listInput.value,
              text: "",
              from_rank: ngslOptions.fromRank,
              to_rank: ngslOptions.toRank,
              count,
              randomize,
              include_unknown: ngslOptions.includeUnknown,
              ngsl_filter: ngslOptions.enabled,
              card_type: realCardType,
            }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "提取失败");
          words = (data.words || []).map((item) => item.word);
        } else {
          const count = source === "saved"
            ? 5000
            : Math.min(5000, Number(document.getElementById("real-card-count").value) || 100);
          const randomInput = document.getElementById("real-card-random");
          const randomize = Boolean(randomInput && randomInput.checked);
          const res = await fetch("/api/card-studio/targets", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source,
              text: "",
              from_rank: ngslOptions.fromRank,
              to_rank: ngslOptions.toRank,
              count,
              randomize,
              include_unknown: ngslOptions.includeUnknown,
              ngsl_filter: ngslOptions.enabled,
              card_type: realCardType,
            }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(data.detail || "提取失败");
          words = (data.words || []).map((item) => item.word);
        }
        target.value = words.join("\n");
        if (status) status.textContent = "已提取 " + words.length + " 个目标词";
        setWizardStep(3);
      } catch (err) {
        if (status) status.textContent = err.message;
      } finally {
        realCardExtract.disabled = false;
      }
    };
  }

  const realCardGenerate = document.getElementById("real-card-generate");
  if (realCardGenerate) {
    const progressWrap = document.getElementById("real-card-progress-wrap");
    const progressFill = document.getElementById("real-card-progress-fill");
    const progressText = document.getElementById("real-card-progress-text");
    let progressTimer = null;
    let generationStartedWall = 0;

    function setCardProgress(completed, total, detail) {
      if (!progressWrap || !progressFill || !progressText) return;
      progressWrap.hidden = false;
      const percent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
      progressFill.style.width = percent + "%";
      progressText.textContent = detail
        ? "已生成 " + completed + " / " + total + " 张（" + detail + "）"
        : "已生成 " + completed + " / " + total + " 张";
    }

    async function pollCardProgress() {
      try {
        const res = await fetch("/api/card-studio/progress", {
          headers: { "Content-Type": "application/json" },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.progress) return;
        const p = data.progress;
        if (p.done) {
          // 生成结束：请求可能已被代理/隧道切断，结果靠轮询拿到。
          // 忽略本轮点击之前就完成的旧结果（请求完全没到达服务器时）。
          if (p.finished_wall && Number(p.finished_wall) < generationStartedWall) {
            return;
          }
          finishCardGeneration(p.result || {}, Number((p.result || {}).total) || 0);
          return;
        }
        setCardProgress(
          Number(p.completed) || 0,
          Number(p.total) || 0,
          p.detail || ""
        );
      } catch (_) { /* 轮询失败不打断生成 */ }
    }

    function stopCardProgressPoll() {
      if (progressTimer) {
        clearInterval(progressTimer);
        progressTimer = null;
      }
      realCardGenerate.disabled = false;
    }

    async function finishCardGeneration(result, totalWords) {
      stopCardProgressPoll();
      const resultEl = document.getElementById("real-card-result");
      if (resultEl) {
        let html =
          "已生成 " + (result.created || 0) +
          " 张，已有 " + (result.existing || 0) + " 张重复。";
        const failed = result.failed || [];
        if (failed.length) {
          html +=
            '<div class="card-generate-failed"><b>失败 ' + failed.length + " 个：</b><ul>" +
            failed.slice(0, 20).map((item) => "<li>" + escapeHtml(item) + "</li>").join("") +
            "</ul></div>";
        }
        if (result.limit_notice) {
          html = '<p class="card-limit-notice">' + escapeHtml(result.limit_notice) + "</p>" + html;
        }
        resultEl.innerHTML = '<div class="empty-state">' + html + "</div>";
      }
      setCardProgress(result.created || 0, result.processed || totalWords || 0, "完成");
      const status = document.getElementById("real-card-status");
      if (status) {
        status.textContent = result.limit_notice || (result.error ? "部分失败：" + result.error : "");
      }
      await loadRealWords();
      requestReviewRefresh();
    }

    realCardGenerate.onclick = async () => {
      const textarea = document.getElementById("real-card-words");
      const words = (textarea.value || "").split("\n").map((s) => s.trim()).filter(Boolean);
      const status = document.getElementById("real-card-status");
      if (!realCardType) {
        if (status) status.textContent = "请先选择卡片类型";
        return;
      }
      if (!words.length) {
        if (status) status.textContent = "请先输入单词";
        return;
      }
      realCardGenerate.disabled = true;
      if (status) status.textContent = "正在生成…";
      setCardProgress(0, words.length, "排队中");
      generationStartedWall = Math.floor(Date.now() / 1000);
      progressTimer = setInterval(pollCardProgress, 1500);
      let watchTimeout = null;
      try {
        const res = await fetch("/api/card-studio/cards", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ words, card_type: realCardType }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.error || "生成失败，请稍后重试");
        await finishCardGeneration(data, words.length);
      } catch (err) {
        // 请求可能被代理/隧道切断（大批量生成超过 ~100 秒），
        // 但生成仍在后台继续：进入观望模式，靠轮询进度拿最终结果，
        // 避免进度条卡死、刷新后才发现卡片已生成。
        if (status) status.textContent = "连接中断，生成仍在后台进行…";
        pollCardProgress();
        watchTimeout = setTimeout(() => {
          if (progressTimer) {
            stopCardProgressPoll();
            if (status) status.textContent = "仍未收到完成结果，请稍后刷新查看";
          }
        }, 6 * 60 * 1000);
      }
    };
  }

  /* ---------- 登录后初始化 ---------- */
  async function initLoggedIn() {
    const demoCards = document.getElementById("guest-demo-cards");
    const demoArticle = document.getElementById("guest-demo-article");
    const realReview = document.getElementById("real-review");
    const realArticle = document.getElementById("real-article-panel");
    if (demoCards) demoCards.hidden = true;
    if (demoArticle) demoArticle.hidden = true;
    if (realReview) realReview.hidden = false;
    if (realArticle) realArticle.hidden = false;
    try {
      const res = await fetch("/api/me", {
        headers: { "Content-Type": "application/json" },
      });
      if (res.ok) {
        const me = await res.json();
        const email = document.getElementById("home-email");
        if (email) email.textContent = me.email;
      }
    } catch (_) { /* 静默 */ }
    await loadRealReview();
    loadRealArticle();
    loadRealProfile();
    loadRealWords();
    loadRealBrowser();
    wireTodayOverviewButtons();
    loadTodayOverview();
  }

  /* ---------- 三种查询模式：查词 / 词源 / 问答 ---------- */
  let searchMode = "lookup";
  const SEARCH_PLACEHOLDERS = {
    lookup: "输入单词，短语或者简短中文（双击可查词）",
    etymology: "输入英文单词，查词源，如 arena",
    qa: "输入英语问题，如 lie 和 lay 的区别",
  };
  function setSearchMode(mode) {
    searchMode = mode;
    document.querySelectorAll(".search-mode-btn").forEach((btn) => {
      const active = btn.dataset.searchMode === mode;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const input = form.querySelector("input");
    if (input) {
      input.placeholder = SEARCH_PLACEHOLDERS[mode] || SEARCH_PLACEHOLDERS.lookup;
    }
  }

  /* 释义中的英文例句（"• " 开头行）逐句加朗读按钮 */
  function sentenceAudioHtml(sentence) {
    return ' <button class="demo-audio lookup-audio" data-real-audio="' +
      escapeHtml(sentence) +
      '" type="button" aria-label="朗读例句">▶</button>';
  }

  /* 收集释义中的英文例句文本（用于渲染后预生成音频，点击即播） */
  function lookupSentenceTexts(text) {
    const texts = [];
    if (!text) return texts;
    String(text)
      .split(/\r?\n/)
      .forEach(function (line) {
        const m = line.trim().match(/^[•*]\s+(.+)$/);
        if (!m) return;
        const sentence = m[1].replace(/\s+/g, " ").trim();
        if (sentence) texts.push(sentence);
      });
    return texts;
  }

  function renderLookupExplanation(text) {
    if (!text) return escapeHtml("暂无解释");
    return String(text)
      .split(/\r?\n/)
      .map(function (line) {
        const m = line.trim().match(/^[•*]\s+(.+)$/);
        if (!m) return escapeHtml(line);
        const sentence = m[1].replace(/\s+/g, " ").trim();
        if (!sentence) return escapeHtml(line);
        return escapeHtml(line) + sentenceAudioHtml(sentence);
      })
      .join("\n");
  }

  function lookupCardActionHtml(lookup) {
    if (!isLoggedIn || !lookup || !lookup.id || !["word", "phrase"].includes(lookup.query_type)) return "";
    if (lookup.has_card || lookup.card_id) {
      return '<div class="lookup-actions"><span class="lookup-state">已制成学习卡片</span></div>';
    }
    if (lookup.saved) {
      // easy/mid/hard 三态互斥；Easy 词可再次加入生词库（升级为 hard）
      if (lookup.easy) {
        const saveButton = '<button class="small" type="button" data-save-lookup-word="' +
          Number(lookup.id) + '">加入生词库</button>';
        return '<div class="lookup-actions"><span class="lookup-state easy">已标记 Easy</span>' +
          saveButton + "</div>";
      }
      return '<div class="lookup-actions"><span class="lookup-state">已在生词库</span></div>';
    }
    const saveButton = '<button class="small" type="button" data-save-lookup-word="' +
      Number(lookup.id) + '">加入生词库</button>';
    return '<div class="lookup-actions">' + saveButton + "</div>";
  }

  async function saveLookupWord(button) {
    const lookupId = Number(button.dataset.saveLookupWord);
    if (!lookupId || button.disabled) return;
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      const res = await fetch("/api/lookups/" + lookupId + "/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "加入生词库失败");
      const actions = button.closest(".lookup-actions");
      if (actions) {
        actions.innerHTML = '<span class="lookup-state">已在生词库</span>';
      } else {
        button.textContent = "已在生词库";
      }
      loadRealWords();
    } catch (err) {
      button.disabled = false;
      button.textContent = err.message || "加入生词库失败";
    }
  }

  document.addEventListener("click", (event) => {
    const saveButton = event.target.closest("[data-save-lookup-word]");
    if (saveButton) saveLookupWord(saveButton);
  });
  const searchModeGroup = document.querySelector(".search-mode");
  if (searchModeGroup) {
    searchModeGroup.addEventListener("click", (event) => {
      const btn = event.target.closest(".search-mode-btn");
      if (!btn) return;
      setSearchMode(btn.dataset.searchMode);
      const input = form.querySelector("input");
      if (input) input.focus();
    });
  }

  box.addEventListener("click", function (event) {
    const actionButton = event.target.closest("[data-vocabtool-action]");
    if (!actionButton) return;
    const action = actionButton.dataset.vocabtoolAction;
    if (action === "egg") {
      triggerLogoEasterEgg();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = form.querySelector("input");
    const raw = input.value.trim();
    if (!raw) return;
    box.hidden = false;
    box.textContent = "查询中…";
    box.className = "landing-search-result loading";
    box.setAttribute("aria-busy", "true");
    let mode = "normal";
    let text = raw;
    const quickPrefix = raw.startsWith("!") || raw.startsWith("！");
    const qaPrefix = raw.startsWith("?") || raw.startsWith("？");
    if (quickPrefix && raw.length > 1) {
      mode = "quick";
      text = raw.slice(1).trim();
    } else if (qaPrefix && raw.length > 1) {
      mode = "qa";
      text = raw.slice(1).trim();
    } else if (searchMode === "etymology") {
      mode = "quick";
      text = raw;
    } else if (searchMode === "qa") {
      mode = "qa";
      text = raw;
    }
    if (quickPrefix || qaPrefix || mode === "quick" || mode === "qa") {
      if (!text) {
        box.textContent = mode === "qa"
          ? "请在 ? 后面输入问题，如：?lie 和 lay 的区别"
          : "请在 ! 后面输入单词，如：!arena";
        box.className = "landing-search-result error";
        return;
      }
    }
    try {
      if (mode === "qa") {
        const res = await fetch("/api/lookups/question", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: text }),
        });
        const data = await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error(data.detail || "查询失败");
        box.innerHTML = '<div class="qa-rich">' + renderRichText(data.answer || "暂无回答") + "</div>" +
          (data.lookup ? lookupCardActionHtml(data.lookup) : "");
        box.className = "landing-search-result ok";
        addSearchClose();
        return;
      }
      if (mode === "quick") {
        const res = await fetch("/api/lookups/quick", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        const data = await res.json().catch(function () { return {}; });
        if (!res.ok) throw new Error(data.detail || "查询失败");
        const quickLookup = data.lookup || {};
        const displayWord =
          (data.spelling_note && data.spelling_note.corrected) || quickLookup.headword || text;
        const spellingNote = data.spelling_note && data.spelling_note.corrected
          ? '<div class="spelling-note">拼写更正：<s>' +
            escapeHtml(data.spelling_note.original || text) + "</s> → <strong>" +
            escapeHtml(data.spelling_note.corrected) +
            "</strong>，以下显示正确拼写的词源结果。</div>"
          : "";
        box.innerHTML =
          spellingNote +
          '<div class="lookup-head">' +
          '<div class="lookup-word"><mark class="word-highlight">' + escapeHtml(displayWord) + "</mark>" +
          ' <button class="demo-audio lookup-audio" data-real-audio="' + escapeHtml(displayWord) +
          '" type="button" aria-label="朗读发音">▶</button></div>' +
          (quickLookup.ngsl_rank
            ? '<div class="search-rank">NGSL 排名 #' + Number(quickLookup.ngsl_rank) + "</div>"
            : "") +
          "</div>" +
        '<div class="lookup-explanation">' +
        renderLookupExplanation(quickLookup.explanation || "暂无解释") +
        "</div>" + lookupCardActionHtml(quickLookup);
        warmUpAudioTexts([displayWord].concat(lookupSentenceTexts(quickLookup.explanation)));
        box.className = "landing-search-result ok";
        addSearchClose();
        return;
      }
      const res = await fetch("/api/lookups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok) {
        box.textContent = data.detail || "查询失败，请稍后重试";
        box.className = "landing-search-result error";
        addSearchClose();
        return;
      }
      const lookup = data.lookup || {};
      const isVocabtool = text.toLowerCase().replace(/\s+/g, " ") === "vocabtool";
      const displayWord =
        (data.spelling_note && data.spelling_note.corrected) || lookup.query || text;
      box.innerHTML =
        '<div class="lookup-head">' +
        '<div class="lookup-word"><mark class="word-highlight">' + escapeHtml(displayWord) + "</mark>" +
        ' <button class="demo-audio lookup-audio" data-real-audio="' + escapeHtml(displayWord) +
        '" type="button" aria-label="朗读发音">▶</button></div>' +
        (lookup.ngsl_rank
          ? '<div class="search-rank">NGSL 排名 #' + Number(lookup.ngsl_rank) + "</div>"
          : "") +
        "</div>" +
        '<div class="lookup-explanation">' +
        renderLookupExplanation(lookup.explanation || "暂无解释") +
        "</div>" + lookupCardActionHtml(lookup) +
        (isVocabtool
          ? '<div class="vocabtool-actions">' +
            '<button type="button" class="small" data-vocabtool-action="egg">🐕 彩蛋</button>' +
            "</div>"
          : "");
      box.className = "landing-search-result ok";
      warmUpAudioTexts([displayWord].concat(lookupSentenceTexts(lookup.explanation)));
      addSearchClose();
    } catch (err) {
      box.textContent = err.message || "网络异常，请稍后重试";
      box.className = "landing-search-result error";
      addSearchClose();
    } finally {
      box.setAttribute("aria-busy", "false");
    }
  });

  /* ---------- 清空搜索 ---------- */
  const clearButton = document.getElementById("landing-search-clear");
  const searchInput = form.querySelector("input");
  function addSearchClose() {
    if (box.querySelector(".search-result-close")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "search-result-close";
    btn.setAttribute("aria-label", "关闭搜索结果");
    btn.textContent = "×";
    btn.addEventListener("click", () => {
      box.hidden = true;
      box.innerHTML = "";
      box.className = "landing-search-result";
    });
    box.appendChild(btn);
  }
  function syncSearchClear() {
    if (clearButton) clearButton.hidden = !(searchInput && searchInput.value.trim());
  }
  if (clearButton) {
    clearButton.addEventListener("click", () => {
      searchInput.value = "";
      box.hidden = true;
      box.textContent = "";
      box.className = "landing-search-result";
      syncSearchClear();
      searchInput.focus();
    });
  }
  if (searchInput) {
    searchInput.addEventListener("input", syncSearchClear);
    syncSearchClear();
  }

  /* ---------- 左上角 logo / 名称：点击刷新 ---------- */
  document.querySelectorAll(".topbar-logo, .landing-brand").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      location.reload();
    });
  });

  /* ---------- 游客示例卡片：按钮翻面，按钮发音 ---------- */
  if (!isLoggedIn) {
    document.querySelectorAll("#guest-demo-cards .demo-card").forEach((card) => {
      card.addEventListener("click", (event) => {
        if (!event.target.closest("[data-demo-flip]")) return;
        card.classList.toggle("flipped");
      });
    });
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-audio]");
      if (!button) return;
      event.stopPropagation();
      try {
        if (!window.speechSynthesis) return;
        window.speechSynthesis.cancel();
        guestUtterance = new SpeechSynthesisUtterance(button.dataset.audio);
        const utterance = guestUtterance;
        utterance.lang = "en-US";
        utterance.rate = 0.9;
        const voices = window.speechSynthesis.getVoices();
        const english = (voice) =>
          voice.lang && voice.lang.toLowerCase().startsWith("en");
        const voice =
          voices.find(
            (v) => english(v) && /microsoft.*online.*natural/i.test(v.name)
          ) ||
          voices.find((v) => english(v) && /online.*natural/i.test(v.name)) ||
          voices.find((v) => english(v) && /google/i.test(v.name)) ||
          voices.find(english);
        if (voice) utterance.voice = voice;
        utterance.onend = () => { if (guestUtterance === utterance) guestUtterance = null; };
        utterance.onerror = () => { if (guestUtterance === utterance) guestUtterance = null; };
        window.speechSynthesis.speak(utterance);
      } catch (_) { /* 浏览器不支持语音时静默忽略 */ }
    });
    document.addEventListener("click", (event) => {
      const ratingButton = event.target.closest("[data-demo-rating]");
      if (!ratingButton) return;
      event.stopPropagation();
      const card = ratingButton.closest(".demo-card");
      if (!card) return;
      if (ratingButton.dataset.demoRating === "easy") {
        card.querySelectorAll("[data-demo-rating]").forEach((button) => {
          button.disabled = true;
        });
        card.classList.add("learned");
        card.classList.remove("flipped");
        setTimeout(() => {
          card.classList.remove("learned");
          card.querySelectorAll("[data-demo-rating]").forEach((button) => {
            button.disabled = false;
          });
        }, 900);
        return;
      }
      if (ratingButton.dataset.demoRating === "good") {
        card.querySelectorAll("[data-demo-rating]").forEach((button) => {
          button.disabled = true;
        });
        card.classList.add("learned");
        card.classList.remove("flipped");
        setTimeout(() => {
          card.classList.remove("learned");
          card.querySelectorAll("[data-demo-rating]").forEach((button) => {
            button.disabled = false;
          });
        }, 900);
        return;
      }
      card.classList.remove("flipped");
    });
    if (window.speechSynthesis) {
      window.speechSynthesis.getVoices();
      window.speechSynthesis.onvoiceschanged = () => {
        window.speechSynthesis.getVoices();
      };
    }
  }

  /* ---------- 生词库 / 学习卡片 / 制作新卡切换 ---------- */
  let realShowManagePanel = null;
  let realShowManagePanelOnly = null;
  function initManageTabs() {
    const buttons = document.querySelectorAll(".manage-button");
    const panels = document.querySelectorAll(".manage-panel");
    const container = document.querySelector(".manage-panels");
    if (!panels.length) return;
    function showPanel(id, forceOpen) {
      const alreadyActive = [...buttons].some((button) =>
        button.dataset.managePanel === id && button.classList.contains("active")
      );
      if (alreadyActive && !forceOpen) {
        // 点击已展开的菜单：收回。
        if (container) container.classList.remove("is-open");
        panels.forEach((panel) => {
          panel.hidden = true;
        });
        buttons.forEach((button) => {
          button.classList.remove("active");
          button.setAttribute("aria-selected", "false");
        });
        return;
      }
      if (container) container.classList.add("is-open");
      panels.forEach((panel) => {
        panel.hidden = panel.id !== id;
      });
      buttons.forEach((button) => {
        const active = button.dataset.managePanel === id;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
    }
    realShowManagePanelOnly = showPanel;
    realShowManagePanel = (id) => {
      showPanel(id, true);
      if (isMobileLayout()) {
        applyMobileView("cards", false);
      }
      const area = document.querySelector(".manage-area");
      if (area) area.scrollIntoView({ behavior: "smooth", block: "start" });
      if (id === "real-card-browser" && typeof loadSentenceRefreshPreference === "function") {
        loadSentenceRefreshPreference();
      }
    };
    buttons.forEach((button) => {
      button.addEventListener("click", () => showPanel(button.dataset.managePanel));
    });
  }
  initManageTabs();

  wireLogoEasterEgg();
  if (isLoggedIn) initLoggedIn();
  initMobileNav();

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      // 必须显式指定 scope：SW 默认作用域是脚本所在目录 /static/，
      // 控制不了 / 下的页面；同时依赖服务端对 /static/sw.js 返回
      // Service-Worker-Allowed: / 头（见 main.py），二者缺一离线兜底失效。
      navigator.serviceWorker
        .register("/static/sw.js", { scope: "/" })
        .catch(() => {});
    });
  }
})();
