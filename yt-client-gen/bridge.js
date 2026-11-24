// Roda na página local aberta pelo Live Setup (app.py).
// Lê o job da página e pede ao service worker para executar o procedimento completo.
(() => {
  const el = document.getElementById("ytcg-job");
  if (!el) return; // não é a página do Live Setup
  let job;
  try { job = JSON.parse(el.textContent); } catch { return; }
  if (!job || !job.token || !job.project) return;

  document.documentElement.dataset.ytcg = "1";
  const base = location.origin + "/" + job.token;
  chrome.runtime.sendMessage({ type: "auto", project: job.project, name: job.name, base });
})();
