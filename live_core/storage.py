"""Leitura e gravação de configurações, perfis e tokens (pasta data/)."""
import copy
import json
import os
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LIVE_SETUP_DATA") or (BASE_DIR / "data"))
SETTINGS_FILE = DATA_DIR / "settings.json"
PROFILES_FILE = DATA_DIR / "profiles.json"
YOUTUBE_TOKEN_FILE = DATA_DIR / "youtube_token.json"
TWITCH_TOKEN_FILE = DATA_DIR / "twitch_token.json"

DEFAULT_SETTINGS = {
    "youtube_client_secret": "",
    "twitch_client_id": "",
    "twitch_client_secret": "",
    "stream_id": "",      # chave de transmissão do YouTube usada no OBS
    "stream_label": "",
    "last_profile": "",
    # Live criada pelo START que ainda não foi usada:
    # {"profile_id", "broadcast_id", "n", "from_queue"}
    "pending": None,
}

PROFILE_DEFAULTS = {
    "id": "",
    "name": "Novo perfil",
    "title_template": "{jogo} #{n}",
    "twitch_title_template": "",
    "description_template": "",
    "counter": 1,    # {n}
    "counter2": 1,   # {s}
    "queue": [],  # [{"title": str, "description": str}]
    "apply_youtube": True,
    "apply_twitch": True,
    # YouTube
    "thumbnail": "",
    "yt_playlist_id": "",
    "yt_playlist_name": "",
    "yt_tags": [],
    "yt_privacy": "",  # "" = não alterar
    "declare_no_synthetic": True,
    # Twitch
    "twitch_game_id": "",
    "twitch_game_name": "",
    "twitch_tags": [],
}


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(default)


def write_json(path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_settings():
    data = copy.deepcopy(DEFAULT_SETTINGS)
    data.update(read_json(SETTINGS_FILE, {}))
    return data


def save_settings(settings):
    write_json(SETTINGS_FILE, settings)


def new_profile(name="Novo perfil"):
    p = copy.deepcopy(PROFILE_DEFAULTS)
    p["id"] = uuid.uuid4().hex[:8]
    p["name"] = name
    return p


def load_profiles():
    profiles = []
    for raw in read_json(PROFILES_FILE, []):
        p = copy.deepcopy(PROFILE_DEFAULTS)
        p.update(raw)
        if not p["id"]:
            p["id"] = uuid.uuid4().hex[:8]
        profiles.append(p)
    return profiles


def save_profiles(profiles):
    write_json(PROFILES_FILE, profiles)
