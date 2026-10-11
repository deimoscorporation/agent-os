/* Páginas de equipe (organizações) e cofre de segredos. */
(() => {
  "use strict";

  const { $, api, toast, modal } = window.Deimos;

  const json = (method, body) => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

  async function run(action, { reload = true, success } = {}) {
    try {
      const res = await action();
      if (success) toast(success);
      if (reload) setTimeout(() => location.reload(), success ? 500 : 0);
      return res;
    } catch (err) {
      toast(err.message, true);
      return null;
    }
  }

  /* ------------------------------------------------------ Organizações */

  const createForm = $("#org-create-form");
  if (createForm) {
    createForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const res = await run(() => api("/api/organizations/", json("POST", { name: $("#org-create-name").value })), { reload: false });
      if (res) location.href = "/equipe/";
    });
    if (new URLSearchParams(location.search).has("nova")) $("#org-create-name").focus();
  }

  const renameForm = $("#rename-form");
  if (renameForm) {
    renameForm.addEventListener("submit", (e) => {
      e.preventDefault();
      run(() => api("/api/organization/", json("PATCH", { name: $("#org-name").value })), { success: "Organização renomeada." });
    });
  }

  const deleteOrg = $("#org-delete");
  if (deleteOrg) {
    deleteOrg.addEventListener("click", async () => {
      const name = deleteOrg.dataset.name;
      const typed = await modal({
        title: "Excluir organização?",
        text: `Isso apaga itens, instalações e segredos de “${name}”. Digite o nome da organização para confirmar.`,
        input: "",
        ok: "Excluir",
        danger: true,
      });
      if (typed === null) return;
      if (typed.trim() !== name) return toast("O nome não confere.", true);
      const res = await run(() => api("/api/organization/", json("DELETE")), { reload: false });
      if (res) location.href = "/workspace/";
    });
  }

  const inviteForm = $("#invite-form");
  if (inviteForm) {
    inviteForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const res = await run(
        () => api("/api/organization/members/", json("POST", { email: $("#invite-email").value, role: $("#invite-role").value })),
        { reload: false },
      );
      if (!res) return;
      const data = await res.json();
      toast(data.invited ? "Convite registrado — entra ao se cadastrar." : "Pessoa adicionada à organização.");
      setTimeout(() => location.reload(), 600);
    });
  }

  document.addEventListener("change", (e) => {
    const select = e.target.closest("[data-member-role]");
    if (!select) return;
    run(() => api(`/api/organization/members/${select.dataset.memberRole}/`, json("PATCH", { role: select.value })), {
      success: "Papel atualizado.",
    }).then((res) => { if (!res) location.reload(); });
  });

  /* ------------------------------------------------------------ Cofre */

  const secretForm = $("#secret-form");
  if (secretForm) {
    const name = $("#secret-name");
    name.addEventListener("input", () => {
      const start = name.selectionStart;
      name.value = name.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_");
      name.setSelectionRange(start, start);
    });
    secretForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const value = $("#secret-value");
      const res = await run(
        () => api("/api/secrets/", json("POST", { name: name.value, value: value.value, description: $("#secret-description").value })),
        { success: "Segredo guardado no cofre." },
      );
      // O valor sai da página assim que é enviado.
      if (res) value.value = "";
    });
  }

  /* ----------------------------------------------------------- Cliques */

  document.addEventListener("click", async (e) => {
    const target = e.target.closest("[data-member-remove], [data-invitation-remove], [data-secret-delete], [data-secret-fill]");
    if (!target) return;

    if (target.dataset.secretFill) {
      $("#secret-name").value = target.dataset.secretFill;
      $("#secret-value").focus();
      secretForm.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (target.dataset.memberRemove) {
      const self = target.dataset.self === "true";
      const ok = await modal(self
        ? { title: "Sair da organização?", text: `Você perde o acesso ao workspace “${target.dataset.name}”.`, ok: "Sair", danger: true }
        : { title: "Remover da organização?", text: `${target.dataset.name} perde o acesso ao workspace compartilhado.`, ok: "Remover", danger: true });
      if (!ok) return;
      const res = await run(() => api(`/api/organization/members/${target.dataset.memberRemove}/`, json("DELETE")), { reload: false });
      if (res) location.href = self ? "/workspace/" : "/equipe/";
    } else if (target.dataset.invitationRemove) {
      run(() => api(`/api/organization/invitations/${target.dataset.invitationRemove}/`, json("DELETE")), { success: "Convite cancelado." });
    } else if (target.dataset.secretDelete) {
      const ok = await modal({
        title: "Excluir segredo?",
        text: `As ferramentas que usam ${target.dataset.secretDelete} vão falhar até ele ser definido de novo.`,
        ok: "Excluir",
        danger: true,
      });
      if (ok) run(() => api(`/api/secrets/${target.dataset.secretDelete}/`, json("DELETE")), { success: "Segredo excluído." });
    }
  });
})();
