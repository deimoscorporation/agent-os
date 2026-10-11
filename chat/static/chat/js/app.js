(() => {
  "use strict";

  const { $, api, toast, modal, copyText, closeSidebar, groupLabel } = window.Deimos;

  const app = $("#app");
  const thread = $("#thread");
  const messagesEl = $("#messages");
  const conversationsEl = $("#conversations");
  const input = $("#input");
  const form = $("#composer");
  const sendBtn = $("#send-btn");
  const titleEl = $("#chat-title");
  const searchEl = $("#search");
  const scrollDownBtn = $("#scroll-down");
  const tplMessage = $("#tpl-message");
  const agentSelect = $("#agent-select");
  const modelSelect = $("#model-select");
  const knowledgeToggle = $("#knowledge-toggle");
  const shareMenu = $("#share-menu");
  const attachmentsEl = $("#attachments");
  const fileInput = $("#file-input");
  const dropHint = $("#drop-hint");

  const DEFAULT_TITLE = "Nova conversa";
  const MAX_ATTACHMENTS = 5;
  const MAX_BYTES = 10 * 1024 * 1024;

  const state = {
    conversationId: app.dataset.initialConversation || null,
    conversations: [],
    agents: [],
    controller: null,
    stickToBottom: true,
    useKnowledge: false,
    // Anexos da próxima mensagem: {key, name, kind, size, id?, url?, uploading, preview?}
    attachments: [],
  };

  const currentConversation = () => state.conversations.find((c) => c.id === state.conversationId);

  /* ----------------------------------------------------------- Markdown */

  marked.use({ gfm: true, breaks: true });

  function renderMarkdown(text) {
    const html = DOMPurify.sanitize(marked.parse(text));
    const tmp = document.createElement("div");
    tmp.innerHTML = html;

    tmp.querySelectorAll("pre > code").forEach((code) => {
      const lang = ([...code.classList].find((c) => c.startsWith("language-")) || "").slice(9);
      if (lang && hljs.getLanguage(lang)) {
        code.innerHTML = hljs.highlight(code.textContent, { language: lang }).value;
      } else {
        code.innerHTML = hljs.highlightAuto(code.textContent).value;
      }
      code.classList.add("hljs");

      const block = document.createElement("div");
      block.className = "code-block";
      block.innerHTML =
        `<div class="code-head"><span>${lang || "código"}</span>` +
        `<button class="code-copy" data-action="copy-code"><svg><use href="#i-copy"/></svg><span>Copiar</span></button></div>`;
      const pre = code.parentElement;
      pre.replaceWith(block);
      block.appendChild(pre);
    });

    tmp.querySelectorAll("a").forEach((a) => {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    });
    return tmp.innerHTML;
  }

  /* ----------------------------------------------------------- Messages */

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
  }

  function attachmentChip(a) {
    const el = document.createElement(a.url ? "a" : "span");
    if (a.kind === "image" && (a.url || a.preview)) {
      el.className = "attachment attachment--image";
      const img = document.createElement("img");
      img.src = a.preview || a.url;
      img.alt = a.name;
      img.loading = "lazy";
      el.appendChild(img);
    } else {
      el.className = "attachment";
      el.innerHTML = '<svg><use href="#i-file"/></svg><span class="attachment-text"><strong></strong><small></small></span>';
      $("strong", el).textContent = a.name;
      $("small", el).textContent = formatSize(a.size);
    }
    if (a.url) {
      el.href = a.url;
      el.target = "_blank";
      el.rel = "noopener";
    }
    el.title = a.name;
    return el;
  }

  function renderAttachments(node, attachments) {
    const box = $(".msg-attachments", node);
    box.innerHTML = "";
    box.hidden = !attachments || !attachments.length;
    (attachments || []).forEach((a) => box.appendChild(attachmentChip(a)));
  }

  function renderSources(node, sources) {
    const box = $(".msg-sources", node);
    box.innerHTML = "";
    box.hidden = !sources || !sources.length;
    if (box.hidden) return;
    const label = document.createElement("span");
    label.className = "sources-label";
    label.innerHTML = '<svg><use href="#i-book"/></svg>Fontes';
    box.appendChild(label);
    // Um chip por documento, com o trecho usado como dica.
    const byDocument = new Map();
    sources.forEach((s) => { if (!byDocument.has(s.document_id)) byDocument.set(s.document_id, s); });
    byDocument.forEach((s) => {
      const chip = document.createElement("a");
      chip.className = "source";
      chip.href = `/conhecimento/${s.document_id}/arquivo/`;
      chip.title = s.excerpt;
      chip.textContent = s.document;
      box.appendChild(chip);
    });
  }

  function createMessage(role, content = "", extra = {}) {
    const node = tplMessage.content.firstElementChild.cloneNode(true);
    node.classList.add(`msg--${role}`);
    node.dataset.raw = content;
    const body = $(".msg-content", node);
    if (role === "user") {
      body.textContent = content;
      body.hidden = !content;
    } else {
      body.classList.add("prose");
      body.innerHTML = renderMarkdown(content);
    }
    renderAttachments(node, extra.attachments);
    renderSources(node, extra.sources);
    messagesEl.appendChild(node);
    app.classList.add("has-messages");
    markLast();
    return node;
  }

  function markLast() {
    messagesEl.querySelectorAll(".msg.is-last").forEach((m) => m.classList.remove("is-last"));
    const last = messagesEl.lastElementChild;
    if (last && last.classList.contains("msg--assistant")) last.classList.add("is-last");
  }

  function renderMessages(list) {
    messagesEl.innerHTML = "";
    list.forEach((m) => createMessage(m.role, m.content, m));
    app.classList.toggle("has-messages", list.length > 0);
    scrollToBottom(false);
  }

  function setTyping(node) {
    $(".msg-content", node).innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
  }

  function showError(node, message) {
    node.classList.remove("is-streaming");
    $(".msg-content", node).innerHTML = "";
    const err = document.createElement("div");
    err.className = "msg-error";
    err.textContent = message;
    $(".msg-content", node).appendChild(err);
  }

  /* ---------------------------------------------------------- Streaming */

  async function streamInto(node, request) {
    setStreaming(true);
    node.classList.add("is-streaming");
    setTyping(node);
    state.stickToBottom = true;
    scrollToBottom();

    const controller = new AbortController();
    state.controller = controller;
    let text = "";
    let frame = 0;
    const paint = () => {
      frame = 0;
      $(".msg-content", node).innerHTML = renderMarkdown(text);
      if (state.stickToBottom) scrollToBottom(false);
    };

    try {
      const res = await request(controller.signal);
      const newId = res.headers.get("X-Conversation-Id");
      if (newId && newId !== state.conversationId) {
        state.conversationId = newId;
        history.pushState({ id: newId }, "", `/c/${newId}/`);
        loadConversations();
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        text += decoder.decode(value, { stream: true });
        if (!frame) frame = requestAnimationFrame(paint);
      }
      cancelAnimationFrame(frame);
      paint();
    } catch (err) {
      cancelAnimationFrame(frame);
      if (err.name === "AbortError") {
        if (text) paint();
        else showError(node, "Geração interrompida.");
      } else {
        showError(node, err.message && err.message.length < 120 ? err.message : "Não foi possível obter a resposta. Tente novamente.");
        console.error(err);
      }
    } finally {
      node.dataset.raw = text.trim();
      node.classList.remove("is-streaming");
      state.controller = null;
      setStreaming(false);
      loadConversations();
      syncSavedMessage(node);
    }
  }

  // Depois do streaming, busca o que só existe no servidor: fontes da resposta e links dos anexos.
  async function syncSavedMessage(replyNode) {
    if (!state.conversationId) return;
    try {
      const res = await api(`/api/conversations/${state.conversationId}/`);
      const saved = (await res.json()).messages;
      const nodes = [...messagesEl.children];
      const reply = saved[saved.length - 1];
      if (reply && reply.role === "assistant" && nodes[nodes.length - 1] === replyNode) {
        renderSources(replyNode, reply.sources);
        const question = saved[saved.length - 2];
        const questionNode = nodes[nodes.length - 2];
        if (question && question.attachments.length && questionNode) renderAttachments(questionNode, question.attachments);
      }
    } catch (err) {
      console.error(err);
    }
  }

  function sendMessage(text) {
    text = text.trim();
    const attachments = state.attachments;
    if (state.controller) return;
    if (attachments.some((a) => a.uploading)) return toast("Aguarde o envio dos anexos terminar.");
    if (!text && !attachments.length) return;

    createMessage("user", text, { attachments });
    const reply = createMessage("assistant");
    input.value = "";
    state.attachments = [];
    renderTray();
    autoresize();

    const isNew = !state.conversationId;
    const body = JSON.stringify({
      message: text,
      conversation_id: state.conversationId,
      attachment_ids: attachments.map((a) => a.id),
      agent_id: isNew ? agentSelect.value || null : null,
      model: isNew ? modelSelect.value : null,
      use_knowledge: isNew ? state.useKnowledge : null,
    });
    streamInto(reply, (signal) => api("/api/chat/", { method: "POST", body, signal }));
  }

  function regenerate() {
    if (!state.conversationId || state.controller) return;
    const last = messagesEl.lastElementChild;
    if (last && last.classList.contains("msg--assistant")) last.remove();
    const reply = createMessage("assistant");
    const id = state.conversationId;
    streamInto(reply, (signal) => api(`/api/conversations/${id}/regenerate/`, { method: "POST", signal }));
  }

  function setStreaming(on) {
    app.classList.toggle("is-streaming", on);
    sendBtn.setAttribute("aria-label", on ? "Parar geração" : "Enviar");
    updateSendState();
  }

  /* -------------------------------------------------------------- Anexos */

  function renderTray() {
    attachmentsEl.innerHTML = "";
    attachmentsEl.hidden = !state.attachments.length;
    state.attachments.forEach((a) => {
      const item = document.createElement("div");
      item.className = "tray-item" + (a.uploading ? " is-uploading" : "");
      item.appendChild(attachmentChip({ ...a, url: null }));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "tray-remove";
      remove.title = "Remover anexo";
      remove.innerHTML = '<svg><use href="#i-x"/></svg>';
      remove.addEventListener("click", () => removeAttachment(a));
      item.appendChild(remove);
      attachmentsEl.appendChild(item);
    });
    updateSendState();
  }

  function removeAttachment(a) {
    state.attachments = state.attachments.filter((x) => x !== a);
    if (a.preview) URL.revokeObjectURL(a.preview);
    if (a.id) api(`/attachments/${a.id}/`, { method: "DELETE" }).catch(() => {});
    renderTray();
  }

  async function addFiles(files) {
    for (const file of files) {
      if (state.attachments.length >= MAX_ATTACHMENTS) {
        toast(`No máximo ${MAX_ATTACHMENTS} anexos por mensagem.`, true);
        break;
      }
      if (file.size > MAX_BYTES) {
        toast(`“${file.name}” passa de 10 MB.`, true);
        continue;
      }
      const isImage = file.type.startsWith("image/");
      const entry = {
        key: crypto.randomUUID(),
        name: file.name || "imagem-colada.png",
        size: file.size,
        kind: isImage ? "image" : "file",
        preview: isImage ? URL.createObjectURL(file) : null,
        uploading: true,
      };
      state.attachments.push(entry);
      renderTray();

      const data = new FormData();
      data.append("file", file, entry.name);
      try {
        const res = await api("/api/attachments/", { method: "POST", body: data });
        Object.assign(entry, await res.json(), { uploading: false, preview: entry.preview });
      } catch (err) {
        toast(`${entry.name}: ${err.message}`, true);
        state.attachments = state.attachments.filter((x) => x !== entry);
      }
      renderTray();
    }
  }

  $("#attach-btn").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    addFiles([...fileInput.files]);
    fileInput.value = "";
  });

  input.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData ? e.clipboardData.files : [])];
    if (!files.length) return;
    e.preventDefault();
    addFiles(files);
  });

  let dragDepth = 0;
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { dragDepth++; dropHint.hidden = false; } });
  window.addEventListener("dragleave", (e) => { if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; dropHint.hidden = true; } });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    dropHint.hidden = true;
    addFiles([...e.dataTransfer.files]);
  });

  /* ------------------------------------------------------ Conversations */

  async function loadConversations() {
    try {
      const res = await api("/api/conversations/");
      state.conversations = (await res.json()).conversations;
      renderConversations();
      syncHeader();
    } catch (err) {
      console.error(err);
    }
  }

  function renderConversations() {
    const q = searchEl.value.trim().toLowerCase();
    const list = state.conversations.filter((c) => !q || c.title.toLowerCase().includes(q));
    conversationsEl.innerHTML = "";

    if (!list.length) {
      const p = document.createElement("p");
      p.className = "conversations-empty";
      p.textContent = q ? "Nenhum resultado." : "Nenhuma conversa ainda.";
      conversationsEl.appendChild(p);
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
        conversationsEl.appendChild(group);
      }
      const item = document.createElement("div");
      item.className = "conv-item" + (c.id === state.conversationId ? " is-active" : "");
      item.dataset.id = c.id;
      item.innerHTML =
        `<button class="conv-link" data-action="open"></button>` +
        `<div class="conv-actions">` +
        `<button data-action="rename" title="Renomear"><svg><use href="#i-pencil"/></svg></button>` +
        `<button data-action="delete" title="Excluir"><svg><use href="#i-trash"/></svg></button>` +
        `</div>`;
      const link = $(".conv-link", item);
      link.textContent = c.title;
      link.title = c.title;
      group.appendChild(item);
    });
  }

  function syncHeader() {
    const current = currentConversation();
    const title = current ? current.title : DEFAULT_TITLE;
    titleEl.textContent = title;
    document.title = current ? `${title} · Deimos Agent OS` : "Deimos Agent OS";
    $("#btn-rename").hidden = !current;
    $("#btn-delete").hidden = !current;
    shareMenu.hidden = !current;
    if (current) {
      $("#export-md").href = `/c/${current.id}/export.md`;
      $("#export-json").href = `/c/${current.id}/export.json`;
      state.useKnowledge = current.use_knowledge;
      syncShare(current.shared ? `${location.origin}/s/…` : null, current.shared);
    }
    knowledgeToggle.setAttribute("aria-pressed", String(state.useKnowledge));
    syncAgent(current);
    syncModel(current);
  }

  /* ---------------------------------------------------- Agente e modelo */

  // O agente é escolhido ao iniciar a conversa e fica fixo nela.
  function syncAgent(current) {
    agentSelect.disabled = Boolean(current);
    if (!current) return;
    const agent = current.agent;
    if (agent && !agentSelect.querySelector(`option[value="${agent.id}"]`)) {
      // Agente removido do workspace depois da conversa: mantém o nome visível.
      agentSelect.add(new Option(agent.name, agent.id));
    }
    agentSelect.value = agent ? agent.id : "";
  }

  // Com agente, vale o modelo definido nele; sem agente, o usuário escolhe (e pode trocar no meio).
  function syncModel(current) {
    const agent = state.agents.find((a) => a.id === agentSelect.value);
    const forced = agentSelect.value ? (agent && agent.model) || "" : "";
    modelSelect.disabled = Boolean(agentSelect.value);
    modelSelect.parentElement.title = agentSelect.value ? "Modelo definido pelo agente" : "Modelo de IA";
    const wanted = forced || (current && current.model) || modelSelect.dataset.chosen || modelSelect.dataset.default;
    if (wanted && !modelSelect.querySelector(`option[value="${wanted}"]`)) modelSelect.add(new Option(wanted, wanted));
    if (wanted) modelSelect.value = wanted;
  }

  agentSelect.addEventListener("change", () => syncModel(currentConversation()));

  modelSelect.addEventListener("change", async () => {
    modelSelect.dataset.chosen = modelSelect.value;
    const current = currentConversation();
    if (!current) return;
    try {
      await api(`/api/conversations/${current.id}/`, { method: "PATCH", body: JSON.stringify({ model: modelSelect.value }) });
      current.model = modelSelect.value;
      toast("Modelo alterado para as próximas respostas.");
    } catch (err) {
      toast("Não foi possível trocar o modelo.", true);
      syncModel(current);
    }
  });

  async function loadAgents() {
    try {
      const res = await api("/api/workspace/agents/");
      state.agents = (await res.json()).agents;
      state.agents.forEach((a) => {
        if (!agentSelect.querySelector(`option[value="${a.id}"]`)) agentSelect.add(new Option(a.name, a.id));
      });
      const wanted = new URLSearchParams(location.search).get("agente");
      if (wanted && !state.conversationId && agentSelect.querySelector(`option[value="${wanted}"]`)) {
        agentSelect.value = wanted;
      }
      syncModel(currentConversation());
    } catch (err) {
      console.error(err);
    }
  }

  /* -------------------------------------------------- Base de conhecimento */

  knowledgeToggle.addEventListener("click", async () => {
    const next = !state.useKnowledge;
    if (next && knowledgeToggle.dataset.hasDocuments !== "true") {
      const go = await modal({
        title: "A base de conhecimento está vazia",
        text: "Envie documentos (PDF, DOCX, TXT…) para o assistente consultar nas respostas.",
        ok: "Adicionar documentos",
      });
      if (go) location.href = "/conhecimento/";
      return;
    }
    const current = currentConversation();
    if (current) {
      try {
        await api(`/api/conversations/${current.id}/`, { method: "PATCH", body: JSON.stringify({ use_knowledge: next }) });
        current.use_knowledge = next;
      } catch (err) {
        return toast("Não foi possível alterar.", true);
      }
    }
    state.useKnowledge = next;
    knowledgeToggle.setAttribute("aria-pressed", String(next));
    toast(next ? "As respostas vão consultar a base de conhecimento." : "Base de conhecimento desativada nesta conversa.");
  });

  /* ------------------------------------------------- Compartilhar */

  function syncShare(url, shared) {
    $("#share-link").hidden = !shared;
    $("#share-create").hidden = shared;
    $("#share-revoke").hidden = !shared;
    if (url) $("#share-url").value = url;
    $("#share-hint").textContent = shared
      ? "Link ativo: quem tiver o endereço lê a conversa (inclusive mensagens futuras), sem precisar de conta. Anexos não são incluídos."
      : "Qualquer pessoa com o link lê a conversa, sem precisar de conta. Anexos não são incluídos.";
  }

  async function setShare(method) {
    const current = currentConversation();
    if (!current) return null;
    try {
      const res = await api(`/api/conversations/${current.id}/share/`, { method });
      const data = await res.json();
      current.shared = data.shared;
      syncShare(data.url, data.shared);
      return data;
    } catch (err) {
      toast("Não foi possível alterar o compartilhamento.", true);
      return null;
    }
  }

  // Ao abrir o menu de uma conversa já compartilhada, busca o endereço completo.
  shareMenu.addEventListener("toggle", () => {
    const current = currentConversation();
    if (shareMenu.open && current && current.shared) setShare("POST");
  });

  /* ------------------------------------------------------ Abrir / criar */

  async function openConversation(id, push = true) {
    if (state.controller) state.controller.abort();
    try {
      const res = await api(`/api/conversations/${id}/`);
      const data = await res.json();
      state.conversationId = id;
      if (push) history.pushState({ id }, "", `/c/${id}/`);
      // A lista pode ainda não ter chegado: garante os dados desta conversa para o cabeçalho.
      const index = state.conversations.findIndex((c) => c.id === id);
      if (index >= 0) state.conversations[index] = data.conversation;
      else state.conversations.push(data.conversation);
      renderMessages(data.messages);
      renderConversations();
      syncHeader();
      closeSidebar();
      input.focus();
    } catch (err) {
      toast("Conversa não encontrada.", true);
      newChat();
    }
  }

  function newChat(push = true) {
    if (state.controller) state.controller.abort();
    state.conversationId = null;
    state.useKnowledge = false;
    if (push && location.pathname !== "/") history.pushState({}, "", "/");
    messagesEl.innerHTML = "";
    app.classList.remove("has-messages");
    shareMenu.open = false;
    renderConversations();
    syncHeader();
    closeSidebar();
    input.focus();
  }

  async function renameConversation(id) {
    const conv = state.conversations.find((c) => c.id === id);
    if (!conv) return;
    const title = await modal({ title: "Renomear conversa", input: conv.title, ok: "Salvar" });
    if (!title || !title.trim() || title.trim() === conv.title) return;
    try {
      await api(`/api/conversations/${id}/`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) });
      await loadConversations();
      toast("Conversa renomeada.");
    } catch (err) {
      toast("Não foi possível renomear.", true);
    }
  }

  async function deleteConversation(id) {
    const conv = state.conversations.find((c) => c.id === id);
    if (!conv) return;
    const ok = await modal({
      title: "Excluir conversa?",
      text: `“${conv.title}” será excluída permanentemente, com seus anexos${conv.shared ? " e o link público" : ""}.`,
      ok: "Excluir",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/conversations/${id}/`, { method: "DELETE" });
      if (id === state.conversationId) newChat();
      await loadConversations();
      toast("Conversa excluída.");
    } catch (err) {
      toast("Não foi possível excluir.", true);
    }
  }

  /* ------------------------------------------------------- UI helpers */

  function scrollToBottom(smooth = true) {
    thread.scrollTo({ top: thread.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }

  thread.addEventListener("scroll", () => {
    const distance = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    state.stickToBottom = distance < 80;
    scrollDownBtn.hidden = state.stickToBottom || !app.classList.contains("has-messages");
  });

  function autoresize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 220) + "px";
    updateSendState();
  }

  function updateSendState() {
    const ready = state.attachments.length > 0 && !state.attachments.some((a) => a.uploading);
    sendBtn.disabled = !state.controller && !input.value.trim() && !ready;
  }

  /* ----------------------------------------------------------- Typewriter */

  function typewriter() {
    const el = $("#typewriter");
    const phrases = [
      "Advanced AI & CyberSecurity",
      "Agentes inteligentes sob medida",
      "Segurança por padrão",
      "Pergunte qualquer coisa",
    ];
    let p = 0;
    let i = phrases[0].length;
    let deleting = true;
    const tick = () => {
      const phrase = phrases[p];
      el.textContent = phrase.slice(0, i);
      let delay = deleting ? 28 : 55;
      if (deleting) {
        if (--i < 0) { deleting = false; p = (p + 1) % phrases.length; i = 0; delay = 300; }
      } else if (++i > phrases[p].length) {
        deleting = true; i = phrases[p].length; delay = 2600;
      }
      setTimeout(tick, delay);
    };
    setTimeout(tick, 3200);
  }

  /* -------------------------------------------------------------- Events */

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.controller) state.controller.abort();
    else sendMessage(input.value);
  });

  input.addEventListener("input", autoresize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!state.controller) sendMessage(input.value);
    }
  });

  searchEl.addEventListener("input", renderConversations);

  document.addEventListener("click", async (e) => {
    const target = e.target.closest("[data-action], [data-prompt]");
    if (!target) return;

    if (target.dataset.prompt) {
      sendMessage(target.dataset.prompt);
      return;
    }

    const item = target.closest(".conv-item");
    const id = item && item.dataset.id;

    switch (target.dataset.action) {
      case "new-chat": e.preventDefault(); newChat(); break;
      case "open": if (id !== state.conversationId) openConversation(id); else closeSidebar(); break;
      case "rename": renameConversation(id || state.conversationId); break;
      case "delete": deleteConversation(id); break;
      case "delete-current": deleteConversation(state.conversationId); break;
      case "regenerate": regenerate(); break;
      case "scroll-bottom": state.stickToBottom = true; scrollToBottom(); break;
      case "copy-message": copyText(target.closest(".msg").dataset.raw || "", target); break;
      case "copy-code": copyText(target.closest(".code-block").querySelector("code").textContent, target); break;
      case "share-create": {
        const data = await setShare("POST");
        if (data && data.url) copyText(data.url);
        break;
      }
      case "share-copy": copyText($("#share-url").value, target); break;
      case "share-revoke":
        if (await modal({ title: "Desativar o link?", text: "Quem tiver o endereço deixa de conseguir abrir a conversa.", ok: "Desativar", danger: true })) {
          if (await setShare("DELETE")) toast("Link desativado.");
        }
        break;
    }
  });

  titleEl.addEventListener("dblclick", () => state.conversationId && renameConversation(state.conversationId));

  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.shiftKey && e.key.toLowerCase() === "o") {
      e.preventDefault();
      newChat();
    } else if (e.key === "Escape" && state.controller) {
      state.controller.abort();
    } else if (e.key === "/" && document.activeElement === document.body) {
      e.preventDefault();
      input.focus();
    }
  });

  window.addEventListener("popstate", () => {
    const match = location.pathname.match(/^\/c\/([0-9a-f-]{36})\/?$/);
    if (match) openConversation(match[1], false);
    else newChat(false);
  });

  /* ---------------------------------------------------------------- Boot */

  typewriter();
  loadAgents();
  loadConversations();
  if (state.conversationId) openConversation(state.conversationId, false);
  autoresize();
})();
