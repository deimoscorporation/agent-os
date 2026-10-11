/* Marketplace, detalhe de item e workspace. */
(() => {
  "use strict";

  const { $, api, toast, modal, copyText } = window.Deimos;

  /* ------------------------------------------------ Filtros do catálogo */

  const grid = $("#catalog-grid");
  if (grid) {
    const search = $("#catalog-search");
    const tabs = $("#kind-tabs");
    const category = $("#catalog-category");
    const sort = $("#catalog-sort");
    const scope = $("#catalog-scope");
    if (scope) scope.addEventListener("change", () => apply());
    const empty = $("#catalog-empty");
    const params = new URLSearchParams(location.search);
    let kind = params.get("kind") || "";
    category.value = params.get("category") || "";

    const apply = () => {
      const q = search.value.trim().toLowerCase();
      let visible = 0;
      grid.querySelectorAll(".card").forEach((card) => {
        const show =
          (!kind || card.dataset.kind === kind) &&
          (!category.value || card.dataset.category === category.value) &&
          (!scope || !scope.value || card.dataset.scope === scope.value) &&
          (!q || card.dataset.search.includes(q));
        card.hidden = !show;
        if (show) visible++;
      });
      empty.hidden = visible > 0;
      tabs.querySelectorAll(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.kind === kind));
    };

    const applySort = () => {
      const key = sort.value;
      const value = (card) => Number(card.dataset[key]) || 0;
      [...grid.querySelectorAll(".card")]
        .sort((a, b) => value(b) - value(a) || value2(b) - value2(a))
        .forEach((card) => grid.appendChild(card));
    };
    const value2 = (card) => Number(card.dataset.installs) || 0;

    const syncUrl = () => {
      const url = new URL(location.href);
      kind ? url.searchParams.set("kind", kind) : url.searchParams.delete("kind");
      category.value ? url.searchParams.set("category", category.value) : url.searchParams.delete("category");
      history.replaceState({}, "", url);
    };

    tabs.addEventListener("click", (e) => {
      const tab = e.target.closest(".tab");
      if (!tab) return;
      kind = tab.dataset.kind;
      syncUrl();
      apply();
    });
    category.addEventListener("change", () => { syncUrl(); apply(); });
    sort.addEventListener("change", applySort);
    search.addEventListener("input", apply);
    apply();
  }

  /* ------------------------------------------------ Instalar / remover */

  function setInstallButton(button, installed) {
    button.dataset.installed = String(installed);
    button.className = button.className.replace(/\bbtn-(primary|installed)\b/, installed ? "btn-installed" : "btn-primary");
    button.querySelector("use").setAttribute("href", installed ? "#i-check" : "#i-download");
    button.querySelector("span").textContent = installed ? "Instalado" : "Instalar";
  }

  async function toggleInstall(button) {
    const id = button.dataset.install;
    const installed = button.dataset.installed === "true";
    button.disabled = true;
    try {
      await api(`/api/marketplace/${id}/${installed ? "uninstall" : "install"}/`, { method: "POST" });
      // Na página de detalhe, instalar libera o formulário de avaliação.
      if ($("#reviews")) return location.reload();
      setInstallButton(button, !installed);
      toast(installed ? "Removido do workspace." : "Instalado no workspace.");
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
    }
  }

  // No hover, o botão "Instalado" sugere a remoção.
  document.addEventListener("mouseover", (e) => {
    const b = e.target.closest('[data-install][data-installed="true"]:not(:disabled)');
    if (b) b.querySelector("span").textContent = "Remover";
  });
  document.addEventListener("mouseout", (e) => {
    const b = e.target.closest('[data-install][data-installed="true"]');
    if (b && !b.contains(e.relatedTarget)) b.querySelector("span").textContent = "Instalado";
  });

  /* ------------------------------------------------ Workspace */

  async function removeFromWorkspace(button, isOwn) {
    const name = button.dataset.name;
    const ok = await modal(isOwn
      ? { title: "Excluir item?", text: `“${name}” será excluído permanentemente, inclusive do marketplace se estiver publicado.`, ok: "Excluir", danger: true }
      : { title: "Remover do workspace?", text: `“${name}” deixará de estar disponível para seus agentes.`, ok: "Remover", danger: true });
    if (!ok) return;
    try {
      if (isOwn) await api(`/api/items/${button.dataset.deleteItem}/`, { method: "DELETE" });
      else await api(`/api/marketplace/${button.dataset.uninstall}/uninstall/`, { method: "POST" });
      location.reload();
    } catch (err) {
      toast(err.message, true);
    }
  }

  document.addEventListener("change", async (e) => {
    const input = e.target.closest("[data-toggle-enabled]");
    if (!input) return;
    const card = input.closest(".ws-card");
    try {
      await api(`/api/workspace/items/${input.dataset.toggleEnabled}/`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: input.checked }),
      });
      card.classList.toggle("is-disabled", !input.checked);
      toast(input.checked ? "Ativado." : "Desativado — agentes não verão este item.");
    } catch (err) {
      input.checked = !input.checked;
      toast(err.message, true);
    }
  });

  /* ------------------------------------------------ Avaliações */

  const reviewForm = $("#review-form");
  if (reviewForm) {
    const url = `/api/marketplace/${reviewForm.dataset.item}/review/`;
    let rating = Number(reviewForm.dataset.rating) || 0;
    const stars = [...reviewForm.querySelectorAll(".star")];
    const paint = (n) => stars.forEach((s) => {
      const on = Number(s.dataset.star) <= n;
      s.classList.toggle("is-on", on);
      s.setAttribute("aria-checked", String(Number(s.dataset.star) === rating));
    });
    stars.forEach((s) => {
      s.addEventListener("click", () => { rating = Number(s.dataset.star); paint(rating); });
      s.addEventListener("mouseenter", () => paint(Number(s.dataset.star)));
    });
    reviewForm.querySelector(".stars-input").addEventListener("mouseleave", () => paint(rating));
    paint(rating);

    reviewForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!rating) return toast("Escolha uma nota de 1 a 5 estrelas.", true);
      try {
        await api(url, { method: "POST", body: JSON.stringify({ rating, comment: $("#review-comment").value }) });
        location.reload();
      } catch (err) {
        toast(err.message, true);
      }
    });

    const remove = reviewForm.querySelector('[data-review="delete"]');
    if (remove) {
      remove.addEventListener("click", async () => {
        try {
          await api(url, { method: "DELETE" });
          location.reload();
        } catch (err) {
          toast(err.message, true);
        }
      });
    }
  }

  /* ------------------------------------------------ Cliques */

  document.addEventListener("click", (e) => {
    // Fecha o menu "Criar" ao clicar fora.
    document.querySelectorAll("details.menu[open]").forEach((menu) => {
      if (!menu.contains(e.target)) menu.open = false;
    });

    const target = e.target.closest("[data-install], [data-uninstall], [data-delete-item], [data-upgrade], [data-copy-text], [data-copy-target]");
    if (!target) return;
    if (target.dataset.upgrade) {
      api(`/api/workspace/items/${target.dataset.upgrade}/upgrade/`, { method: "POST" })
        .then(() => location.reload())
        .catch((err) => toast(err.message, true));
    } else if (target.dataset.install) toggleInstall(target);
    else if (target.dataset.uninstall) removeFromWorkspace(target, false);
    else if (target.dataset.deleteItem) removeFromWorkspace(target, true);
    else if (target.dataset.copyText) copyText(target.dataset.copyText, target);
    else if (target.dataset.copyTarget) copyText($(target.dataset.copyTarget).textContent, target);
  });

  /* ------------------------------------------------ Realce de código */

  if (window.hljs) document.querySelectorAll("pre code[class*='language-']").forEach((el) => hljs.highlightElement(el));
})();
