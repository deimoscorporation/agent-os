/* Criador e editor de ferramentas. */
(() => {
  "use strict";

  const { $, api, toast, modal, copyText } = window.Deimos;

  const config = JSON.parse($("#editor-config").textContent);
  const mode = config.mode;

  const editorEl = $("#stage-editor");
  const paramsEl = $("#params");
  const tplParam = $("#tpl-param");
  const saveBtn = $('[data-editor="save"]');
  const publishBtn = $('[data-editor="publish"]');
  const iconPicker = $("#f-icon");
  const gutter = $("#code-gutter");
  const f = {
    name: $("#f-name"),
    slug: $("#f-slug"),
    summary: $("#f-summary"),
    description: $("#f-description"),
    category: $("#f-category"),
    tags: $("#f-tags"),
    code: $("#f-code"),
  };

  const state = {
    tool: config.item,
    icon: "i-wrench",
    report: null,
    dirty: false,
    saving: false,
    generating: false,
    validationSeq: 0,
    tab: "code",
  };

  /* ------------------------------------------------------------ Ícones */

  config.icons.forEach((icon) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "icon-option";
    b.dataset.icon = icon;
    b.setAttribute("role", "radio");
    b.setAttribute("aria-label", icon.replace("i-", ""));
    b.innerHTML = `<svg><use href="#${icon}"/></svg>`;
    iconPicker.appendChild(b);
  });

  function setIcon(icon) {
    state.icon = config.icons.includes(icon) ? icon : "i-wrench";
    iconPicker.querySelectorAll(".icon-option").forEach((b) => {
      b.setAttribute("aria-checked", String(b.dataset.icon === state.icon));
    });
    $("#ed-icon-preview use").setAttribute("href", `#${state.icon}`);
  }

  iconPicker.addEventListener("click", (e) => {
    const b = e.target.closest(".icon-option");
    if (!b) return;
    setIcon(b.dataset.icon);
    onChange();
  });

  /* -------------------------------------------------------- Parâmetros */

  function addParamRow(p = { name: "", type: "string", description: "", required: true, enum: [] }) {
    const row = tplParam.content.firstElementChild.cloneNode(true);
    const select = $(".param-type", row);
    config.types.forEach((t) => select.add(new Option(t, t)));
    $(".param-name", row).value = p.name;
    select.value = config.types.includes(p.type) ? p.type : "string";
    $(".param-required", row).checked = p.required;
    $(".param-desc", row).value = p.description || "";
    $(".param-enum", row).value = (p.enum || []).join(", ");
    syncEnumVisibility(row);
    paramsEl.appendChild(row);
    return row;
  }

  function syncEnumVisibility(row) {
    $(".param-enum", row).hidden = $(".param-type", row).value !== "string";
  }

  function schemaToParams(schema) {
    const props = (schema && schema.properties) || {};
    const required = new Set((schema && schema.required) || []);
    return Object.entries(props).map(([name, def]) => ({
      name,
      type: def.type || "string",
      description: def.description || "",
      required: required.has(name),
      enum: Array.isArray(def.enum) ? def.enum : [],
    }));
  }

  function readParams() {
    return [...paramsEl.querySelectorAll(".param")].map((row) => ({
      name: $(".param-name", row).value.trim(),
      type: $(".param-type", row).value,
      description: $(".param-desc", row).value.trim(),
      required: $(".param-required", row).checked,
      enum: $(".param-enum", row).value.split(",").map((v) => v.trim()).filter(Boolean),
    }));
  }

  function paramsToSchema() {
    const properties = {};
    const required = [];
    readParams().forEach((p) => {
      if (!p.name) return;
      const def = { type: p.type };
      if (p.description) def.description = p.description;
      if (p.type === "string" && p.enum.length) def.enum = p.enum;
      properties[p.name] = def;
      if (p.required) required.push(p.name);
    });
    return { type: "object", properties, required };
  }

  paramsEl.addEventListener("click", (e) => {
    const remove = e.target.closest(".param-remove");
    if (!remove) return;
    remove.closest(".param").remove();
    onChange();
  });

  paramsEl.addEventListener("change", (e) => {
    if (e.target.classList.contains("param-type")) syncEnumVisibility(e.target.closest(".param"));
  });

  /* ------------------------------------------------------------- Spec */

  function collectSpec() {
    return {
      name: f.name.value.trim(),
      slug: f.slug.value.trim(),
      summary: f.summary.value.trim(),
      description: f.description.value.trim(),
      icon: state.icon,
      category: f.category.value,
      tags: f.tags.value.split(",").map((t) => t.trim()).filter(Boolean),
      input_schema: paramsToSchema(),
      code: f.code.value,
    };
  }

  function fill(spec) {
    f.name.value = spec.name || "";
    f.slug.value = spec.slug || "";
    f.summary.value = spec.summary || "";
    f.description.value = spec.description || "";
    f.category.value = spec.category || "other";
    f.tags.value = (spec.tags || []).join(", ");
    f.code.value = spec.code || "";
    setIcon(spec.icon);
    paramsEl.innerHTML = "";
    schemaToParams(spec.input_schema).forEach((p) => addParamRow(p));
    refresh();
  }

  function definitionFor(spec) {
    return { name: spec.slug, description: spec.description, input_schema: spec.input_schema };
  }

  function highlightJson(el, value) {
    const text = JSON.stringify(value, null, 2);
    el.innerHTML = window.hljs ? hljs.highlight(text, { language: "json" }).value : "";
    if (!window.hljs) el.textContent = text;
  }

  function refresh() {
    const spec = collectSpec();
    $("#ed-title").textContent = spec.name || "Sem nome";

    const version = state.tool ? `v${state.tool.version}` : "v1.0.0";
    let status = "Rascunho";
    if (state.tool) status = state.tool.status;
    if (state.dirty && mode === "edit") status += " · alterações não salvas";
    $("#ed-subtitle").textContent = `${spec.slug || "sem_identificador"} · ${version} · ${status}`;

    highlightJson($("#v-schema"), spec.input_schema);
    highlightJson($("#v-definition"), definitionFor(spec));
    updateGutter();
  }

  function onChange() {
    state.dirty = true;
    refresh();
    scheduleValidate();
  }

  editorEl.addEventListener("input", (e) => {
    if (e.target.matches(".input, textarea, select, input")) onChange();
  });
  editorEl.addEventListener("change", (e) => {
    if (e.target.matches("select, input[type=checkbox]")) onChange();
  });

  f.slug.addEventListener("blur", () => {
    const normalized = f.slug.value
      .normalize("NFKD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
    if (normalized !== f.slug.value) {
      f.slug.value = normalized;
      onChange();
    }
  });

  /* ------------------------------------------------------ Editor de código */

  function updateGutter() {
    const lines = f.code.value.split("\n").length;
    gutter.textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n");
    gutter.scrollTop = f.code.scrollTop;
  }

  function insertText(text) {
    // execCommand preserva o histórico de desfazer do textarea.
    if (!document.execCommand("insertText", false, text)) {
      f.code.setRangeText(text, f.code.selectionStart, f.code.selectionEnd, "end");
      f.code.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }

  f.code.addEventListener("scroll", () => { gutter.scrollTop = f.code.scrollTop; });
  f.code.addEventListener("keydown", (e) => {
    if (e.key === "Tab" && !e.shiftKey && !e.ctrlKey) {
      e.preventDefault();
      insertText("    ");
    } else if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) {
      const { value, selectionStart } = f.code;
      const line = value.slice(value.lastIndexOf("\n", selectionStart - 1) + 1, selectionStart);
      let indent = line.match(/^[ \t]*/)[0];
      if (/:\s*$/.test(line)) indent += "    ";
      e.preventDefault();
      insertText("\n" + indent);
    }
  });

  /* -------------------------------------------------------------- Abas */

  document.querySelector(".code-tabs").addEventListener("click", (e) => {
    const tab = e.target.closest("[data-tab]");
    if (!tab) return;
    state.tab = tab.dataset.tab;
    document.querySelectorAll(".code-tab").forEach((t) => t.classList.toggle("is-active", t === tab));
    document.querySelectorAll(".tab-panel").forEach((p) => { p.hidden = p.dataset.panel !== state.tab; });
  });

  function activeTabText() {
    const spec = collectSpec();
    if (state.tab === "schema") return JSON.stringify(spec.input_schema, null, 2);
    if (state.tab === "definition") return JSON.stringify(definitionFor(spec), null, 2);
    return spec.code;
  }

  /* --------------------------------------------------------- Validação */

  let validateTimer = 0;

  function scheduleValidate() {
    clearTimeout(validateTimer);
    setAuditPending();
    validateTimer = setTimeout(validate, 400);
  }

  function setAuditPending() {
    const status = $("#audit-status");
    status.className = "audit-status";
    status.innerHTML = '<span class="spinner"></span>Validando…';
    state.report = null;
    updateSaveState();
  }

  async function validate() {
    const seq = ++state.validationSeq;
    try {
      const res = await api("/api/items/validate/", {
        method: "POST",
        body: JSON.stringify({ kind: "tool", spec: collectSpec(), item_id: state.tool ? state.tool.id : null }),
      });
      const report = await res.json();
      if (seq === state.validationSeq) renderReport(report);
    } catch (err) {
      if (seq !== state.validationSeq) return;
      const status = $("#audit-status");
      status.className = "audit-status is-error";
      status.textContent = "Não foi possível validar agora.";
    }
  }

  function renderReport(report) {
    state.report = report;
    const status = $("#audit-status");
    const list = $("#audit-list");
    const perms = $("#audit-perms");
    const { errors, warnings, permissions } = report;

    if (errors.length) {
      status.className = "audit-status is-error";
      status.innerHTML = '<svg><use href="#i-x"/></svg>';
      status.append(`${errors.length} erro${errors.length > 1 ? "s" : ""} a corrigir`);
    } else {
      status.className = "audit-status " + (warnings.length ? "is-warn" : "is-ok");
      status.innerHTML = '<svg><use href="#i-check"/></svg>';
      status.append(warnings.length
        ? `Válida, com ${warnings.length} aviso${warnings.length > 1 ? "s" : ""}`
        : mode === "create" ? "Pronta para instalar" : "Válida");
    }

    list.innerHTML = "";
    [...errors.map((m) => ["is-error", "i-x", m]), ...warnings.map((m) => ["is-warn", "i-alert", m])].forEach(([cls, icon, msg]) => {
      const li = document.createElement("li");
      li.className = `audit-item ${cls}`;
      li.innerHTML = `<svg><use href="#${icon}"/></svg><span></span>`;
      li.lastChild.textContent = msg;
      list.appendChild(li);
    });

    perms.innerHTML = "";
    if (!permissions.length) {
      perms.innerHTML = '<p class="ok-line"><svg><use href="#i-shield"/></svg>Nenhuma — roda isolada.</p>';
    }
    permissions.forEach((p) => {
      const chip = document.createElement("span");
      chip.className = "perm";
      chip.innerHTML = '<svg><use href="#i-alert"/></svg>';
      chip.append((report.permission_labels || {})[p] || p);
      perms.appendChild(chip);
    });

    const secrets = $("#audit-secrets");
    secrets.innerHTML = "";
    if (!(report.secrets_status || []).length) {
      secrets.innerHTML = '<p class="muted audit-none">Esta ferramenta não usa segredos.</p>';
    }
    (report.secrets_status || []).forEach((s) => {
      const chip = document.createElement("span");
      chip.className = s.configured ? "perm perm--ok" : "perm";
      chip.title = s.configured ? "Configurado no cofre deste workspace" : "Ainda não está no cofre deste workspace";
      chip.innerHTML = `<svg><use href="#${s.configured ? "i-lock" : "i-alert"}"/></svg>`;
      chip.append(s.name + (s.configured ? "" : " — faltando"));
      secrets.appendChild(chip);
    });

    updateSaveState();
  }

  function updateSaveState() {
    saveBtn.disabled =
      state.saving || !state.report || state.report.errors.length > 0 || (mode === "edit" && !state.dirty);
  }

  /* ------------------------------------------------------- Persistência */

  async function save() {
    if (saveBtn.disabled) return;
    let changelog = "";
    if (mode === "edit") {
      changelog = await modal({ title: "Salvar nova versão", text: "O que mudou? (opcional)", input: "", ok: "Salvar" });
      if (changelog === null) return;
    }
    state.saving = true;
    updateSaveState();
    const spec = collectSpec();
    try {
      if (mode === "create") {
        const res = await api("/api/items/", { method: "POST", body: JSON.stringify({ kind: "tool", spec }) });
        const data = await res.json();
        state.dirty = false;
        try { sessionStorage.setItem("deimos-item-created", data.item.name); } catch (e) {}
        location.href = data.url;
        return;
      }
      const res = await api(`/api/items/${state.tool.id}/`, { method: "PUT", body: JSON.stringify({ spec, changelog }) });
      const data = await res.json();
      state.tool = data.item;
      state.dirty = false;
      renderReport(data.report);
      refresh();
      toast(`Salvo — versão ${data.item.version}.`);
    } catch (err) {
      toast(err.message, true);
      validate();
    } finally {
      state.saving = false;
      updateSaveState();
    }
  }

  function updatePublishButton() {
    if (!publishBtn) return;
    const isPublic = state.tool.visibility === "public";
    publishBtn.querySelector("use").setAttribute("href", isPublic ? "#i-eye" : "#i-upload");
    publishBtn.querySelector("span").textContent = isPublic ? "Despublicar" : "Publicar";
  }

  async function togglePublish() {
    const makePublic = state.tool.visibility !== "public";
    if (makePublic && state.dirty) return toast("Salve as alterações antes de publicar.", true);
    const ok = await modal(makePublic
      ? { title: "Publicar no marketplace?", text: "Qualquer usuário poderá ver o código desta ferramenta e instalá-la.", ok: "Publicar" }
      : { title: "Despublicar?", text: "A ferramenta sai do marketplace. Quem já instalou continua com ela.", ok: "Despublicar" });
    if (!ok) return;
    try {
      const res = await api(`/api/items/${state.tool.id}/publish/`, { method: "POST", body: JSON.stringify({ public: makePublic }) });
      state.tool.visibility = (await res.json()).visibility;
      updatePublishButton();
      refresh();
      toast(makePublic ? "Publicada no marketplace." : "Removida do marketplace.");
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function deleteTool() {
    const ok = await modal({
      title: "Excluir ferramenta?",
      text: `“${state.tool.name}” será excluída permanentemente, inclusive do marketplace se estiver publicada.`,
      ok: "Excluir",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/items/${state.tool.id}/`, { method: "DELETE" });
      state.dirty = false;
      location.href = "/workspace/";
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* ------------------------------------------------------------ Geração */

  function setupGenerator() {
    const form = $("#describe-form");
    const input = $("#describe-input");
    const genBtn = $("#generate-btn");
    const stepsEl = $("#steps");

    const syncButton = () => { genBtn.disabled = state.generating || input.value.trim().length < 10; };
    input.addEventListener("input", syncButton);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if (!genBtn.disabled) generate();
      }
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (!genBtn.disabled) generate();
    });
    document.querySelector(".examples").addEventListener("click", (e) => {
      const chip = e.target.closest("[data-example]");
      if (!chip || state.generating) return;
      input.value = chip.dataset.example;
      syncButton();
      generate();
    });

    function renderStep(ev) {
      let li = stepsEl.querySelector(`[data-step="${ev.id}"]`);
      if (!li) {
        li = document.createElement("li");
        li.dataset.step = ev.id;
        li.innerHTML = '<span class="step-icon"><span class="spinner"></span><svg><use href="#i-check"/></svg></span><span class="step-label"></span>';
        $(".step-label", li).textContent = ev.label;
        stepsEl.appendChild(li);
      }
      li.className = `step is-${ev.status}`;
    }

    async function generate() {
      if (state.dirty && !editorEl.hidden) {
        const ok = await modal({ title: "Gerar novamente?", text: "As alterações no rascunho atual serão perdidas.", ok: "Gerar" });
        if (!ok) return;
      }
      state.generating = true;
      syncButton();
      genBtn.querySelector("span").textContent = "Gerando…";
      stepsEl.innerHTML = "";
      stepsEl.hidden = false;
      editorEl.hidden = true;

      try {
        const res = await api("/api/tools/generate/", {
          method: "POST",
          body: JSON.stringify({ description: input.value.trim() }),
        });
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop();
          lines.filter(Boolean).forEach((line) => handleEvent(JSON.parse(line)));
        }
      } catch (err) {
        stepsEl.querySelectorAll(".step.is-running").forEach((li) => { li.className = "step is-failed"; });
        toast(err.message, true);
      } finally {
        state.generating = false;
        genBtn.querySelector("span").textContent = "Gerar ferramenta";
        syncButton();
      }
    }

    function handleEvent(ev) {
      if (ev.type === "step") {
        renderStep(ev);
      } else if (ev.type === "draft") {
        fill(ev.draft);
        state.dirty = false;
        renderReport(ev.report);
        editorEl.hidden = false;
        requestAnimationFrame(() => editorEl.scrollIntoView({ behavior: "smooth", block: "start" }));
      } else if (ev.type === "notice") {
        toast(ev.message, true);
      } else if (ev.type === "error") {
        throw new Error(ev.message);
      }
    }

    return {
      reset() {
        editorEl.hidden = true;
        stepsEl.hidden = true;
        state.dirty = false;
        input.focus();
        $("#tool-page").scrollTo({ top: 0, behavior: "smooth" });
      },
    };
  }

  /* ------------------------------------------------------------ Ações */

  const generator = mode === "create" ? setupGenerator() : null;

  editorEl.addEventListener("click", async (e) => {
    const action = e.target.closest("[data-editor]");
    if (!action) return;
    switch (action.dataset.editor) {
      case "add-param":
        $(".param-name", addParamRow({ name: "", type: "string", description: "", required: true, enum: [] })).focus();
        onChange();
        break;
      case "save": save(); break;
      case "publish": togglePublish(); break;
      case "delete": deleteTool(); break;
      case "copy-tab": copyText(activeTabText(), action); break;
      case "discard":
        if (await modal({ title: "Descartar rascunho?", text: "A ferramenta gerada não será instalada.", ok: "Descartar", danger: true })) {
          generator.reset();
        }
        break;
    }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !editorEl.hidden) {
      e.preventDefault();
      save();
    }
  });

  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  /* -------------------------------------------------------------- Boot */

  window.DeimosEditor = { isDirty: () => state.dirty };

  if (mode === "edit") {
    fill(state.tool);
    updatePublishButton();
    validate();
  } else {
    $("#describe-input").focus();
  }

  try {
    const created = sessionStorage.getItem("deimos-item-created");
    if (created) {
      sessionStorage.removeItem("deimos-item-created");
      toast(`“${created}” instalada no seu workspace.`);
    }
  } catch (e) {}
})();
