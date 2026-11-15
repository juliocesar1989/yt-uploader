"""Monta os textos da próxima live, valida e executa as atualizações."""
import re
from datetime import date
from pathlib import Path

YT_TITLE_MAX = 100
YT_DESC_MAX = 5000
TW_TITLE_MAX = 140
THUMB_MAX_BYTES = 50 * 1024 * 1024  # limite da API thumbnails.set desde 14/09/2026 (antes era 2 MB)
THUMB_MAX_LABEL = "50 MB"


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
        **_thumb_fields(profile, n, s),
    }


# ---------------- thumbnail ----------------
THUMB_EXTS = (".jpg", ".jpeg", ".png")
_NUM_RE = re.compile(r"\d+")


def check_thumb_file(path):
    """Erro da imagem (texto) ou None se ela pode ser enviada ao YouTube."""
    p = Path(path)
    if not p.is_file():
        return "arquivo não encontrado"
    if p.suffix.lower() not in THUMB_EXTS:
        return f"extensão {p.suffix or '(sem extensão)'} não aceita — use JPG ou PNG"
    size = p.stat().st_size
    if size > THUMB_MAX_BYTES:
        return f"arquivo com {size / 1024 / 1024:.1f} MB — o YouTube aceita no máximo {THUMB_MAX_LABEL}"
    if size == 0:
        return "arquivo vazio"
    return None


def file_number(path):
    """Último número do nome do arquivo: 'Livro 6 - Ep 05.png' → 5."""
    nums = _NUM_RE.findall(Path(path).stem)
    return int(nums[-1]) if nums else None


def find_in_folder(folder, number):
    """Arquivo da pasta cujo nome termina com `number`. Retorna (caminho, erro)."""
    try:
        files = [f for f in Path(folder).iterdir() if f.is_file()]
    except OSError as e:
        return None, f"não consegui ler a pasta: {e}"
    matches = [f for f in files if file_number(f) == number]
    images = [f for f in matches if f.suffix.lower() in THUMB_EXTS]
    if len(images) == 1:
        return str(images[0]), None
    if len(images) > 1:
        names = ", ".join(sorted(f.name for f in images))
        return None, f"mais de um arquivo com o número {number}: {names} — deixe só um"
    if matches:  # achou o número, mas com extensão errada (ex.: .webp)
        return str(matches[0]), None
    return None, f"nenhum arquivo com o número {number} no final do nome nesta pasta"


def resolve_thumbnail(profile, n, s):
    """Qual imagem o START vai enviar. Retorna (caminho ou '', erro ou None).

    - campo vazio → não mexe na thumbnail;
    - pasta → procura o arquivo cujo nome termina com o número de {n} ou {s};
    - arquivo (pode ter variáveis, ex. ...\\ep{n:2}.jpg) → usa esse arquivo.
    """
    raw = (profile.get("thumbnail") or "").strip()
    if not raw:
        return "", None
    target = render(raw, profile, n, s).strip()
    if Path(target).is_dir():
        number = s if profile.get("thumb_counter") == "s" else n
        path, err = find_in_folder(target, number)
        if err:
            return path or "", err
    else:
        path = target
    return path, check_thumb_file(path)


def _thumb_fields(profile, n, s):
    path, err = resolve_thumbnail(profile, n, s)
    return {"thumbnail": path, "thumbnail_error": err}


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
        # 2ª verificação da imagem (a 1ª é a prévia na tela), feita de novo na hora do START
        if texts.get("thumbnail_error"):
            where = f"\n{texts['thumbnail']}" if texts.get("thumbnail") else ""
            errs.append(f"Thumbnail: {texts['thumbnail_error']}{where}")
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
    # "failed" lista o que deu errado; o START só incrementa se ficar vazia.
    res = {"twitch": None, "youtube": None, "broadcast_id": None, "failed": []}

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
            res["failed"].append("Twitch")
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
            res["failed"].append("YouTube")
            log(f"YouTube: ✖ {e}")
            return res

        if texts.get("thumbnail"):
            try:
                yt.set_thumbnail(bid, texts["thumbnail"])
                log(f"YouTube: ✔ thumbnail ({Path(texts['thumbnail']).name})")
            except Exception as e:  # noqa: BLE001
                res["failed"].append("thumbnail")
                log(f"YouTube: ✖ thumbnail: {e}")

        if profile.get("yt_playlist_id"):
            try:
                added = yt.add_to_playlist(bid, profile["yt_playlist_id"])
                name = profile.get("yt_playlist_name") or profile["yt_playlist_id"]
                log(f"YouTube: ✔ playlist {name}" + ("" if added else " (já estava nela)"))
            except Exception as e:  # noqa: BLE001
                res["failed"].append("playlist")
                log(f"YouTube: ✖ playlist: {e}")

        log(f"YouTube: conferir no Studio → https://studio.youtube.com/video/{bid}/livestreaming")

    return res
