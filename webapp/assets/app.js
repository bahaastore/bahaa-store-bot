/* بهاء ستور — التطبيق المصغّر */
(() => {
  "use strict";
  const tg = window.Telegram && window.Telegram.WebApp;
  const $ = (s, el = document) => el.querySelector(s);
  const view = $("#view");
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} },
  };
  const rawBase = window.BS_API_BASE || "";
  const API_BASE = rawBase.startsWith("__PORT_") ? "" : rawBase.replace(/\/$/, "");
  const state = { me: null, store: {}, cur: store.get("bs_cur") || "USD", path: [], methods: null };

  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor("#002623"); tg.setBackgroundColor(getComputedStyle(document.body).backgroundColor); } catch (e) {}
  }

  // ── أدوات ──────────────────────────────────────────
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (x, d = 2) => Number(x || 0).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
  const usd = (x) => (Number(x) < 1 && Number(x) > 0 ? num(x, 4) : num(x, 2)) + "$";
  const syp = (x) => num(Math.ceil(Number(x || 0) * (state.store.syp_rate || 1)), 0) + " ل.س";
  const money = (x) => (state.cur === "SYP" ? syp(x) : usd(x));
  const date = (t) => new Date(t * 1000).toLocaleString("ar-SY-u-nu-latn", { dateStyle: "medium", timeStyle: "short" });
  const haptic = (t = "light") => { try { tg && tg.HapticFeedback.impactOccurred(t); } catch (e) {} };
  const initial = (s) => esc((String(s || "?").trim()[0] || "?").toUpperCase());
  const ICONS = [
    [/لعب|ألعاب|game|pubg|free ?fire|legend|ببجي|فري/i, '<path d="M6 9h4M8 7v4M15 10h.01M18 9h.01"/><path d="M7 4h10a5 5 0 0 1 5 5v4a4 4 0 0 1-7 2.6L14 14h-4l-1 1.6A4 4 0 0 1 2 13V9a5 5 0 0 1 5-5z"/>'],
    [/دردشة|شات|chat|bigo|likee|jawaker|بيغو|لايكي|جواكر|يلا|soulchill/i, '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/>'],
    [/رصيد|تحويل|سيرياتيل|mtn|syriatel|موبايل|اتصال/i, '<rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18h2"/>'],
    [/بطاق|card|google|itunes|steam|psn|playstation|xbox|هدايا/i, '<rect x="3" y="8" width="18" height="13" rx="2"/><path d="M12 8v13M3 12h18M12 8c-2-4-6-4-6-1.5S10 8 12 8zM12 8c2-4 6-4 6-1.5S14 8 12 8z"/>'],
    [/سوشيال|social|متابع|instagram|tiktok|facebook|telegram/i, '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18.5 20a6.5 6.5 0 0 0-2.5-5.1"/>'],
  ];
  const FIELD_AR = { "player id": "معرّف اللاعب", "zone id": "رقم السيرفر", "server id": "رقم السيرفر", "phone number": "رقم الهاتف",
    "phone": "رقم الهاتف", "email": "البريد الإلكتروني", "username": "اسم المستخدم", "password": "كلمة المرور", "link": "الرابط", "url": "الرابط" };
  const fieldLabel = (f) => {
    const k = String(f).trim().toLowerCase();
    if (FIELD_AR[k]) return `${FIELD_AR[k]} <small class="sub ltr" style="display:inline">(${esc(f)})</small>`;
    const m = k.match(/^(.+) id$/);
    return m ? `معرّف ${esc(f.replace(/ id$/i, ""))} <small class="sub ltr" style="display:inline">(${esc(f)})</small>` : esc(f);
  };
  const catIcon = (name) => {
    const hit = ICONS.find(([re]) => re.test(name || ""));
    return hit ? `<svg viewBox="0 0 24 24" style="width:26px;height:26px">${hit[1]}</svg>` : initial(name);
  };

  function toast(msg, ms = 2400) {
    const t = $("#toast");
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
  }

  async function api(path, opts = {}) {
    const headers = { "X-Init-Data": (tg && tg.initData) || "", ...(opts.headers || {}) };
    if (opts.json) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
    const r = await fetch(API_BASE + path, { credentials: "same-origin", method: opts.method || (opts.body ? "POST" : "GET"), headers, body: opts.body });
    let data = null;
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((data && (typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg)) || "حدث خطأ (" + r.status + ")");
    return data;
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("تم النسخ ✓"); }
    catch (e) {
      const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); toast("تم النسخ ✓"); } catch (e2) { toast("تعذّر النسخ"); }
      ta.remove();
    }
  }
  document.addEventListener("click", (e) => {
    const c = e.target.closest("[data-copy]");
    if (c) { haptic(); copy(c.dataset.copy); }
  });

  // ── النافذة السفلية ─────────────────────────────────
  function openSheet(html) {
    $("#sheet-body").innerHTML = html;
    $("#sheet").hidden = false; $("#backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    if (tg) { tg.BackButton.show(); }
  }
  function closeSheet() {
    $("#sheet").hidden = true; $("#backdrop").hidden = true;
    document.body.style.overflow = "";
    if (tg && !state.path.length) tg.BackButton.hide();
  }
  $("#backdrop").addEventListener("click", closeSheet);
  if (tg) tg.BackButton.onClick(() => {
    if (!$("#sheet").hidden) return closeSheet();
    if (state.path.length) { state.path.pop(); renderHome(); }
  });

  // ── الرصيد والترويسة ───────────────────────────────
  function renderBalance() {
    if (!state.me) return;
    $("#bal").textContent = money(state.me.balance);
  }
  $("#bal-btn").addEventListener("click", () => {
    state.cur = state.cur === "USD" ? "SYP" : "USD";
    store.set("bs_cur", state.cur);
    haptic(); renderBalance(); route();
  });
  $("#theme-btn").addEventListener("click", () => {
    const t = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", t);
    store.set("bs_theme", t); haptic();
  });

  async function loadMe() {
    const d = await api("/api/app/me");
    state.me = d.user; state.store = d.store;
    $("#store-name").textContent = d.store.name || "بهاء ستور";
    document.title = d.store.name || "بهاء ستور";
    const n = $("#notice");
    if (d.store.maintenance) { n.textContent = "🛠 المتجر في وضع الصيانة حالياً — سنعود قريباً."; n.hidden = false; }
    else if (d.store.announcement) { n.textContent = d.store.announcement; n.hidden = false; }
    else n.hidden = true;
    $("#demo-bar").hidden = !d.store.demo;
    renderBalance();
  }

  const skeleton = (n = 6, h = 112) => `<div class="grid">${Array.from({ length: n }, () => `<div class="skel" style="height:${h}px"></div>`).join("")}</div>`;
  const errorBox = (msg) => `<div class="empty"><div class="big">!</div>${esc(msg)}<br><br><button class="btn sm ghost" onclick="location.reload()">إعادة المحاولة</button></div>`;

  // ── الرئيسية ───────────────────────────────────────
  async function renderHome() {
    const cat = state.path[state.path.length - 1];
    if (tg) state.path.length ? tg.BackButton.show() : tg.BackButton.hide();
    const head = !cat ? `
      <section class="hero">
        <div class="kicker">تنفيذ آلي على مدار الساعة</div>
        <h2>اشحن ألعابك وتطبيقاتك بضغطة واحدة</h2>
        <p>اختر القسم، أدخل البيانات المطلوبة، واستلم طلبك خلال ثوانٍ مع إشعار فوري عبر البوت.</p>
        <div class="chips"><span class="chip">شحن الألعاب</span><span class="chip">تطبيقات الدردشة</span><span class="chip">تحويل الرصيد</span></div>
      </section>
      <label class="search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input id="q" type="search" placeholder="ابحث عن منتج…" autocomplete="off" aria-label="البحث"></label>
      <div id="search-res" hidden></div>
      <h2 class="sec">الأقسام</h2>` : `
      <div class="crumbs">
        <button class="back" data-crumb="-1"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>رجوع</button>
        <span>·</span><button data-crumb="0">الرئيسية</button>
        ${state.path.map((c, i) => `<span>›</span>${i === state.path.length - 1 ? `<b>${esc(c.name)}</b>` : `<button data-crumb="${i + 1}">${esc(c.name)}</button>`}`).join("")}
      </div>
      <h1 class="title">${esc(cat.name)}</h1>`;
    view.innerHTML = head + `<div id="cat-body">${skeleton()}</div>`;

    view.querySelectorAll("[data-crumb]").forEach((b) => b.addEventListener("click", () => {
      const i = Number(b.dataset.crumb);
      state.path = i < 0 ? state.path.slice(0, -1) : state.path.slice(0, i);
      renderHome();
    }));
    const q = $("#q");
    if (q) {
      let t;
      q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => doSearch(q.value), 300); });
    }

    let data;
    try { data = await api("/api/app/content/" + (cat ? cat.id : 0)); }
    catch (e) { $("#cat-body").innerHTML = errorBox(e.message); return; }
    const body = $("#cat-body");
    if (!body) return;
    let html = "";
    if (data.categories.length) {
      html += `<div class="grid">${data.categories.map((c) => `
        <button class="cat" data-cat="${c.id}" data-name="${esc(c.name)}">
          <span class="ico">${c.image ? `<img src="${esc(c.image)}" alt="" loading="lazy">` : catIcon(c.name + " " + (state.path.map((x) => x.name).join(" ")))}</span>
          <span>${esc(c.name)}</span>
        </button>`).join("")}</div>`;
    }
    if (data.products.length) {
      if (data.categories.length) html += `<h2 class="sec">المنتجات</h2>`;
      html += productList(data.products);
    }
    if (!html) html = `<div class="empty"><div class="big">∅</div>لا توجد منتجات في هذا القسم حالياً</div>`;
    body.innerHTML = html;
    bindProducts(body, data.products);
    body.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
      haptic(); state.path.push({ id: Number(b.dataset.cat), name: b.dataset.name }); renderHome(); scrollTo(0, 0);
    }));
  }

  function productList(products) {
    return `<div class="list">${products.map((p) => `
      <button class="prod ${p.available ? "" : "off"}" data-pid="${p.id}">
        <span class="ico">${p.image ? `<img src="${esc(p.image)}" alt="" loading="lazy">` : `<svg viewBox="0 0 24 24"><path d="M21 8 12 3 3 8v8l9 5 9-5zM3 8l9 5 9-5M12 13v8"/></svg>`}</span>
        <span class="info"><span class="name">${esc(p.name)}</span>
          <span class="meta">${p.available ? (p.type === "package" ? "⚡ شحن فوري" : "السعر للوحدة") : "غير متوفر حالياً"}</span></span>
        <span class="price">${money(p.price)}</span>
      </button>`).join("")}</div>`;
  }
  function bindProducts(root, products) {
    root.querySelectorAll("[data-pid]").forEach((b) => b.addEventListener("click", () => {
      const p = products.find((x) => x.id === Number(b.dataset.pid));
      if (!p.available) return toast("هذا المنتج غير متوفر حالياً");
      haptic(); openProduct(p);
    }));
  }

  async function doSearch(q) {
    const box = $("#search-res");
    if (!box) return;
    if (!q.trim()) { box.hidden = true; return; }
    box.hidden = false; box.innerHTML = `<div class="skel" style="height:64px"></div>`;
    try {
      const d = await api("/api/app/search?q=" + encodeURIComponent(q));
      box.innerHTML = d.products.length ? productList(d.products) : `<div class="empty">لا نتائج مطابقة لـ «${esc(q)}»</div>`;
      bindProducts(box, d.products);
    } catch (e) { box.innerHTML = errorBox(e.message); }
  }

  // ── شراء منتج ───────────────────────────────────────
  function openProduct(p) {
    const isPkg = p.type === "package";
    openSheet(`
      <h3>${esc(p.name)}</h3>
      <div class="card" style="padding:12px 14px">
        <div class="row"><span>${isPkg ? "السعر" : "سعر الوحدة"}</span><b>${money(p.price)}</b></div>
        ${isPkg ? "" : `<div class="row"><span>الكمية المسموحة</span><b>${num(p.qty.min, 0)} – ${num(p.qty.max, 0)}</b></div>`}
      </div>
      ${p.description ? `<p class="sub">${esc(p.description)}</p>` : ""}
      ${p.params.map((f, i) => `
        <div class="field"><label for="f${i}">${fieldLabel(f)} *</label>
          <input id="f${i}" data-field="${esc(f)}" class="ltr" inputmode="${/phone|id|رقم|number/i.test(f) ? "numeric" : "text"}" autocomplete="off" placeholder="${esc(f)}"></div>`).join("")}
      ${isPkg ? "" : `
        <div class="field"><label for="qty">الكمية *</label>
          <div class="stepper"><button type="button" data-step="-1" aria-label="إنقاص">−</button>
          <input id="qty" type="number" inputmode="numeric" min="${p.qty.min}" max="${p.qty.max}" value="${p.qty.min}">
          <button type="button" data-step="1" aria-label="زيادة">+</button></div></div>`}
      <div class="card" style="padding:12px 14px">
        <div class="row"><span>الإجمالي</span><b id="total">${money(p.price * (isPkg ? 1 : p.qty.min))}</b></div>
        <div class="row"><span>رصيدك</span><b>${money(state.me.balance)}</b></div>
      </div>
      <button class="btn" id="buy">شراء الآن</button>`);
    const qty = $("#qty");
    const total = () => p.price * (isPkg ? 1 : Math.max(0, Number(qty.value) || 0));
    const upd = () => { $("#total").textContent = money(total()); };
    if (qty) {
      qty.addEventListener("input", upd);
      document.querySelectorAll("[data-step]").forEach((b) => b.addEventListener("click", () => {
        const step = Math.max(1, Math.round((p.qty.max - p.qty.min) / 100) || 1);
        qty.value = Math.min(p.qty.max, Math.max(p.qty.min, (Number(qty.value) || 0) + Number(b.dataset.step) * step));
        upd(); haptic();
      }));
    }
    $("#buy").addEventListener("click", async () => {
      const fields = {};
      for (const el of document.querySelectorAll("[data-field]")) {
        if (!el.value.trim()) { el.focus(); return toast("أدخل " + el.dataset.field); }
        fields[el.dataset.field] = el.value.trim();
      }
      const q = isPkg ? 1 : Number(qty.value);
      if (!isPkg && (q < p.qty.min || q > p.qty.max)) return toast(`الكمية بين ${p.qty.min} و ${p.qty.max}`);
      if (total() > state.me.balance + 1e-9) {
        toast("رصيدك غير كافٍ — اشحن رصيدك أولاً");
        return setTimeout(() => { closeSheet(); location.hash = "#topup"; }, 900);
      }
      const ok = await confirmBox(`تأكيد شراء «${p.name}» بمبلغ ${money(total())}؟`);
      if (!ok) return;
      const btn = $("#buy"); btn.disabled = true; btn.textContent = "جاري التنفيذ…";
      try {
        const o = await api("/api/app/order", { json: { product_id: p.id, qty: q, fields } });
        state.me.balance = o.balance_after; renderBalance();
        try { tg && tg.HapticFeedback.notificationOccurred("success"); } catch (e) {}
        openSheet(`
          <div class="center"><div class="done-ico"><svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg></div>
          <h3>تم إرسال طلبك ✓</h3>
          <p class="sub">طلبك قيد المعالجة — ستصلك رسالة من البوت فور اكتماله.</p></div>
          <div class="card" style="padding:12px 14px">
            <div class="row"><span>رقم الطلب</span><b>#${o.num}</b></div>
            <div class="row"><span>المنتج</span><b>${esc(o.product_name)}</b></div>
            <div class="row"><span>المبلغ</span><b>${money(o.price)}</b></div>
            <div class="row"><span>الرصيد بعد الطلب</span><b>${money(o.balance_after)}</b></div>
          </div>
          <button class="btn" id="go-orders">متابعة الطلب</button>
          <button class="btn ghost" id="close-s">متابعة التسوّق</button>`);
        $("#go-orders").onclick = () => { closeSheet(); location.hash = "#orders"; };
        $("#close-s").onclick = closeSheet;
      } catch (e) {
        try { tg && tg.HapticFeedback.notificationOccurred("error"); } catch (x) {}
        toast(e.message, 3500); btn.disabled = false; btn.textContent = "شراء الآن";
      }
    });
  }

  function confirmBox(msg) {
    return new Promise((res) => {
      if (tg && tg.showConfirm && tg.platform !== "unknown") { try { return tg.showConfirm(msg, res); } catch (e) {} }
      res(window.confirm(msg));
    });
  }

  // ── طلباتي ──────────────────────────────────────────
  const ORDER_ST = { submitting: ["⏱ قيد الإرسال", "b-wait"], wait: ["⏱ قيد التنفيذ", "b-wait"], accept: ["✓ مكتمل", "b-ok"], reject: ["✗ مرفوض", "b-bad"] };
  async function renderOrders() {
    view.innerHTML = `<h1 class="title">طلباتي</h1><div id="ob">${skeleton(4, 72).replace('class="grid"', 'class="list"')}</div>`;
    let d;
    try { d = await api("/api/app/orders"); } catch (e) { $("#ob").innerHTML = errorBox(e.message); return; }
    if (!d.orders.length) {
      $("#ob").innerHTML = `<div class="empty"><div class="big">🧾</div>لم تقم بإجراء أي طلبات بعد — استعرض منتجاتنا وابدأ التسوّق<br><br><a class="btn sm" href="#home">تصفّح المتجر</a></div>`;
      return;
    }
    $("#ob").innerHTML = `<div class="list">${d.orders.map((o) => {
      const [lbl, cls] = ORDER_ST[o.status] || [o.status, "b-wait"];
      return `<button class="prod" data-oid="${o.id}">
        <span class="info"><span class="name">${esc(o.product_name)}${o.qty > 1 ? ` × ${num(o.qty, 0)}` : ""}</span>
        <span class="meta">#${o.num} · ${date(o.created_at)}</span></span>
        <span style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
          <span class="price">${money(o.price)}</span><span class="badge ${cls}">${lbl}</span></span>
      </button>`;
    }).join("")}</div>`;
    view.querySelectorAll("[data-oid]").forEach((b) => b.addEventListener("click", () => {
      const o = d.orders.find((x) => x.id === b.dataset.oid);
      const [lbl, cls] = ORDER_ST[o.status] || [o.status, "b-wait"];
      openSheet(`
        <h3>طلب #${o.num}</h3>
        <div class="card" style="padding:12px 14px">
          <div class="row"><span>الحالة</span><span class="badge ${cls}">${lbl}</span></div>
          <div class="row"><span>المنتج</span><b>${esc(o.product_name)}</b></div>
          <div class="row"><span>الكمية</span><b>${num(o.qty, 0)}</b></div>
          <div class="row"><span>المبلغ</span><b>${money(o.price)}</b></div>
          <div class="row"><span>التاريخ</span><b>${date(o.created_at)}</b></div>
          ${Object.entries(o.params || {}).map(([k, v]) => `<div class="row"><span>${esc(k)}</span><b class="ltr">${esc(v)}</b></div>`).join("")}
        </div>
        ${o.note ? `<p class="sub">السبب: ${esc(o.note)}</p>` : ""}
        ${o.delivery && o.delivery.length ? `<div class="field"><label>📨 التسليم</label>${o.delivery.map((c) => `<button class="copy" data-copy="${esc(c)}"><code>${esc(c)}</code><small>اضغط للنسخ</small></button>`).join("")}</div>` : ""}
        ${o.status === "wait" || o.status === "submitting" ? `<p class="sub">⏳ طلبك قيد المعالجة — ستصلك رسالة من البوت فور اكتماله.</p>` : ""}
        <button class="btn ghost" data-copy="${o.num}">نسخ رقم الطلب</button>`);
    }));
  }

  // ── الشحن ───────────────────────────────────────────
  async function renderTopup() {
    view.innerHTML = `<h1 class="title">شحن الرصيد</h1><p class="sub">اختر طريقة الدفع المناسبة، حوّل المبلغ، ثم أدخل رقم العملية. يُضاف الرصيد بعد التحقق.</p><div id="tb">${skeleton(4, 72).replace('class="grid"', 'class="list"')}</div>`;
    let d;
    try { d = await api("/api/app/wallet/methods"); } catch (e) { $("#tb").innerHTML = errorBox(e.message); return; }
    state.methods = d.methods; state.store.syp_rate = d.syp_rate;
    if (!d.methods.length) { $("#tb").innerHTML = `<div class="empty">لا توجد طرق دفع متاحة حالياً</div>`; return; }
    $("#tb").innerHTML = `
      <div class="card list" style="padding:4px 4px">${d.methods.map((m) => `
        <button class="method" data-m="${esc(m.key)}"><span class="ico">${esc(m.icon || "💳")}</span>
          <span class="info"><b>${esc(m.label)}</b><br><span class="sub">${m.currency === "SYP" ? "بالليرة السورية" : "بالدولار"}</span></span>
          <svg class="chev" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg></button>`).join("")}</div>
      <p class="sub center">سعر الصرف المعتمد: 1$ = ${num(d.syp_rate, 0)} ل.س</p>`;
    view.querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () => { haptic(); openMethod(d.methods.find((m) => m.key === b.dataset.m)); }));
  }

  function openMethod(m) {
    const cur = m.currency === "SYP" ? "ل.س" : "$";
    openSheet(`
      <h3>${esc(m.icon || "")} ${esc(m.label)}</h3>
      <div class="field"><label>حوّل إلى الحساب التالي</label>
        <button class="copy" data-copy="${esc(m.account)}"><code>${esc(m.account)}</code><small>اضغط للنسخ</small></button></div>
      ${m.instructions ? `<p class="sub">${esc(m.instructions)}</p>` : ""}
      <div class="field"><label for="amt">المبلغ (${cur}) *</label>
        <input id="amt" type="number" inputmode="decimal" class="ltr" placeholder="الحد الأدنى ${num(m.min, 2)}">
        <span class="hint" id="amt-hint">${m.currency === "SYP" ? `1$ = ${num(state.store.syp_rate, 0)} ل.س` : ""}</span></div>
      <div class="field"><label for="tx">رقم العملية *</label>
        <input id="tx" class="ltr" autocomplete="off" placeholder="أدخل رقم العملية بعد التحويل"></div>
      <div class="field"><label>صورة الإيصال (اختياري)</label>
        <label class="upload" id="up-lbl"><span id="up-txt">📷 اضغط لرفع صورة الإيصال</span>
          <input id="proof" type="file" accept="image/*" hidden></label></div>
      <button class="btn" id="send-dep">إرسال للمراجعة</button>`);
    $("#amt").addEventListener("input", (e) => {
      const v = Number(e.target.value) || 0;
      $("#amt-hint").textContent = m.currency === "SYP" ? `≈ ${usd(v / state.store.syp_rate)} يُضاف لرصيدك` : "";
    });
    $("#proof").addEventListener("change", (e) => {
      const f = e.target.files[0];
      $("#up-txt").textContent = f ? "✓ " + f.name : "📷 اضغط لرفع صورة الإيصال";
      $("#up-lbl").classList.toggle("done", !!f);
    });
    $("#send-dep").addEventListener("click", async () => {
      const amount = Number($("#amt").value), tx = $("#tx").value.trim();
      if (!amount) return toast("أدخل المبلغ");
      if (amount < Number(m.min || 0)) return toast(`الحد الأدنى ${num(m.min, 2)} ${cur}`);
      if (tx.length < 4) return toast("أدخل رقم العملية");
      const fd = new FormData();
      fd.append("method", m.key); fd.append("amount", amount); fd.append("tx_id", tx);
      const f = $("#proof").files[0]; if (f) fd.append("proof", f);
      const btn = $("#send-dep"); btn.disabled = true; btn.textContent = "جاري الإرسال…";
      try {
        const r = await api("/api/app/wallet/deposit", { body: fd });
        try { tg && tg.HapticFeedback.notificationOccurred("success"); } catch (e) {}
        openSheet(`<div class="center"><div class="done-ico"><svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg></div>
          <h3>تم إرسال طلب الإيداع #${r.id}</h3>
          <p class="sub">سيتم مراجعة طلبك وإضافة الرصيد بعد التأكد من العملية. سيصلك إشعار عبر البوت.</p></div>
          <button class="btn" id="go-dep">عرض الإيداعات</button>`);
        $("#go-dep").onclick = () => { closeSheet(); location.hash = "#deposits"; };
      } catch (e) { toast(e.message, 3500); btn.disabled = false; btn.textContent = "إرسال للمراجعة"; }
    });
  }

  // ── الإيداعات ───────────────────────────────────────
  const DEP_ST = { pending: ["⏱ قيد المراجعة", "b-wait"], approved: ["✓ مقبولة", "b-ok"], rejected: ["✗ مرفوضة", "b-bad"] };
  async function renderDeposits() {
    view.innerHTML = `<h1 class="title">الإيداعات</h1><div id="db">${skeleton(3, 72).replace('class="grid"', 'class="list"')}</div>`;
    let d;
    try { d = await api("/api/app/wallet/deposits"); } catch (e) { $("#db").innerHTML = errorBox(e.message); return; }
    if (!d.deposits.length) {
      $("#db").innerHTML = `<div class="empty"><div class="big">💳</div>لا إيداعات بعد — اشحن من قسم الشحن<br><br><a class="btn sm" href="#topup">شحن الرصيد</a></div>`;
      return;
    }
    $("#db").innerHTML = `<div class="list">${d.deposits.map((x) => {
      const [lbl, cls] = DEP_ST[x.status] || [x.status, "b-wait"];
      return `<div class="prod"><span class="info"><span class="name">${esc(x.method_label)} · #${x.id}</span>
        <span class="meta">${date(x.created_at)} · العملية ${esc(x.tx_id)}</span>
        ${x.status === "rejected" && x.admin_note ? `<span class="meta" style="color:var(--bad)">السبب: ${esc(x.admin_note)}</span>` : ""}</span>
        <span style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
        <span class="price">${x.currency === "SYP" ? num(x.amount, 0) + " ل.س" : usd(x.amount)}</span>
        <span class="badge ${cls}">${lbl}</span></span></div>`;
    }).join("")}</div>`;
  }

  // ── حسابي ───────────────────────────────────────────
  function renderAccount() {
    const u = state.me;
    view.innerHTML = `
      <h1 class="title">حسابي</h1>
      <div class="card profile"><span class="avatar">${initial(u.name)}</span>
        <div><b>${esc(u.name || "مستخدم")}</b><br><span class="sub">${u.username ? "@" + esc(u.username) + " · " : ""}<span class="ltr">#${u.id}</span></span></div></div>
      <div class="stat-grid">
        <div class="stat"><small>الرصيد</small><b>${money(u.balance)}</b></div>
        <div class="stat"><small>الإنفاق الكلي</small><b>${money(u.spent)}</b></div>
      </div>
      <div class="card" style="padding:4px 16px">
        <button class="link-row" data-copy="${u.id}"><span>معرّف الحساب</span><span class="sub ltr">${u.id} · نسخ</span></button>
        <button class="link-row" id="cur-row"><span>عملة العرض</span><span class="sub">${state.cur === "SYP" ? "ليرة سورية" : "دولار"}</span></button>
        <div class="link-row"><span>سعر الصرف</span><span class="sub">1$ = ${num(state.store.syp_rate, 0)} ل.س</span></div>
        ${state.store.support_url ? `<button class="link-row" id="support"><span>الدعم الفني</span><span class="sub">تواصل معنا على مدار الساعة ‹</span></button>` : ""}
      </div>
      <p class="sub center">${esc(state.store.name)} — جميع الحقوق محفوظة</p>`;
    $("#cur-row").onclick = () => $("#bal-btn").click();
    const s = $("#support");
    if (s) s.onclick = () => {
      const url = state.store.support_url;
      if (tg && /t\.me\//.test(url)) tg.openTelegramLink(url); else if (tg) tg.openLink(url); else open(url, "_blank");
    };
  }

  // ── التوجيه ─────────────────────────────────────────
  const ROUTES = { home: renderHome, orders: renderOrders, topup: renderTopup, deposits: renderDeposits, account: renderAccount };
  function route() {
    const tab = (location.hash || "#home").slice(1);
    const fn = ROUTES[tab] || renderHome;
    document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === (ROUTES[tab] ? tab : "home")));
    closeSheet();
    if (tab !== "home" && tg) tg.BackButton.hide();
    fn();
  }
  addEventListener("hashchange", () => { scrollTo(0, 0); route(); if (state.me) loadMe().catch(() => {}); });

  (async () => {
    try { await loadMe(); route(); }
    catch (e) {
      view.innerHTML = `<div class="empty"><div class="big">!</div>${esc(e.message)}<br><br>افتح المتجر من داخل بوت تيليجرام.</div>`;
    }
  })();
})();
