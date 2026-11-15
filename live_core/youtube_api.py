"""Cliente da YouTube Data API v3 (OAuth de app desktop)."""
import json
import mimetypes
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import storage

SCOPES = ["https://www.googleapis.com/auth/youtube"]


class YouTubeError(Exception):
    pass


def _http_msg(e):
    try:
        err = json.loads(e.content.decode("utf-8"))["error"]
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        return f"{err.get('message', '')} ({reason})" if reason else err.get("message", str(e))
    except Exception:  # noqa: BLE001
        return str(e)


class YouTubeClient:
    def __init__(self, client_secret_path):
        self.client_secret_path = client_secret_path
        self._service = None

    # ---------- autenticação ----------
    def _save(self, creds):
        storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
        storage.YOUTUBE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    def _credentials(self, interactive):
        creds = None
        if storage.YOUTUBE_TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(storage.YOUTUBE_TOKEN_FILE), SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save(creds)
                return creds
            except Exception:  # noqa: BLE001
                if not interactive:
                    raise YouTubeError("O login do YouTube expirou. Conecte de novo em Contas e configurações.")
        if not interactive:
            raise YouTubeError("YouTube não conectado. Abra Contas e configurações e clique em Conectar YouTube.")
        if not self.client_secret_path or not Path(self.client_secret_path).is_file():
            raise YouTubeError("Escolha o arquivo client_secret.json em Contas e configurações.")
        flow = InstalledAppFlow.from_client_secrets_file(self.client_secret_path, SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", open_browser=True)
        self._save(creds)
        return creds

    def login(self):
        """Abre o navegador para autorizar. Retorna o nome do canal."""
        self._service = build("youtube", "v3", credentials=self._credentials(True), cache_discovery=False)
        return self.channel_title()

    def _svc(self):
        if self._service is None:
            self._service = build("youtube", "v3", credentials=self._credentials(False), cache_discovery=False)
        return self._service

    @staticmethod
    def _exec(request):
        try:
            return request.execute(num_retries=2)
        except HttpError as e:
            raise YouTubeError(_http_msg(e)) from e

    # ---------- leitura ----------
    def channel_title(self):
        data = self._exec(self._svc().channels().list(part="snippet", mine=True))
        items = data.get("items") or []
        return items[0]["snippet"]["title"] if items else "(canal sem nome)"

    def list_playlists(self):
        out, token = [], None
        while True:
            data = self._exec(self._svc().playlists().list(
                part="snippet", mine=True, maxResults=50, pageToken=token))
            out += [(i["id"], i["snippet"]["title"]) for i in data.get("items", [])]
            token = data.get("nextPageToken")
            if not token:
                return out

    def list_streams(self):
        """Chaves de transmissão do canal: [(stream_id, nome, últimos 4 da chave)]."""
        data = self._exec(self._svc().liveStreams().list(part="id,snippet,cdn", mine=True, maxResults=50))
        out = []
        for s in data.get("items", []):
            key = s.get("cdn", {}).get("ingestionInfo", {}).get("streamName", "")
            out.append((s["id"], s["snippet"].get("title", ""), key[-4:]))
        return out

    def list_upcoming(self):
        """Lives que ainda não começaram (inclui a que o Studio prepara para a chave padrão)."""
        data = self._exec(self._svc().liveBroadcasts().list(
            part="id,snippet,contentDetails,status", broadcastStatus="upcoming",
            broadcastType="all", maxResults=50))
        return [{
            "id": b["id"],
            "title": b["snippet"].get("title", ""),
            "scheduled": b["snippet"].get("scheduledStartTime", ""),
            "stream": b.get("contentDetails", {}).get("boundStreamId", ""),
            "status": b.get("status", {}).get("lifeCycleStatus", ""),
        } for b in data.get("items", [])]

    # ---------- escrita (só altera; nunca cria live) ----------
    def update_video(self, video_id, title, description, tags=None, privacy=None, no_synthetic=True):
        data = self._exec(self._svc().videos().list(part="snippet,status", id=video_id))
        items = data.get("items") or []
        if not items:
            raise YouTubeError(f"Vídeo {video_id} não encontrado.")
        snip, st = items[0]["snippet"], items[0].get("status", {})

        snippet = {
            "title": title,
            "description": description,
            "categoryId": snip.get("categoryId", "20"),
        }
        for k in ("defaultLanguage", "defaultAudioLanguage"):
            if snip.get(k):
                snippet[k] = snip[k]
        if tags:
            snippet["tags"] = tags
        elif snip.get("tags"):
            snippet["tags"] = snip["tags"]

        # Reenvia os campos graváveis do status para não resetá-los.
        status = {"privacyStatus": privacy or st.get("privacyStatus", "public")}
        for k in ("embeddable", "license", "publicStatsViewable", "selfDeclaredMadeForKids"):
            if k in st:
                status[k] = st[k]
        if "selfDeclaredMadeForKids" not in status and "madeForKids" in st:
            status["selfDeclaredMadeForKids"] = st["madeForKids"]
        if no_synthetic:
            status["containsSyntheticMedia"] = False
        elif "containsSyntheticMedia" in st:
            status["containsSyntheticMedia"] = st["containsSyntheticMedia"]

        self._exec(self._svc().videos().update(
            part="snippet,status", body={"id": video_id, "snippet": snippet, "status": status}))

    def set_thumbnail(self, video_id, path):
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        media = MediaFileUpload(path, mimetype=mime, resumable=True)  # arquivos grandes (até 50 MB)
        self._exec(self._svc().thumbnails().set(videoId=video_id, media_body=media))

    def add_to_playlist(self, video_id, playlist_id):
        """Retorna False se o vídeo já estava na playlist."""
        existing = self._exec(self._svc().playlistItems().list(
            part="id", playlistId=playlist_id, videoId=video_id, maxResults=1))
        if existing.get("items"):
            return False
        self._exec(self._svc().playlistItems().insert(part="snippet", body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        }))
        return True
