const $ = (id) => document.getElementById(id);

chrome.storage.local.get(["project", "name"], (v) => {
  if (v.project) $("project").value = v.project;
  if (v.name) $("name").value = v.name;
});

function save() {
  chrome.storage.local.set({ project: $("project").value.trim(), name: $("name").value.trim() });
}

$("detect").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const p = new URL(tab.url).searchParams.get("project");
    if (p) { $("project").value = p; save(); }
    else $("project").placeholder = "Abra o Console no projeto";
  } catch {}
};

function run(task) {
  const project = $("project").value.trim();
  if (!project) { $("project").focus(); return; }
  save();
  chrome.runtime.sendMessage({ type: "run", task, project, name: $("name").value.trim() || "YouTube Desktop" });
  window.close();
}

$("enable").onclick = () => run("enable");
$("consent").onclick = () => run("consent");
$("create").onclick = () => run("create");
