/* Editor de agentes, skills e workflows (ferramentas usam tool-editor.js). */
(() => {
  "use strict";

  const { $, api, toast, modal } = window.Deimos;

  const config = JSON.parse($("#editor-config").textContent);
  const { mode, kind } = config;
  const REF_LABELS = { tool: "Ferramentas", agent: "Agentes", skill: "Skills" };
  const KIND_NAMES = { agent: "Agente", skill: "Skill", workflow: "Workflow" };

  const editorEl = $("#stage-editor");
  const saveBtn = $('[data-editor="save"]');
  const publishBtn = $('[data-editor="publish"]');
  const iconPicker = $("#f-icon");
  const f = {
    name: $("#f-name"),
    slug: $("#f-slug"),
    summary: $("#f-summary"),
    description: $("#f-description"),
    category: $("#f-category"),
    tags: $("#f-tags"),
  };

  const state = {
    item: mode === "edit" ? config.item : null,
    icon: config.item.icon,
    report: null,
    dirty: false,
    saving: false,
    slugTouched: mode === "edit",
    validationSeq: 0,
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
    state.icon = config.icons.includes(icon) ? icon : config.icons[0];
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

  /* ------------------------------------------------ Campos por tipo */

  const kinds = {
    agent: {
      fill(spec) {
        $("#f-model").value = spec.model;
        $("#f-system-prompt").value = spec.system_prompt || "";
        const list = $("#f-tools");
        const selected = new Set(spec.tools || []);
        const available = config.refs.tool;
        // Ferramentas referenciadas que saíram do workspace continuam visíveis para serem desmarcadas.
        const missing = [...selected].filter((s) => !available.some((t) => t.slug === s));
        list.innerHTML = "";
        if (!available.length && !missing.length) {
          list.innerHTML = '<p class="muted">Nenhuma ferramenta no workspace. <a href="/workspace/new/tool/">Crie uma</a> ou <a href="/marketplace/?kind=tool">instale do marketplace</a>.</p>';
        }
        [...available, ...missing.map((slug) => ({ slug, name: "Não instalada", missing: true }))].forEach((tool) => {
          const label = document.createElement("label");
          label.className = "pick" + (tool.missing ? " is-missing" : "");
          label.innerHTML = '<input type="checkbox"><span class="pick-text"><strong></strong><small class="mono"></small></span>';
          const input = $("input", label);
          input.value = tool.slug;
          input.checked = selected.has(tool.slug);
          $("strong", label).textContent = tool.name;
          $("small", label).textContent = tool.slug;
          list.appendChild(label);
        });
        this.count();
      },
      collect() {
        return {
          model: $("#f-model").value,
          system_prompt: $("#f-system-prompt").value,
          tools: [...document.querySelectorAll("#f-tools input:checked")].map((i) => i.value),
        };
      },
      count() {
        const n = document.querySelectorAll("#f-tools input:checked").length;
        $("#tools-count").textContent = n ? `${n} selecionada${n > 1 ? "s" : ""}` : "";
      },
    },

    skill: {
      fill(spec) { $("#f-instructions").value = spec.instructions || ""; },
      collect() { return { instructions: $("#f-instructions").value }; },
    },

    workflow: {
      fill(spec) {
        $("#f-trigger").value = spec.trigger || "";
        $("#f-steps").innerHTML = "";
        (spec.steps || []).forEach((s) => this.addStep(s));
      },
      collect() {
        return {
          trigger: $("#f-trigger").value,
          steps: [...document.querySelectorAll(".step-row")].map((row) => ({
            name: $(".step-name", row).value,
            uses: $(".step-uses", row).value,
          })),
        };
      },
      addStep(step = { name: "", uses: "" }) {
        const row = $("#tpl-step").content.firstElementChild.cloneNode(true);
        const select = $(".step-uses", row);
        select.add(new Option("Escolha o que executar…", ""));
        let found = !step.uses;
        Object.keys(REF_LABELS).forEach((refKind) => {
          const items = config.refs[refKind];
          if (!items.length) return;
          const group = document.createElement("optgroup");
          group.label = REF_LABELS[refKind];
          items.forEach((it) => {
            const value = `${refKind}:${it.slug}`;
            group.appendChild(new Option(`${it.name} (${it.slug})`, value));
            if (value === step.uses) found = true;
          });
          select.appendChild(group);
        });
        if (!found) select.add(new Option(`${step.uses} — não instalado`, step.uses));
        $(".step-name", row).value = step.name || "";
        select.value = step.uses || "";
        $("#f-steps").appendChild(row);
        this.renumber();
        return row;
      },
      renumber() {
        document.querySelectorAll(".step-row").forEach((row, i) => { $(".step-num", row).textContent = i + 1; });
      },
    },
  };

  const kindUi = kinds[kind];

  if (kind === "workflow") {
    $("#f-steps").addEventListener("click", (e) => {
      const button = e.target.closest("[data-step]");
      if (!button) return;
      const row = button.closest(".step-row");
      if (button.dataset.step === "remove") row.remove();
      else if (button.dataset.step === "up" && row.previousElementSibling) row.previousElementSibling.before(row);
      else if (button.dataset.step === "down" && row.nextElementSibling) row.nextElementSibling.after(row);
      kindUi.renumber();
      onChange();
    });
  }

  /* -------------------------------------------------------------- Spec */

  function collectSpec() {
    return {
      name: f.name.value.trim(),
      slug: f.slug.value.trim(),
      summary: f.summary.value.trim(),
      description: f.description.value.trim(),
      icon: state.icon,
      category: f.category.value,
      tags: f.tags.value.split(",").map((t) => t.trim()).filter(Boolean),
      ...kindUi.collect(),
    };
  }

  function fill(spec) {
    f.name.value = spec.name || "";
    f.slug.value = spec.slug || "";
    f.summary.value = spec.summary || "";
    f.description.value = spec.description || "";
    f.category.value = spec.category || "other";
    f.tags.value = (spec.tags || []).join(", ");
    setIcon(spec.icon);
    kindUi.fill(spec);
    refresh();
  }

  function slugify(text) {
    return text
      .normalize("NFKD").replace(/[̀-ͯ]/g, "")
      .toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64);
  }

  function refresh() {
    const spec = collectSpec();
    $("#ed-title").textContent = spec.name || (mode === "create" ? `${kind === "skill" ? "Nova" : "Novo"} ${KIND_NAMES[kind].toLowerCase()}` : "Sem nome");
    const version = state.item ? `v${state.item.version}` : "v1.0.0";
    let status = "Rascunho";
    if (state.item) status = state.item.status;
    if (state.dirty && mode === "edit") status += " · alterações não salvas";
    $("#ed-subtitle").textContent = `${spec.slug || "sem_identificador"} · ${version} · ${status}`;
    if (kindUi.count) kindUi.count();
  }

  function onChange() {
    state.dirty = true;
    refresh();
    scheduleValidate();
  }

  f.name.addEventListener("input", () => {
    if (!state.slugTouched) f.slug.value = slugify(f.name.value);
  });
  f.slug.addEventListener("input", () => { state.slugTouched = true; });
  f.slug.addEventListener("blur", () => {
    const normalized = slugify(f.slug.value);
    if (normalized !== f.slug.value) {
      f.slug.value = normalized;
      onChange();
    }
  });

  editorEl.addEventListener("input", (e) => {
    if (e.target.matches("input, textarea, select")) onChange();
  });
  editorEl.addEventListener("change", (e) => {
    if (e.target.matches("select, input[type=checkbox]")) onChange();
  });

  /* --------------------------------------------------------- Validação */

  let validateTimer = 0;

  function scheduleValidate() {
    clearTimeout(validateTimer);
    const status = $("#audit-status");
    status.className = "audit-status";
    status.innerHTML = '<span class="spinner"></span>Validando…';
    state.report = null;
    updateSaveState();
    validateTimer = setTimeout(validate, 400);
  }

  async function validate() {
    const seq = ++state.validationSeq;
    try {
      const res = await api("/api/items/validate/", {
        method: "POST",
        body: JSON.stringify({ kind, spec: collectSpec(), item_id: state.item ? state.item.id : null }),
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
    const { errors, warnings } = report;

    if (errors.length) {
      status.className = "audit-status is-error";
      status.innerHTML = '<svg><use href="#i-x"/></svg>';
      status.append(`${errors.length} pendência${errors.length > 1 ? "s" : ""}`);
    } else {
      status.className = "audit-status " + (warnings.length ? "is-warn" : "is-ok");
      status.innerHTML = '<svg><use href="#i-check"/></svg>';
      status.append(warnings.length ? `Válido, com ${warnings.length} aviso${warnings.length > 1 ? "s" : ""}` : "Tudo certo");
    }

    list.innerHTML = "";
    [...errors.map((m) => ["is-error", "i-x", m]), ...warnings.map((m) => ["is-warn", "i-alert", m])].forEach(([cls, icon, msg]) => {
      const li = document.createElement("li");
      li.className = `audit-item ${cls}`;
      li.innerHTML = `<svg><use href="#${icon}"/></svg><span></span>`;
      li.lastChild.textContent = msg;
      list.appendChild(li);
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
        const res = await api("/api/items/", { method: "POST", body: JSON.stringify({ kind, spec }) });
        const data = await res.json();
        state.dirty = false;
        try { sessionStorage.setItem("deimos-item-created", data.item.name); } catch (e) {}
        location.href = data.url;
        return;
      }
      const res = await api(`/api/items/${state.item.id}/`, { method: "PUT", body: JSON.stringify({ spec, changelog }) });
      const data = await res.json();
      state.item = data.item;
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
    const isPublic = state.item.visibility === "public";
    publishBtn.querySelector("use").setAttribute("href", isPublic ? "#i-eye" : "#i-upload");
    publishBtn.querySelector("span").textContent = isPublic ? "Despublicar" : "Publicar";
  }

  async function togglePublish() {
    const makePublic = state.item.visibility !== "public";
    if (makePublic && state.dirty) return toast("Salve as alterações antes de publicar.", true);
    const ok = await modal(makePublic
      ? { title: "Publicar no marketplace?", text: "Qualquer usuário poderá ver o conteúdo deste item e instalá-lo.", ok: "Publicar" }
      : { title: "Despublicar?", text: "O item sai do marketplace. Quem já instalou continua com ele.", ok: "Despublicar" });
    if (!ok) return;
    try {
      const res = await api(`/api/items/${state.item.id}/publish/`, { method: "POST", body: JSON.stringify({ public: makePublic }) });
      state.item.visibility = (await res.json()).visibility;
      updatePublishButton();
      refresh();
      toast(makePublic ? "Publicado no marketplace." : "Removido do marketplace.");
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function deleteItem() {
    const ok = await modal({
      title: `Excluir ${KIND_NAMES[kind].toLowerCase()}?`,
      text: `“${state.item.name}” será excluído permanentemente, inclusive do marketplace se estiver publicado.`,
      ok: "Excluir",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/items/${state.item.id}/`, { method: "DELETE" });
      state.dirty = false;
      location.href = "/workspace/";
    } catch (err) {
      toast(err.message, true);
    }
  }

  /* -------------------------------------------------------------- Ações */

  editorEl.addEventListener("click", (e) => {
    const action = e.target.closest("[data-editor]");
    if (!action) return;
    switch (action.dataset.editor) {
      case "save": save(); break;
      case "publish": togglePublish(); break;
      case "delete": deleteItem(); break;
      case "add-step":
        $(".step-name", kindUi.addStep()).focus();
        onChange();
        break;
    }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
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

  /* ----------------------------------------------- Descreva e gere */

  const draftForm = $("#draft-form");
  if (draftForm) {
    const input = $("#draft-input");
    const button = $("#draft-btn");
    const sync = () => { button.disabled = input.value.trim().length < 10; };
    input.addEventListener("input", (e) => { e.stopPropagation(); sync(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); draftForm.requestSubmit(); }
    });
    draftForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (button.disabled) return;
      if (state.dirty && !(await modal({ title: "Gerar novamente?", text: "O que você já preencheu será substituído pelo novo rascunho.", ok: "Gerar" }))) return;
      button.disabled = true;
      button.querySelector("span").textContent = "Gerando…";
      try {
        const res = await api("/api/items/draft/", { method: "POST", body: JSON.stringify({ kind, description: input.value.trim() }) });
        const data = await res.json();
        state.slugTouched = true;
        fill(data.spec);
        state.dirty = true;
        renderReport(data.report);
        refresh();
        toast(data.notice || "Rascunho gerado — revise e ajuste antes de criar.", Boolean(data.notice));
        editorEl.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (err) {
        toast(err.message, true);
      } finally {
        button.querySelector("span").textContent = "Gerar rascunho";
        sync();
      }
    });
  }

  /* --------------------------------------------------------------- Boot */

  window.DeimosEditor = { isDirty: () => state.dirty };

  fill(config.item);
  if (mode === "edit") updatePublishButton();
  else f.name.focus();
  validate();

  try {
    const created = sessionStorage.getItem("deimos-item-created");
    if (created) {
      sessionStorage.removeItem("deimos-item-created");
      toast(`“${created}” instalado no seu workspace.`);
    }
  } catch (e) {}
})();
