/* Lista de conversas na barra lateral das páginas fora do chat (no chat, quem cuida é chat/js/app.js). */
(() => {
  "use strict";

  const { $, api, toast, modal, groupLabel } = window.Deimos;

  const listEl = $("#conversations");
  const searchEl = $("#search");
  let conversations = [];

  async function load() {
    try {
      const res = await api("/api/conversations/");
      conversations = (await res.json()).conversations;
      render();
    } catch (err) {
      console.error(err);
    }
  }

  function render() {
    const q = searchEl.value.trim().toLowerCase();
    const list = conversations.filter((c) => !q || c.title.toLowerCase().includes(q));
    listEl.innerHTML = "";

    if (!list.length) {
      const p = document.createElement("p");
      p.className = "conversations-empty";
      p.textContent = q ? "Nenhum resultado." : "Nenhuma conversa ainda.";
      listEl.appendChild(p);
      return;
    }

    let group = null;
    let current = "";
    list.forEach((c) => {
      const label = groupLabel(c.updated_at);
      if (label !== current) {
        current = label;
        group = document.createElement("div");
        group.className = "conv-group";
        group.innerHTML = `<div class="conv-group-label"></div>`;
        group.firstChild.textContent = label;
        listEl.appendChild(group);
      }
      const item = document.createElement("div");
      item.className = "conv-item";
      item.dataset.id = c.id;
      item.innerHTML =
        `<a class="conv-link"></a>` +
        `<div class="conv-actions">` +
        `<button data-action="rename" title="Renomear"><svg><use href="#i-pencil"/></svg></button>` +
        `<button data-action="delete" title="Excluir"><svg><use href="#i-trash"/></svg></button>` +
        `</div>`;
      const link = $(".conv-link", item);
      link.href = `/c/${c.id}/`;
      link.textContent = c.title;
      link.title = c.title;
      group.appendChild(item);
    });
  }

  async function rename(conv) {
    const title = await modal({ title: "Renomear conversa", input: conv.title, ok: "Salvar" });
    if (!title || !title.trim() || title.trim() === conv.title) return;
    try {
      await api(`/api/conversations/${conv.id}/`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) });
      await load();
      toast("Conversa renomeada.");
    } catch (err) {
      toast("Não foi possível renomear.", true);
    }
  }

  async function remove(conv) {
    const ok = await modal({
      title: "Excluir conversa?",
      text: `“${conv.title}” será excluída permanentemente.`,
      ok: "Excluir",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/conversations/${conv.id}/`, { method: "DELETE" });
      await load();
      toast("Conversa excluída.");
    } catch (err) {
      toast("Não foi possível excluir.", true);
    }
  }

  $("#sidebar").addEventListener("click", (e) => {
    const target = e.target.closest("[data-action]");
    if (!target) return;
    if (target.dataset.action === "new-chat") {
      location.href = "/";
      return;
    }
    const item = target.closest(".conv-item");
    const conv = item && conversations.find((c) => c.id === item.dataset.id);
    if (!conv) return;
    if (target.dataset.action === "rename") rename(conv);
    else if (target.dataset.action === "delete") remove(conv);
  });

  searchEl.addEventListener("input", render);

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "o") {
      e.preventDefault();
      location.href = "/";
    }
  });

  load();
})();
