/* @ds-bundle: {"format":4,"namespace":"SP","components":[{"name":"Button"},{"name":"Field"},{"name":"Check"},{"name":"Card"},{"name":"Badge"},{"name":"Chip"},{"name":"Tabs"},{"name":"Nav"},{"name":"Alert"},{"name":"Toast"},{"name":"Dialog"},{"name":"Table"},{"name":"Pagination"},{"name":"Progress"},{"name":"EmptyState"},{"name":"Header"},{"name":"ThemeToggle"},{"name":"Layout"}]} */
/* SoftPanel UI (ui.js) — без зависимостей. window.SP.
   Два способа: 1) пишете HTML с классами sp-* и зовёте SP.init(); 2) создаёте элементы фабриками SP.Button({...}). */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;
  var THEME_KEY = "softpanel:theme";
  var ICONS = {
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    alert: '<path d="M12 3 2 20h20L12 3Z"/><path d="M12 10v4M12 17h.01"/>'
  };

  function icon(name) {
    var s = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    s.setAttribute("fill", "none");
    s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "1.8");
    s.setAttribute("stroke-linecap", "round");
    s.setAttribute("stroke-linejoin", "round");
    s.innerHTML = ICONS[name] || "";
    return s;
  }

  /* h("button", {class:"sp-btn", onclick: fn}, "Текст") — маленький помощник создания DOM. Текст всегда как textContent. */
  function h(tag, props) {
    var el = doc.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === "class") el.className = Array.isArray(v) ? v.filter(Boolean).join(" ") : v;
      else if (k === "style" && typeof v === "object") Object.keys(v).forEach(function (p) { el.style.setProperty(p, v[p]); });
      else if (k.slice(0, 2) === "on" && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k in el && typeof v !== "string") el[k] = v;
      else el.setAttribute(k, v === true ? "" : v);
    });
    for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
    return el;
  }
  function append(el, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) return c.forEach(function (x) { append(el, x); });
    el.appendChild(c.nodeType ? c : doc.createTextNode(String(c)));
  }
  function mod(base, list) { return [base].concat((list || []).filter(Boolean).map(function (m) { return base + "--" + m; })); }

  /* ---------- Тема ---------- */
  var theme = {
    get: function () { return root.dataset.theme === "dark" ? "dark" : "light"; },
    set: function (t) {
      t = t === "dark" ? "dark" : "light";
      root.dataset.theme = t;
      root.style.colorScheme = t;
      try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* без хранилища тема живёт до перезагрузки */ }
      window.dispatchEvent(new CustomEvent("softpanel:themechange", { detail: { theme: t } }));
      return t;
    },
    toggle: function () { return theme.set(theme.get() === "dark" ? "light" : "dark"); }
  };
  if (!root.dataset.theme) {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { saved = null; }
    var sys = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    root.dataset.theme = saved === "dark" || saved === "light" ? saved : sys;
  }

  function ThemeToggle() {
    var b = h("button", { class: "sp-theme-toggle", type: "button", role: "switch" },
      h("span", null, icon("sun")), h("span", null, icon("moon")));
    function sync() {
      b.setAttribute("aria-checked", String(theme.get() === "dark"));
      b.setAttribute("aria-label", "Тёмная тема");
    }
    b.addEventListener("click", function () { theme.toggle(); });
    window.addEventListener("softpanel:themechange", sync);
    sync();
    return b;
  }

  /* ---------- Фабрики ---------- */
  var ICON_LABELS = { sun: "Светлая тема", moon: "Тёмная тема", close: "Закрыть", info: "Подробнее", check: "Готово", alert: "Внимание" };
  function Button(o) {
    o = o || {};
    if (o.icon && !o.text && !o.label && window.console) console.warn("SP.Button: кнопке-иконке нужен label");
    var link = !!o.href;
    var el = h(link ? "a" : "button", {
      class: mod("sp-btn", [o.variant, o.size, o.icon && !o.text ? "icon" : null, o.block ? "block" : null]),
      href: o.disabled && link ? null : o.href, type: link ? null : (o.type || "button"),
      disabled: link ? null : o.disabled, "aria-disabled": link && o.disabled ? "true" : null,
      "aria-label": o.label || (!o.text ? ICON_LABELS[o.icon] : null), onclick: o.onClick
    }, o.icon ? icon(o.icon) : null, o.text);
    return el;
  }
  function busy(btn, on) { btn.setAttribute("aria-busy", on ? "true" : "false"); btn.disabled = !!on; }

  var uid = 0;
  function Field(o) {
    o = o || {};
    var id = o.id || "sp-f" + (++uid), hintId = id + "-hint";
    var control;
    if (o.type === "select") {
      control = h("select", { class: "sp-select", id: id, name: o.name, required: o.required },
        (o.options || []).map(function (op) {
          op = typeof op === "string" ? { value: op, label: op } : op;
          return h("option", { value: op.value, selected: op.value === o.value }, op.label);
        }));
    } else if (o.type === "textarea") {
      control = h("textarea", { class: "sp-textarea", id: id, name: o.name, placeholder: o.placeholder, required: o.required });
      if (o.value) control.value = o.value;
    } else {
      control = h("input", { class: "sp-input", id: id, name: o.name, type: o.type || "text", placeholder: o.placeholder, required: o.required, autocomplete: o.autocomplete, inputmode: o.inputmode });
      if (o.value != null) control.value = o.value;
    }
    var msg = h("p", { class: o.error ? "sp-error" : "sp-hint", id: hintId, hidden: !(o.error || o.hint) }, o.error || o.hint);
    if (o.error || o.hint) control.setAttribute("aria-describedby", hintId);
    if (o.error) control.setAttribute("aria-invalid", "true");
    var wrap = h("div", { class: ["sp-field", o.span ? "sp-span" : null] }, h("label", { class: "sp-label", for: id }, o.label), control, msg);
    wrap.control = control;
    wrap.setError = function (text) {
      msg.className = text ? "sp-error" : "sp-hint";
      msg.textContent = text || o.hint || "";
      msg.hidden = !msg.textContent;
      if (text) control.setAttribute("aria-invalid", "true"); else control.removeAttribute("aria-invalid");
      if (msg.textContent) control.setAttribute("aria-describedby", hintId);
    };
    return wrap;
  }

  function Check(o) {
    o = o || {};
    var input = h("input", { type: o.type === "radio" ? "radio" : "checkbox", name: o.name, value: o.value, checked: o.checked });
    if (o.type === "switch") {
      input.setAttribute("role", "switch");
      return h("label", { class: "sp-switch" }, input, h("span", null, o.label));
    }
    return h("label", { class: "sp-check" }, input, h("span", null, o.label, o.hint ? h("span", { class: "sp-hint", style: { display: "block" } }, o.hint) : null));
  }

  function Card(o) {
    o = o || {};
    return h(o.href ? "a" : "article", { class: mod("sp-card", [o.variant, o.href ? "link" : null]), href: o.href },
      o.eyebrow ? h("p", { class: "eyebrow" }, o.eyebrow) : null,
      o.title ? h("h4", null, o.title) : null,
      o.text ? h("p", { class: "sp-muted" }, o.text) : null,
      o.children || null,
      o.actions ? h("div", { class: "sp-card__footer" }, o.actions) : null);
  }

  function Badge(text, variant, dot) {
    return h("span", { class: mod("sp-badge", [variant, dot || variant === "live" ? "dot" : null]) }, text);
  }

  function Chip(o) {
    o = o || {};
    var c = h("button", { class: "sp-chip", type: "button", "aria-pressed": String(!!o.pressed) },
      o.text, o.count != null ? h("span", { class: "sp-chip__count" }, o.count) : null);
    c.addEventListener("click", function () {
      var on = c.getAttribute("aria-pressed") !== "true";
      c.setAttribute("aria-pressed", String(on));
      if (o.onChange) o.onChange(on);
    });
    return c;
  }

  function Alert(o) {
    o = o || {};
    var kind = o.kind || "info";
    var ic = { info: "info", success: "check", warning: "alert", danger: "alert", orange: "info" }[kind];
    return h("div", { class: mod("sp-alert", [kind === "info" ? null : kind]), role: kind === "danger" ? "alert" : "status" },
      icon(ic), h("div", null, o.title ? h("strong", null, o.title) : null, o.text));
  }

  function toastHost() {
    return doc.querySelector(".sp-toasts") || doc.body.appendChild(h("div", { class: "sp-toasts", role: "status", "aria-live": "polite" }));
  }
  function toast(text, o) {
    o = o || {};
    var host = toastHost();
    var t = h("div", { class: mod("sp-toast", [o.kind]) }, h("span", null, text));
    var close = function () { t.remove(); };
    if (o.action) {
      t.appendChild(h("button", { class: "sp-btn sp-btn--sm sp-btn--soft", type: "button", onclick: function () { o.action.onClick(); close(); } }, o.action.text));
      t.appendChild(h("button", { class: "sp-btn sp-btn--sm sp-btn--text", type: "button", "aria-label": "Закрыть", onclick: close }, icon("close")));
    }
    /* живая область уже в DOM — добавляем сообщение в следующем кадре, чтобы чтец его объявил */
    requestAnimationFrame(function () { host.appendChild(t); });
    /* тост с действием не исчезает сам (WCAG 2.2.1), если не задан timeout */
    if (!o.action || o.timeout) setTimeout(close, o.timeout || 5000);
    return close;
  }

  function EmptyState(o) {
    o = o || {};
    return h("div", { class: "sp-empty" }, h("h4", null, o.title), o.text ? h("p", null, o.text) : null, o.action || null);
  }

  function Progress(value, variant) {
    var bar = h("div", { class: mod("sp-progress", [variant]), role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" }, h("i"));
    bar.set = function (v) {
      if (v == null) { bar.style.removeProperty("--value"); bar.removeAttribute("aria-valuenow"); return; }
      v = Math.max(0, Math.min(100, Math.round(v)));
      bar.style.setProperty("--value", v + "%");
      bar.setAttribute("aria-valuenow", String(v));
    };
    bar.set(value);
    return bar;
  }

  var proc = null;
  var processing = {
    show: function (o) {
      o = o || {};
      if (!proc) {
        proc = { bar: Progress(null), label: h("p", { class: "sp-muted" }), title: h("h3", null) };
        proc.title.id = "sp-proc-title";
        proc.el = h("div", { class: "sp-processing", role: "alertdialog", "aria-modal": "true", "aria-labelledby": "sp-proc-title", "aria-live": "polite" },
          h("div", { class: "sp-processing__card", tabindex: "-1" }, proc.title, proc.bar, proc.label));
        doc.body.appendChild(proc.el);
      }
      if (proc.el.hidden !== false || !proc.shown) {
        proc.shown = true;
        proc.back = doc.activeElement;
        Array.prototype.forEach.call(doc.body.children, function (c) { if (c !== proc.el && !c.inert) { c.inert = true; c.dataset.spInert = "1"; } });
      }
      proc.title.textContent = o.title || "Обрабатываем…";
      proc.label.textContent = o.label || "";
      proc.bar.set(o.value);
      proc.el.hidden = false;
      proc.el.firstChild.focus();
    },
    hide: function () {
      if (!proc || !proc.shown) return;
      proc.shown = false;
      proc.el.hidden = true;
      Array.prototype.forEach.call(doc.querySelectorAll("[data-sp-inert]"), function (c) { c.inert = false; delete c.dataset.spInert; });
      if (proc.back && proc.back.focus) proc.back.focus();
    }
  };

  /* ---------- Поведение ---------- */
  function Menu(trigger, panel) {
    function open(on) {
      panel.hidden = !on;
      trigger.setAttribute("aria-expanded", String(on));
      if (on) { var f = panel.querySelector("a,button"); if (f) f.focus(); }
    }
    if (!panel.id) panel.id = "sp-menu-" + (++uid);
    trigger.setAttribute("aria-controls", panel.id);
    open(false);
    trigger.addEventListener("click", function (e) { e.stopPropagation(); open(panel.hidden); });
    panel.addEventListener("click", function (e) { if (e.target.closest("a,button")) open(false); });
    panel.addEventListener("focusout", function (e) { if (e.relatedTarget && !panel.contains(e.relatedTarget) && e.relatedTarget !== trigger) open(false); });
    doc.addEventListener("pointerdown", function (e) { if (!panel.hidden && !panel.contains(e.target) && !trigger.contains(e.target)) open(false); });
    doc.addEventListener("keydown", function (e) { if (e.key === "Escape" && !panel.hidden) { open(false); trigger.focus(); } });
    return { open: function () { open(true); }, close: function () { open(false); } };
  }

  function Dialog(dialog, triggers) {
    (triggers || []).forEach(function (t) { t.addEventListener("click", function () { dialog.showModal(); }); });
    var downOnBackdrop = false;
    dialog.addEventListener("pointerdown", function (e) { downOnBackdrop = e.target === dialog; });
    dialog.addEventListener("click", function (e) {
      if (e.target.closest("[data-sp-close]")) dialog.close();
      else if (e.target === dialog && downOnBackdrop) dialog.close();
      downOnBackdrop = false;
    });
    return dialog;
  }

  function Tabs(list) {
    var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
    list.setAttribute("role", "tablist");
    tabs.forEach(function (t) {
      if (!t.id) t.id = "sp-tab-" + (++uid);
      var p = t.getAttribute("aria-controls") && doc.getElementById(t.getAttribute("aria-controls"));
      if (p) { p.setAttribute("role", "tabpanel"); p.setAttribute("aria-labelledby", t.id); }
    });
    function select(tab, focus, silent) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        var p = t.getAttribute("aria-controls") && doc.getElementById(t.getAttribute("aria-controls"));
        if (p) p.hidden = !on;
      });
      if (focus) tab.focus();
      if (!silent) list.dispatchEvent(new CustomEvent("sp:change", { detail: { tab: tab } }));
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t); });
      t.addEventListener("keydown", function (e) {
        var n = tabs.length, j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: n - 1 }[e.key];
        if (j != null) { e.preventDefault(); select(tabs[(j + n) % n], true); }
      });
    });
    select(tabs.filter(function (t) { return t.getAttribute("aria-selected") === "true"; })[0] || tabs[0], false, true);
    return { select: select };
  }

  /* SP.init(): подключает поведение к разметке по data-атрибутам. Повторный вызов безопасен. */
  function init(scope) {
    scope = scope || doc;
    function each(sel, fn) {
      Array.prototype.forEach.call(scope.querySelectorAll(sel), function (el) {
        if (el.dataset.spBound) return;
        el.dataset.spBound = "1";
        fn(el);
      });
    }
    each("[data-sp-menu]", function (t) { var p = doc.getElementById(t.dataset.spMenu); if (p) Menu(t, p); });
    each("[data-sp-dialog]", function (t) {
      var d = doc.getElementById(t.dataset.spDialog);
      if (!d) return;
      if (!d.dataset.spBound) { d.dataset.spBound = "1"; Dialog(d); }
      t.addEventListener("click", function () { d.showModal(); });
    });
    each("[data-sp-tabs]", Tabs);
    each("[data-sp-theme-toggle]", function (slot) { slot.replaceWith(ThemeToggle()); });
  }
  function boot() { init(); toastHost(); }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot);
  else boot();

  window.SP = {
    version: "1.0.0",
    h: h, icon: icon, init: init, theme: theme, busy: busy, toast: toast, processing: processing,
    Button: Button, Field: Field, Check: Check, Card: Card, Badge: Badge, Chip: Chip, Alert: Alert,
    Toast: toast, EmptyState: EmptyState, Progress: Progress, ThemeToggle: ThemeToggle,
    Menu: Menu, Dialog: Dialog, Tabs: Tabs
  };
})();
