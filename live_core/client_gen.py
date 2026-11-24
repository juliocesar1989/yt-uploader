"""Ponte entre o app e a extensão yt-client-gen (Chrome).

Fluxo:
1. O app sobe um servidor HTTP local temporário (127.0.0.1, porta livre, token aleatório).
2. Abre a página desse servidor no Chrome, no perfil onde a extensão está instalada.
3. O content script da extensão (bridge.js) lê o job da página e dispara o procedimento.
4. A extensão manda o progresso (POST /status) e, depois do download, o caminho do JSON (POST /done).
5. O app move o client_secret para a pasta do programa.
"""
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import storage

EXT_DIR = storage.BASE_DIR / "yt-client-gen"
CHROME_USER_DATA = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
TIMEOUT_S = 10 * 60


class ClientGenError(Exception):
    pass


# ================= Chrome =================
def find_chrome():
    """Caminho do chrome.exe ou None."""
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    path = winreg.QueryValue(k, None)
                    if path and Path(path).is_file():
                        return path
            except OSError:
                pass
    except ImportError:
        pass
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if p.is_file():
                return str(p)
    return None


def list_profiles():
    """[(pasta, nome visível)] dos perfis do Chrome, em ordem de nome."""
    try:
        state = json.loads((CHROME_USER_DATA / "Local State").read_text(encoding="utf-8"))
        cache = state.get("profile", {}).get("info_cache", {})
    except (OSError, ValueError):
        return []
    return sorted(((d, (info.get("name") or d)) for d, info in cache.items()), key=lambda x: x[1].lower())


def _same_dir(a, b):
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def detect_extension_profiles(ext_dir=EXT_DIR):
    """Pastas de perfil que têm a extensão desempacotada (carregada de ext_dir) instalada. Só lê arquivos."""
    found = []
    if not CHROME_USER_DATA.is_dir():
        return found
    for prof in sorted(CHROME_USER_DATA.iterdir()):
        if not prof.is_dir():
            continue
        for name in ("Secure Preferences", "Preferences"):
            f = prof / name
            if not f.is_file():
                continue
            try:
                exts = json.loads(f.read_text(encoding="utf-8")).get("extensions", {}).get("settings", {})
            except (OSError, ValueError):
                continue
            if any(isinstance(v, dict) and v.get("path") and _same_dir(v["path"], ext_dir) for v in exts.values()):
                found.append(prof.name)
                break
    return found


def open_in_chrome(url, profile_dir):
    chrome = find_chrome()
    if not chrome:
        raise ClientGenError("Não encontrei o Google Chrome instalado.")
    args = [chrome]
    if profile_dir:
        args.append(f"--profile-directory={profile_dir}")
    args.append(url)
    subprocess.Popen(args, close_fds=True)


# ================= arquivo =================
def project_id_from_secret(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return (data.get("installed") or data.get("web") or {}).get("project_id", "")
    except (OSError, ValueError, AttributeError):
        return ""


def to_recycle_bin(path):
    """Manda o arquivo para a Lixeira do Windows (o secret não pode ser baixado de novo no Console)."""
    path = str(Path(path).resolve())
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 4, 0x10, 0x40, 0x400
        op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=path + "\0",
                             fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI)
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not Path(path).exists():
            return
    except (ImportError, AttributeError, OSError):
        pass
    Path(path).unlink()


def prune_secrets(keep, folder=storage.BASE_DIR):
    """Deixa só `keep` entre os client_secret*.json de `folder`. Os outros vão para a Lixeira.

    Não faz nada se `keep` não for um arquivo dessa pasta (não dá para saber qual manter).
    Retorna os nomes removidos.
    """
    folder = Path(folder)
    if not keep or not Path(keep).is_file() or not _same_dir(Path(keep).parent, folder):
        return []
    removed = []
    for f in folder.glob("client_secret*.json"):
        if f.is_file() and not _same_dir(f, keep):
            to_recycle_bin(f)
            removed.append(f.name)
    return removed


def _clean_name(name):
    """client_secret_x (1).json → client_secret_x.json (sufixo que o Chrome põe em downloads repetidos)."""
    return re.sub(r" \(\d+\)(?=\.json$)", "", name, flags=re.I)


def move_secret(src, dest_dir=storage.BASE_DIR):
    """Valida o JSON baixado, move para dest_dir e deixa só ele lá. Retorna o caminho final."""
    src = Path(src)
    if src.suffix.lower() != ".json" or not src.is_file():
        raise ClientGenError(f"Arquivo baixado não encontrado: {src}")
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
        inst = data["installed"]
        if not inst.get("client_id") or not inst.get("client_secret"):
            raise KeyError
    except (OSError, ValueError, KeyError, TypeError):
        raise ClientGenError(f"{src.name} não parece um client_secret de app para computador.") from None

    dest_dir = Path(dest_dir)
    dest = dest_dir / _clean_name(src.name)
    if not _same_dir(src, dest):
        if dest.exists():
            to_recycle_bin(dest)  # mesmo nome, outro cliente: o antigo vai para a Lixeira
        shutil.move(str(src), str(dest))
    prune_secrets(dest, dest_dir)
    return dest


# ================= servidor =================
PAGE = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Live Setup · gerar client_secret</title>
<style>
 body{{font:15px/1.5 system-ui,sans-serif;background:#1f1f1f;color:#e6e6e6;max-width:640px;margin:40px auto;padding:0 16px}}
 h1{{font-size:20px}} #log div{{padding:4px 0;border-bottom:1px solid #333}} .err{{color:#ff6b6b}} .ok{{color:#4cc38a}}
</style></head><body>
<h1>Gerando client_secret.json</h1>
<p>Projeto: <b>{project}</b></p>
<div id="ext">Procurando a extensão yt-client-gen…</div>
<div id="log"></div>
<script type="application/json" id="ytcg-job">{job}</script>
<script>
 const base = location.pathname.replace(/\\/$/, "");
 let seen = 0;
 setTimeout(() => {{
   const ok = document.documentElement.dataset.ytcg === "1";
   document.getElementById("ext").innerHTML = ok
     ? '<span class="ok">✔ Extensão detectada. Acompanhe nas abas do Google Cloud Console.</span>'
     : '<span class="err">✖ Extensão não detectada neste perfil do Chrome.</span>';
   if (!ok) fetch(base + "/noext", {{method: "POST"}});
 }}, 3000);
 async function poll() {{
   try {{
     const r = await (await fetch(base + "/state")).json();
     const log = document.getElementById("log");
     for (; seen < r.log.length; seen++) {{
       const d = document.createElement("div"); d.textContent = r.log[seen].msg;
       if (r.log[seen].error) d.className = "err"; log.appendChild(d);
     }}
     if (r.finished) return;
   }} catch (e) {{ return; }}
   setTimeout(poll, 1000);
 }}
 poll();
</script></body></html>"""


class ClientGenBridge:
    """Servidor local de uso único. Callbacks são chamados numa thread de fundo."""

    def __init__(self, on_status, on_done, on_error):
        self.on_status, self.on_done, self.on_error = on_status, on_done, on_error
        self.token = secrets.token_urlsafe(24)
        self.log = []
        self.finished = False
        self._server = None
        self._timer = None
        self._lock = threading.Lock()

    @property
    def running(self):
        return self._server is not None and not self.finished

    def start(self, project, name, chrome_profile, open_browser=True):
        job = {"project": project, "name": name or "YouTube Desktop", "token": self.token}
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silencioso
                pass

            def _send(self, code, body=b"", ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _route(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                if not parts or not secrets.compare_digest(parts[0], bridge.token):
                    return None
                return parts[1] if len(parts) > 1 else ""

            def do_OPTIONS(self):
                self._send(204)

            def do_GET(self):
                r = self._route()
                if r == "":
                    page = PAGE.format(project=html.escape(project),
                                       job=json.dumps(job).replace("</", "<\\/"))
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                elif r == "state":
                    self._send(200, json.dumps({"log": bridge.log, "finished": bridge.finished}).encode())
                else:
                    self._send(404)

            def do_POST(self):
                r = self._route()
                if r is None:
                    return self._send(404)
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    data = json.loads(self.rfile.read(n) or b"{}") if n else {}
                except ValueError:
                    return self._send(400)
                if r == "status":
                    bridge._status(str(data.get("msg", "")), bool(data.get("error")))
                elif r == "done":
                    bridge._done(str(data.get("path", "")))
                elif r == "noext":
                    bridge._fail("A extensão yt-client-gen não respondeu nesse perfil do Chrome. "
                                 "Confira se ela está instalada/ativada e recarregada em chrome://extensions.")
                else:
                    return self._send(404)
                self._send(200, b'{"ok":true}')

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._timer = threading.Timer(TIMEOUT_S, lambda: self._fail("Tempo esgotado esperando o client_secret."))
        self._timer.daemon = True
        self._timer.start()
        url = f"http://127.0.0.1:{self._server.server_address[1]}/{self.token}/"
        if open_browser:
            try:
                open_in_chrome(url, chrome_profile)
            except Exception:
                self.stop()
                raise
        return url

    # ---------- eventos ----------
    def _status(self, msg, error=False):
        if self.finished or not msg:
            return
        self.log.append({"msg": msg, "error": error})
        self.on_status(msg, error)

    def _done(self, path):
        with self._lock:
            if self.finished:
                return
            try:
                dest = move_secret(path)
            except Exception as e:  # noqa: BLE001
                self.log.append({"msg": str(e), "error": True})
                self._finish()
                self.on_error(e)
                return
            self.log.append({"msg": f"✔ client_secret movido para {dest}", "error": False})
            self._finish()
        self.on_done(dest)

    def _fail(self, msg):
        with self._lock:
            if self.finished:
                return
            self.log.append({"msg": msg, "error": True})
            self._finish()
        self.on_error(ClientGenError(msg))

    def _finish(self):
        self.finished = True
        if self._timer:
            self._timer.cancel()
        # espera a página buscar o último /state antes de desligar
        t = threading.Timer(3, self._shutdown)
        t.daemon = True
        t.start()

    def _shutdown(self):
        srv, self._server = self._server, None
        if srv:
            srv.shutdown()
            srv.server_close()

    def stop(self):
        self.finished = True
        if self._timer:
            self._timer.cancel()
        threading.Thread(target=self._shutdown, daemon=True).start()
