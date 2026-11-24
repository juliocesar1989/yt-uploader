// Roda dentro do Google Cloud Console.
// 1) Executa tarefas (ativar API / criar cliente) clicando pela interface.
// 2) Observa a página o tempo todo: quando aparecem client ID + secret
//    (diálogo após "Criar"), gera e baixa o client_secret.json.
//    Isso funciona mesmo se você criar o cliente manualmente.

(() => {
  if (window.__ytClientGen) return;
  window.__ytClientGen = true;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const visible = (el) => !!(el && el.offsetParent !== null && el.getClientRects().length);

  async function waitFor(fn, timeout = 20000, step = 300) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const v = fn();
      if (v) return v;
      await sleep(step);
    }
    return null;
  }

  function byText(selector, regex) {
    return [...document.querySelectorAll(selector)].find(
      (el) => visible(el) && regex.test((el.innerText || el.textContent || "").trim())
    );
  }

  function setInput(el, value) {
    el.focus();
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  }

  // ---------- Toast ----------
  function toast(text, ok = true) {
    const id = "__ytcg_toast";
    document.getElementById(id)?.remove();
    const d = document.createElement("div");
    d.id = id;
    d.textContent = text;
    Object.assign(d.style, {
      position: "fixed", zIndex: 2147483647, right: "16px", bottom: "16px",
      maxWidth: "380px", padding: "12px 16px", borderRadius: "8px",
      font: "14px/1.4 system-ui, sans-serif", color: "#fff",
      background: ok ? "#188038" : "#c5221f", boxShadow: "0 4px 16px rgba(0,0,0,.3)",
    });
    document.body.appendChild(d);
    setTimeout(() => d.remove(), 9000);
  }

  // ---------- Tarefas ----------
  // Cada tarefa mostra um toast e devolve { ok, msg } (usado pelo modo automático do Live Setup).
  function result(ok, msg, extra = {}) {
    toast(msg, ok);
    return { ok, msg, ...extra };
  }

  async function enableApi() {
    const btn = await waitFor(() =>
      byText("button, a[role=button]", /^(ativar|enable|gerenciar|manage)$/i)
    );
    if (!btn) return result(false, "Não achei o botão Ativar. Ative a API manualmente.");
    if (/gerenciar|manage/i.test(btn.innerText)) return result(true, "✔ YouTube Data API v3 já está ativa.");
    btn.click();
    toast("Ativando a YouTube Data API v3…");
    // Espera o Console confirmar (botão vira "Gerenciar" ou a página muda para a visão da API).
    const confirmed = await waitFor(
      () => byText("button, a[role=button]", /^(gerenciar|manage|desativar|disable)/i) ||
            /\/apis\/api\/youtube\.googleapis\.com/.test(location.pathname),
      60000, 500
    );
    return confirmed
      ? result(true, "✔ YouTube Data API v3 ativada.")
      : result(true, "Cliquei em Ativar, mas não consegui confirmar. Seguindo mesmo assim…");
  }

  async function createClient(name) {
    // Se o projeto não tem tela de consentimento, o Console redireciona para o setup.
    await sleep(1500);
    if (/\/auth\/overview/.test(location.pathname) || byText("button", /^(get started|primeiros passos|começar)$/i)) {
      return result(false, "Configure primeiro a tela de consentimento (Google Auth Platform) e rode de novo.",
                    { needConsent: true });
    }

    // 1. Tipo de aplicativo
    const select = await waitFor(() =>
      [...document.querySelectorAll('mat-select, [role="combobox"]')].find(visible)
    );
    if (!select) return result(false, "Não achei o campo 'Tipo de aplicativo'. Termine manualmente — o JSON será baixado ao criar.");
    select.click();
    const opt = await waitFor(() => byText('[role="option"], mat-option', /desktop|computador/i), 5000);
    if (!opt) return result(false, "Não achei a opção 'App para computador'. Selecione manualmente.");
    opt.click();
    await sleep(800);

    // 2. Nome
    const inputs = [...document.querySelectorAll('input[type="text"], input:not([type])')].filter(visible);
    const nameInput =
      inputs.find((i) => /name|nome/i.test(
        (i.getAttribute("aria-label") || "") + (i.getAttribute("formcontrolname") || "") + (i.closest("mat-form-field, label, div")?.innerText || "")
      )) || inputs[0];
    if (nameInput) setInput(nameInput, name);
    await sleep(500);

    // 3. Criar
    const create = await waitFor(() => byText("button", /^(create|criar)$/i), 5000);
    if (!create) return result(false, "Não achei o botão Criar. Clique manualmente.");
    create.click();
    return result(true, "Criando cliente… o client_secret.json será baixado em seguida.");
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type !== "task" || window !== window.top) return;
    const run = msg.task === "enable" ? enableApi()
              : msg.task === "create" ? createClient(msg.name || "YouTube Desktop")
              : null;
    if (!run) return;
    run.then(sendResponse, (e) => sendResponse({ ok: false, msg: String(e) }));
    return true; // resposta assíncrona
  });

  // ---------- Captura automática do ID + secret ----------
  const ID_RE = /\b(\d{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com)\b/;
  const SECRET_RE = /\b(GOCSPX-[A-Za-z0-9_-]{20,})\b/;
  const done = new Set();

  function scan() {
    const text =
      (document.body?.innerText || "") + "\n" +
      [...document.querySelectorAll("input, textarea")].map((i) => i.value).join("\n");
    const secret = text.match(SECRET_RE)?.[1];
    if (!secret) return;
    const clientId = text.match(ID_RE)?.[1];
    if (!clientId || done.has(clientId)) return;
    done.add(clientId);

    const project = new URLSearchParams(location.search).get("project") || "";
    chrome.runtime.sendMessage({ type: "secret", clientId, secret, project }, (res) => {
      if (res?.ok && res.auto) toast(`✔ ${res.filename} baixado e enviado ao Live Setup.`);
      else if (res?.ok) toast(`✔ ${res.filename} salvo em Downloads.`);
      else toast("Falha ao salvar o JSON. Use o botão 'Baixar JSON' do diálogo.", false);
    });
  }

  let t;
  new MutationObserver(() => {
    clearTimeout(t);
    t = setTimeout(scan, 400);
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
