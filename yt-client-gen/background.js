// Service worker: abre as páginas do Console, entrega a tarefa ao content script
// e salva o client_secret.json quando o content script captura ID + secret.
//
// Modo automático (Live Setup): bridge.js, rodando na página local aberta pelo app.py,
// manda { type: "auto", project, name, base }. O worker então ativa a API, cria o cliente,
// e depois do download envia o caminho do arquivo para `${base}/done`.

const URLS = {
  enable: (p) => `https://console.cloud.google.com/apis/library/youtube.googleapis.com?project=${encodeURIComponent(p)}`,
  create: (p) => `https://console.cloud.google.com/auth/clients/create?project=${encodeURIComponent(p)}`,
  consent: (p) => `https://console.cloud.google.com/auth/overview?project=${encodeURIComponent(p)}`,
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Espera a aba terminar de carregar. Registrar ANTES de navegar para não perder o evento.
function tabComplete(tabId, timeout = 60000) {
  return new Promise((resolve) => {
    const done = () => {
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      resolve();
    };
    const listener = (id, info) => {
      if (id === tabId && info.status === "complete") done();
    };
    const timer = setTimeout(done, timeout);
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function openTab(url, tabId) {
  if (tabId) {
    const ready = tabComplete(tabId);
    await chrome.tabs.update(tabId, { url, active: true });
    await ready;
    return tabId;
  }
  const tab = await chrome.tabs.create({ url });
  await tabComplete(tab.id);
  return tab.id;
}

// Entrega a tarefa ao content script (frame principal) e devolve a resposta dele.
async function sendTask(tabId, msg, tries = 20) {
  for (let i = 0; i < tries; i++) {
    try {
      return await chrome.tabs.sendMessage(tabId, msg, { frameId: 0 });
    } catch {
      await sleep(500);
    }
  }
  return { ok: false, msg: "A página do Console não respondeu." };
}

function buildClientSecret({ clientId, secret, project }) {
  return {
    installed: {
      client_id: clientId,
      project_id: project || undefined,
      auth_uri: "https://accounts.google.com/o/oauth2/auth",
      token_uri: "https://oauth2.googleapis.com/token",
      auth_provider_x509_cert_url: "https://www.googleapis.com/oauth2/v1/certs",
      client_secret: secret,
      redirect_uris: ["http://localhost"],
    },
  };
}

// ---------- Modo automático ----------
const getJob = async () => (await chrome.storage.session.get("job")).job || null;
const setJob = (job) => chrome.storage.session.set({ job });
const clearJob = () => chrome.storage.session.remove("job");

async function post(base, path, body) {
  try {
    await fetch(`${base}/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return true;
  } catch {
    return false;
  }
}

const report = (base, msg, error = false) => post(base, "status", { msg, error });

async function runAuto({ project, name, base }) {
  const prev = await getJob();
  if (prev && prev.base === base) return; // página recarregada: o job já está rodando
  await setJob({ project, name, base, downloadId: null });

  await report(base, "Extensão conectada. Abrindo o Google Cloud Console…");
  const tabId = await openTab(URLS.enable(project));
  const en = await sendTask(tabId, { type: "task", task: "enable" });
  await report(base, en?.msg || "Etapa 1 sem resposta.", !en?.ok);
  if (!en?.ok) {
    await report(base, "Ative a YouTube Data API v3 manualmente; vou seguir para a criação do cliente.", true);
  }
  await sleep(1500);

  await report(base, "Criando o cliente OAuth (App para computador)…");
  await openTab(URLS.create(project), tabId);
  const cr = await sendTask(tabId, { type: "task", task: "create", name });
  await report(base, cr?.msg || "Etapa 2 sem resposta.", !cr?.ok);

  if (cr?.needConsent) {
    await openTab(URLS.consent(project), tabId);
    await report(base, "Configure a tela de consentimento na aba aberta. Depois crie o cliente " +
      "(Clientes → Criar cliente → App para computador): o JSON será capturado e enviado ao app.", true);
  } else if (!cr?.ok) {
    await report(base, "Termine a criação manualmente na aba do Console: o JSON será capturado ao criar.", true);
  } else {
    await report(base, "Aguardando o Console mostrar o ID e o secret…");
  }
}

async function finishDownload(downloadId) {
  const job = await getJob();
  if (!job || job.downloadId !== downloadId) return;
  const [item] = await chrome.downloads.search({ id: downloadId });
  if (!item) return;
  if (item.state === "complete") {
    await report(job.base, `JSON baixado: ${item.filename}`);
    if (await post(job.base, "done", { path: item.filename })) await clearJob();
  } else if (item.state === "interrupted") {
    await report(job.base, `Download interrompido (${item.error || "erro"}).`, true);
  }
}

// Listener no topo do worker: funciona mesmo se o worker dormir entre o início e o fim do download.
chrome.downloads.onChanged.addListener((delta) => {
  if (delta.state && delta.state.current !== "in_progress") finishDownload(delta.id);
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "auto") {
    runAuto(msg);
    sendResponse({ ok: true });
    return;
  }

  if (msg.type === "run") {
    (async () => {
      const tabId = await openTab(URLS[msg.task](msg.project));
      if (msg.task === "consent") return;
      await sendTask(tabId, { type: "task", task: msg.task, name: msg.name, project: msg.project });
    })();
    sendResponse({ ok: true });
    return;
  }

  if (msg.type === "secret") {
    (async () => {
      const json = JSON.stringify(buildClientSecret(msg), null, 2);
      const url = "data:application/json;base64," + btoa(unescape(encodeURIComponent(json)));
      const name = `client_secret_${(msg.project || "youtube").replace(/[^\w.-]/g, "_")}.json`;
      const job = await getJob();
      let id;
      try {
        id = await chrome.downloads.download({ url, filename: name, conflictAction: "uniquify", saveAs: false });
      } catch {
        id = null;
      }
      if (id && job) {
        await setJob({ ...job, downloadId: id });
        finishDownload(id); // caso já tenha terminado antes de salvar o id
      }
      sendResponse({ ok: !!id, filename: name, auto: !!job });
    })();
    return true; // resposta assíncrona
  }
});
