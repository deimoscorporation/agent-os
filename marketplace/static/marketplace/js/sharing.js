/* Painel de compartilhamento dos editores: privado, organização e envio para revisão. */
(() => {
  "use strict";

  const { $, api, toast, modal } = window.Deimos;
  const panel = $("#sharing");
  if (!panel) return;

  const base = `/api/items/${panel.dataset.item}`;
  const post = (url, body) => api(url, { method: "POST", body: JSON.stringify(body || {}) });

  const CONFIRM = {
    organization: { title: "Compartilhar com a organização?", text: "Todos os membros poderão ver o conteúdo e instalar nos seus workspaces.", ok: "Compartilhar" },
    private: { title: "Tornar privado?", text: "O item sai do marketplace e qualquer revisão em andamento é cancelada. Quem já instalou continua com a versão que tem.", ok: "Tornar privado" },
    send: { title: "Enviar para revisão?", text: "A equipe da Deimos analisa o conteúdo antes de publicar. Você pode acompanhar o status aqui.", ok: "Enviar" },
    cancel: { title: "Cancelar o envio?", text: "O pedido sai da fila de revisão.", ok: "Cancelar envio" },
  };

  panel.addEventListener("click", async (e) => {
    const button = e.target.closest("[data-share], [data-submit]");
    if (!button) return;
    const action = button.dataset.share || button.dataset.submit;

    // O que vai para os outros é a versão salva — nunca um rascunho na tela.
    if (window.DeimosEditor && window.DeimosEditor.isDirty()) {
      return toast("Salve as alterações antes de mudar o compartilhamento.", true);
    }
    if (!(await modal(CONFIRM[action]))) return;

    try {
      if (button.dataset.share) await post(`${base}/share/`, { visibility: action });
      else if (action === "send") await post(`${base}/submit/`);
      else await api(`${base}/submit/`, { method: "DELETE" });
      location.reload();
    } catch (err) {
      toast(err.message, true);
    }
  });
})();
