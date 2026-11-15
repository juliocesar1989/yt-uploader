"""Cliente da Twitch Helix API (OAuth authorization code + refresh)."""
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from . import storage

AUTH_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
HELIX = "https://api.twitch.tv/helix"
REDIRECT_PORT = 17563
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"
SCOPES = "channel:manage:broadcast"


class TwitchError(Exception):
    pass


class TwitchClient:
    def __init__(self, client_id, client_secret):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()

    # ---------- tokens ----------
    @staticmethod
    def _load():
        return storage.read_json(storage.TWITCH_TOKEN_FILE, None)

    @staticmethod
    def _store(tok):
        storage.write_json(storage.TWITCH_TOKEN_FILE, tok)

    def _require_app(self):
        if not self.client_id or not self.client_secret:
            raise TwitchError("Preencha Client ID e Client Secret da Twitch em Contas e configurações.")

    @staticmethod
    def _validate(access_token):
        r = requests.get(VALIDATE_URL, headers={"Authorization": f"OAuth {access_token}"}, timeout=20)
        if r.status_code == 401:
            return None
        r.raise_for_status()
        return r.json()

    def _refresh(self, tok):
        self._require_app()
        r = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
        }, timeout=20)
        if not r.ok:
            raise TwitchError("O login da Twitch expirou. Conecte de novo em Contas e configurações.")
        data = r.json()
        tok["access_token"] = data["access_token"]
        tok["refresh_token"] = data.get("refresh_token", tok["refresh_token"])
        self._store(tok)
        return tok

    def _token(self):
        tok = self._load()
        if not tok:
            raise TwitchError("Twitch não conectada. Abra Contas e configurações e clique em Conectar Twitch.")
        if self._validate(tok["access_token"]) is None:
            tok = self._refresh(tok)
        return tok

    def login(self, timeout=180):
        """Abre o navegador para autorizar. Retorna o login do canal."""
        self._require_app()
        state = secrets.token_urlsafe(16)
        result = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                qs = parse_qs(urlparse(self.path).query)
                if "code" in qs or "error" in qs:
                    result.update({k: v[0] for k, v in qs.items()})
                ok = "code" in qs
                body = ("<h2>Pronto! Pode fechar esta aba e voltar ao programa.</h2>" if ok
                        else "<h2>Login não concluído. Volte ao programa.</h2>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, *args):
                pass

        try:
            server = HTTPServer(("localhost", REDIRECT_PORT), Handler)
        except OSError as e:
            raise TwitchError(f"A porta {REDIRECT_PORT} está ocupada: {e}") from e
        server.timeout = 1
        webbrowser.open(AUTH_URL + "?" + urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "force_verify": "true",
        }))
        deadline = time.time() + timeout
        try:
            while not result and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()

        if not result:
            raise TwitchError("Tempo esgotado esperando o login da Twitch.")
        if "error" in result:
            raise TwitchError(result.get("error_description") or result["error"])
        if result.get("state") != state:
            raise TwitchError("Resposta de login inválida (state não confere).")

        r = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": result["code"],
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        }, timeout=20)
        if not r.ok:
            raise TwitchError(f"Falha ao obter o token: {r.text}")
        data = r.json()
        info = self._validate(data["access_token"]) or {}
        self._store({
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "user_id": info.get("user_id", ""),
            "login": info.get("login", ""),
        })
        return info.get("login", "")

    # ---------- API ----------
    def _api(self, method, path, params=None, json=None):
        self._require_app()
        tok = self._token()
        for attempt in (1, 2):
            r = requests.request(method, HELIX + path, params=params, json=json, timeout=20, headers={
                "Authorization": f"Bearer {tok['access_token']}",
                "Client-Id": self.client_id,
            })
            if r.status_code == 401 and attempt == 1:
                tok = self._refresh(tok)
                continue
            break
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            raise TwitchError(f"{r.status_code}: {msg}")
        return r.json() if r.content else None

    def search_categories(self, query):
        data = self._api("GET", "/search/categories", params={"query": query, "first": 20})
        return [(c["id"], c["name"]) for c in data.get("data", [])]

    def update_channel(self, title, game_id=None, tags=None):
        tok = self._load() or {}
        body = {"title": title}
        if game_id:
            body["game_id"] = game_id
        if tags:
            body["tags"] = tags
        self._api("PATCH", "/channels", params={"broadcaster_id": tok.get("user_id", "")}, json=body)
