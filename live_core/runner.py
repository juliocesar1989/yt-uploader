"""Monta os textos da próxima live, valida e executa as atualizações."""
import re
from datetime import date
from pathlib import Path

YT_TITLE_MAX = 100
YT_DESC_MAX = 5000
TW_TITLE_MAX = 140
THUMB_MAX_BYTES = 2 * 1024 * 1024


def parse_tags(text):
    return [t.strip() for t in text.split(",") if t.strip()]


_COUNTER_RE = re.compile(r"\{([ns])(?::(\d))?\}")


def render(template, profile, n, s=None):
    """{n}/{s} = contadores; {n:2} → 05, {s:3} → 007 (zeros à esquerda)."""
    values = {"n": n, "s": profile.get("counter2", 1) if s is None else s}

    def repl(m):
        value = values[m.group(1)]
        width = int(m.group(2) or 0)
        return str(value).zfill(width)

    return (
        _COUNTER_RE.sub(repl, template)
        .replace("{jogo}", profile.get("name", ""))
        .replace("{data}", date.today().strftime("%d/%m/%Y"))
    )


def next_texts(profile):
    """Título/descrição da próxima live: primeiro item da fila, senão o modelo."""
    n, s = profile["counter"], profile.get("counter2", 1)
    queue = profile.get("queue") or []
    if queue:
        item = queue[0]
        title_t = item.get("title", "")
        desc_t = item.get("description") or profile["description_template"]
        from_queue = True
    else:
        title_t = profile["title_template"]
        desc_t = profile["description_template"]
        from_queue = False
    tw_t = profile.get("twitch_title_template") or title_t
    return {
        "n": n,
        "s": s,
        "from_queue": from_queue,
        "title": render(title_t, profile, n, s).strip(),
        "description": render(desc_t, profile, n, s),
        "twitch_title": render(tw_t, profile, n, s).strip(),
        # caminho da thumbnail também aceita variáveis: ...\ep{n:2}.jpg
        "thumbnail": render(profile.get("thumbnail") or "", profile, n, s).strip(),
    }


def consume(profile, texts):
    """Avança os dois contadores (+1) e tira da fila o item usado."""
    profile["counter"] = int(profile["counter"]) + 1
    profile["counter2"] = int(profile.get("counter2", 1)) + 1
    if texts["from_queue"] and profile.get("queue"):
        profile["queue"].pop(0)


def validate(texts, profile):
    errs = []
    if not profile["apply_youtube"] and not profile["apply_twitch"]:
        errs.append("Marque pelo menos YouTube ou Twitch.")
    if profile["apply_youtube"]:
        t, d = texts["title"], texts["description"]
        if not t:
            errs.append("Título do YouTube vazio.")
        if len(t) > YT_TITLE_MAX:
            errs.append(f"Título do YouTube tem {len(t)} caracteres (máx. {YT_TITLE_MAX}).")
        if "<" in t or ">" in t:
            errs.append("O YouTube não aceita < ou > no título.")
        if len(d) > YT_DESC_MAX:
            errs.append(f"Descrição tem {len(d)} caracteres (máx. {YT_DESC_MAX}).")
        if "<" in d or ">" in d:
            errs.append("O YouTube não aceita < ou > na descrição.")
        thumb = texts.get("thumbnail")
        if thumb:
            p = Path(thumb)
            if not p.is_file():
                errs.append(f"Thumbnail desta live não encontrada:\n{thumb}")
            elif p.stat().st_size > THUMB_MAX_BYTES:
                errs.append("Thumbnail maior que 2 MB (limite do YouTube).")
            elif p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                errs.append("Thumbnail precisa ser JPG ou PNG.")
    if profile["apply_twitch"]:
        t = texts["twitch_title"]
        if not t:
            errs.append("Título da Twitch vazio.")
        if len(t) > TW_TITLE_MAX:
            errs.append(f"Título da Twitch tem {len(t)} caracteres (máx. {TW_TITLE_MAX}).")
        tags = profile.get("twitch_tags") or []
        if len(tags) > 10:
            errs.append("A Twitch aceita no máximo 10 tags.")
        for tag in tags:
            if len(tag) > 25 or not tag.isalnum():
                errs.append(f"Tag da Twitch inválida: '{tag}' (só letras/números, sem espaço, até 25).")
    return errs


# lifeCycleStatus da live preparada
REUSABLE = {"created", "ready", "testStarting", "testing"}   # ainda não começou
USED = {"liveStarting", "live", "complete"}                  # já foi ao ar


def classify(status):
    """'reuse' = ainda dá para editar · 'used' = já foi ao ar · 'gone' = apagada/revogada."""
    if status in REUSABLE:
        return "reuse"
    if status in USED:
        return "used"
    return "gone"


class NoPendingLive(Exception):
    pass


def pick_upcoming(items, stream_id):
    """Escolhe a live pendente a atualizar: a ligada à chave do OBS; senão a mais antiga."""
    if not items:
        return None
    bound = [b for b in items if stream_id and b.get("stream") == stream_id]
    pool = sorted(bound or items, key=lambda b: b.get("scheduled") or "")
    return pool[0]


def describe_upcoming(items, stream_id):
    lines = []
    chosen = pick_upcoming(items, stream_id)
    for b in items:
        mark = "→ " if chosen and b["id"] == chosen["id"] else "   "
        key = " · ligada à sua chave" if stream_id and b.get("stream") == stream_id else ""
        lines.append(f"{mark}\"{b['title']}\" ({b['status']}{key}) https://youtu.be/{b['id']}")
    return lines


def run_job(profile, texts, yt, tw, stream_id, log):
    """Roda em thread separada, ANTES de entrar ao vivo.

    Twitch: atualiza o canal. YouTube: NÃO cria live; encontra a live pendente que o
    YouTube Studio já tem (a que o OBS vai usar) e atualiza os dados dela.
    """
    res = {"twitch": None, "youtube": None, "broadcast_id": None}

    if profile["apply_twitch"]:
        log("Twitch: atualizando canal...")
        try:
            tw.update_channel(
                texts["twitch_title"],
                profile.get("twitch_game_id") or None,
                profile.get("twitch_tags") or None,
            )
            res["twitch"] = True
            extra = []
            if profile.get("twitch_game_id"):
                extra.append(f"categoria {profile.get('twitch_game_name')}")
            if profile.get("twitch_tags"):
                extra.append("tags")
            log("Twitch: ✔ título" + ("" if not extra else ", " + ", ".join(extra)))
        except Exception as e:  # noqa: BLE001
            res["twitch"] = False
            log(f"Twitch: ✖ {e}")

    if profile["apply_youtube"]:
        try:
            log("YouTube: procurando a live pendente no Studio...")
            items = yt.list_upcoming()
            target = pick_upcoming(items, stream_id)
            if not target:
                raise NoPendingLive(
                    "nenhuma live pendente encontrada. Abra o YouTube Studio → Criar → Transmitir ao vivo "
                    "(aba Transmitir) uma vez para o YouTube preparar a próxima live, e clique START de novo. "
                    "Nada foi criado.")
            bid = target["id"]
            if len(items) > 1:
                log(f"YouTube: {len(items)} lives pendentes; usando a marcada com →")
                for line in describe_upcoming(items, stream_id):
                    log("   " + line)
            else:
                log(f"YouTube: live pendente encontrada — \"{target['title']}\"")
            res["broadcast_id"] = bid
            yt.update_video(
                bid,
                texts["title"],
                texts["description"],
                profile.get("yt_tags") or None,
                profile.get("yt_privacy") or None,
                profile.get("declare_no_synthetic", True),
            )
            res["youtube"] = True
            log("YouTube: ✔ título, descrição"
                + (", tags" if profile.get("yt_tags") else "")
                + (", privacidade" if profile.get("yt_privacy") else "")
                + (", conteúdo sintético = Não" if profile.get("declare_no_synthetic", True) else ""))
        except Exception as e:  # noqa: BLE001
            res["youtube"] = False
            log(f"YouTube: ✖ {e}")
            return res

        if texts.get("thumbnail"):
            try:
                yt.set_thumbnail(bid, texts["thumbnail"])
                log(f"YouTube: ✔ thumbnail ({Path(texts['thumbnail']).name})")
            except Exception as e:  # noqa: BLE001
                log(f"YouTube: ✖ thumbnail: {e}")

        if profile.get("yt_playlist_id"):
            try:
                added = yt.add_to_playlist(bid, profile["yt_playlist_id"])
                name = profile.get("yt_playlist_name") or profile["yt_playlist_id"]
                log(f"YouTube: ✔ playlist {name}" + ("" if added else " (já estava nela)"))
            except Exception as e:  # noqa: BLE001
                log(f"YouTube: ✖ playlist: {e}")

        log(f"YouTube: conferir no Studio → https://studio.youtube.com/video/{bid}/livestreaming")

    return res
