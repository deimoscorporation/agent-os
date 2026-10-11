/* Deimos Agent OS — utilitários compartilhados entre as páginas. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const app = $("#app");
  const csrfToken = $('meta[name="csrf-token"]').content;
  const MOBILE = window.matchMedia("(max-width: 860px)");

  async function api(url, options = {}) {
    // Uploads (FormData) deixam o navegador definir o Content-Type com o boundary.
    const base = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
    const res = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers: { ...base, "X-CSRFToken": csrfToken, ...(options.headers || {}) },
    });
    // Sessão expirada: o servidor redireciona a chamada para a tela de login.
    if (res.redirected && new URL(res.url).pathname.startsWith("/entrar/")) {
      location.href = `/entrar/?next=${encodeURIComponent(location.pathname)}`;
      throw new Error("Sua sessão expirou. Entre novamente.");
    }
    if (!res.ok) {
      let message = `Erro ${res.status}`;
      const body = await res.text();
      try { message = JSON.parse(body).error || message; } catch (e) { if (body) message = body; }
      throw new Error(message);
    }
    return res;
  }

  function toast(message, isError = false) {
    const el = document.createElement("div");
    el.className = "toast" + (isError ? " is-error" : "");
    el.textContent = message;
    $("#toasts").appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 300);
    }, 2400);
  }

  function modal({ title, text = "", input: value = null, ok = "Confirmar", danger = false }) {
    const root = $("#modal");
    const field = $("#modal-input");
    const okBtn = $('[data-modal="ok"]', root);
    $("#modal-title").textContent = title;
    $("#modal-text").textContent = text;
    $("#modal-text").hidden = !text;
    field.hidden = value === null;
    field.value = value || "";
    okBtn.textContent = ok;
    okBtn.className = "btn " + (danger ? "btn-danger" : "btn-primary");
    root.hidden = false;
    (value === null ? okBtn : field).focus();
    if (value !== null) field.select();

    return new Promise((resolve) => {
      const close = (result) => {
        root.hidden = true;
        root.removeEventListener("click", onClick);
        root.removeEventListener("keydown", onKey);
        resolve(result);
      };
      const confirm = () => close(value === null ? true : field.value);
      const onClick = (e) => {
        if (e.target === root || e.target.closest('[data-modal="cancel"]')) close(null);
        else if (e.target.closest('[data-modal="ok"]')) confirm();
      };
      const onKey = (e) => {
        if (e.key === "Escape") close(null);
        else if (e.key === "Enter") { e.preventDefault(); confirm(); }
      };
      root.addEventListener("click", onClick);
      root.addEventListener("keydown", onKey);
    });
  }

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (err) {
      toast("Não foi possível copiar.", true);
      return;
    }
    if (!button) return toast("Copiado.");
    const use = button.querySelector("use");
    const label = button.querySelector("span");
    const previous = label && label.textContent;
    use.setAttribute("href", "#i-check");
    if (label) label.textContent = "Copiado";
    setTimeout(() => {
      use.setAttribute("href", "#i-copy");
      if (label) label.textContent = previous;
    }, 1500);
  }

  function toggleSidebar() {
    if (MOBILE.matches) app.classList.toggle("sidebar-open");
    else app.classList.toggle("sidebar-collapsed");
  }

  function closeSidebar() {
    app.classList.remove("sidebar-open");
  }

  document.addEventListener("click", (e) => {
    // Menus em <details> (seletor de workspace, "Criar") fecham ao clicar fora.
    document.querySelectorAll("details.ws-switch[open], details.menu[open]").forEach((menu) => {
      if (!menu.contains(e.target)) menu.open = false;
    });
    const target = e.target.closest("[data-action]");
    if (!target) return;
    switch (target.dataset.action) {
      case "toggle-sidebar": toggleSidebar(); break;
      case "close-sidebar": closeSidebar(); break;
    }
  });

  if (!document.documentElement.classList.contains("splash-seen")) {
    setTimeout(() => $("#splash").classList.add("is-done"), 1100);
  }

  function groupLabel(date) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const day = 86400000;
    const diff = today - new Date(date).setHours(0, 0, 0, 0);
    if (diff <= 0) return "Hoje";
    if (diff <= day) return "Ontem";
    if (diff <= 7 * day) return "Últimos 7 dias";
    if (diff <= 30 * day) return "Últimos 30 dias";
    return "Mais antigas";
  }

  window.Deimos = { $, api, toast, modal, copyText, closeSidebar, groupLabel };
})();
