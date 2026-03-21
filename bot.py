import os
import logging
import tempfile
import httpx
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
import google.generativeai as genai

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN      = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY      = os.environ["GEMINI_API_KEY"]
ALLOWED_USER_ID     = int(os.environ.get("ALLOWED_USER_ID", "0"))
NOTION_TOKEN        = os.environ.get("NOTION_TOKEN", "")
NOTION_PAGE_ID      = os.environ.get("NOTION_PAGE_ID", "")
ARCHIVE_BOT_TOKEN   = os.environ.get("ARCHIVE_BOT_TOKEN", "")
ARCHIVE_CHAT_ID     = 166521454  # ID Telegram di Den

genai.configure(api_key=GEMINI_API_KEY)

AUDIO_EXTENSIONS = {
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}

# msg_id -> {titolo, testo, stato, priorita}
TRASCRIZIONI: dict[int, dict] = {}


# ─── Archivio Bot 2 ──────────────────────────────────────────────────────────

async def invia_audio_archivio(file_id: str):
    """Invia l'audio originale al Bot 2 (archivio)."""
    if not ARCHIVE_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{ARCHIVE_BOT_TOKEN}/sendAudio"
    payload = {"chat_id": ARCHIVE_CHAT_ID, "audio": file_id}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=payload)
        if r.status_code == 200:
            logger.info("✅ Audio inviato all'archivio")
        else:
            logger.error(f"❌ Errore invio audio {r.status_code}: {r.text}")
    except Exception as e:
        logger.error(f"❌ Eccezione invio audio: {e}")


async def invia_all_archivio(testo: str, titolo: str):
    """Invia il messaggio al Bot 2 (archivio)."""
    if not ARCHIVE_BOT_TOKEN:
        logger.warning("ARCHIVE_BOT_TOKEN non configurato.")
        return
    oggi = datetime.now().strftime("%Y-%m-%d %H:%M")
    testo_archivio = f"🗃️ *{titolo}*\n_{oggi}_\n\n{testo}" if titolo else f"🗃️ _{oggi}_\n\n{testo}"
    url = f"https://api.telegram.org/bot{ARCHIVE_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ARCHIVE_CHAT_ID,
        "text": testo_archivio[:4096],
        "parse_mode": "Markdown"
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
        if r.status_code == 200:
            logger.info("✅ Messaggio inviato all'archivio")
        else:
            logger.error(f"❌ Errore archivio {r.status_code}: {r.text}")
    except Exception as e:
        logger.error(f"❌ Eccezione archivio: {e}")


# ─── Notion ───────────────────────────────────────────────────────────────────

async def crea_pagina_notion(titolo: str, testo: str):
    """Crea una sotto-pagina in NOTION_PAGE_ID con titolo data+titolo e corpo testo."""
    logger.info(f"Notion: inizio. TOKEN={'OK' if NOTION_TOKEN else 'MANCANTE'} PAGE_ID={'OK' if NOTION_PAGE_ID else 'MANCANTE'}")
    if not NOTION_TOKEN or not NOTION_PAGE_ID:
        logger.warning("Notion non configurato, salto creazione pagina.")
        return

    oggi = datetime.now().strftime("%Y-%m-%d")
    titolo_pagina = f"{oggi} – {titolo}" if titolo else oggi

    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "parent": {"page_id": NOTION_PAGE_ID},
        "properties": {
            "title": {
                "title": [{"text": {"content": titolo_pagina}}]
            }
        },
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"text": {"content": testo[i:i+2000]}}]
                }
            }
            for i in range(0, len(testo), 2000)
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
        if r.status_code == 200:
            logger.info(f"✅ Pagina Notion creata: {titolo_pagina}")
        else:
            logger.error(f"❌ Errore Notion {r.status_code}: {r.text}")
    except Exception as e:
        logger.error(f"Eccezione Notion: {e}")


# ─── Keyboard / testo ─────────────────────────────────────────────────────────

def build_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    dati     = TRASCRIZIONI.get(msg_id, {})
    stato    = dati.get("stato")
    priorita = dati.get("priorita")

    def btn(emoji, current):
        prefix = "● " if emoji == current else ""
        return InlineKeyboardButton(f"{prefix}{emoji}", callback_data=f"tag:{emoji}:{msg_id}")

    row1 = [btn("👍", stato),    btn("✅", stato)]
    row2 = [btn("⭐", priorita), btn("🗑️", priorita)]
    return InlineKeyboardMarkup([row1, row2])


def build_keyboard_audio(msg_id: int) -> InlineKeyboardMarkup:
    dati     = TRASCRIZIONI.get(msg_id, {})
    stato    = dati.get("stato")
    priorita = dati.get("priorita")

    def btn(emoji, current):
        prefix = "● " if emoji == current else ""
        return InlineKeyboardButton(f"{prefix}{emoji}", callback_data=f"tag:{emoji}:{msg_id}")

    row1 = [btn("👍", stato), btn("✅", stato)]
    row2 = [btn("⭐", priorita), btn("🗑️", priorita)]
    row3 = [InlineKeyboardButton("🗃️ Archivia", callback_data=f"archivia:{msg_id}")]

    return InlineKeyboardMarkup([row1, row2, row3])


def build_keyboard_link(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🗃️ Archivia", callback_data=f"archivia:{msg_id}")
    ]])


def build_testo(msg_id: int) -> str:
    dati     = TRASCRIZIONI.get(msg_id, {})
    titolo   = dati.get("titolo", "")
    testo    = dati.get("testo", "")
    stato    = dati.get("stato") or ""
    priorita = dati.get("priorita") or ""

    badge = "".join(filter(None, [stato, priorita]))
    riga_testo = f"{badge} {testo}" if badge else testo

    header = f"🏷️ *{titolo}*\n\n" if titolo else ""
    nota = "\n\n…_testo completo su Notion_ 📓"
    MAX = 4096 - len(header) - len(nota)

    if len(riga_testo) > MAX:
        riga_testo = riga_testo[:MAX] + nota

    return f"{header}{riga_testo}" if titolo else riga_testo


def build_testo_plain(msg_id: int) -> str:
    dati     = TRASCRIZIONI.get(msg_id, {})
    titolo   = dati.get("titolo", "")
    testo    = dati.get("testo", "")
    stato    = dati.get("stato") or ""
    priorita = dati.get("priorita") or ""

    badge = "".join(filter(None, [stato, priorita]))
    riga_testo = f"{badge} {testo}" if badge else testo

    header = f"🏷️ {titolo}\n\n" if titolo else ""
    nota = "\n\n…testo completo su Notion 📓"
    MAX = 4096 - len(header) - len(nota)

    if len(riga_testo) > MAX:
        riga_testo = riga_testo[:MAX] + nota

    return f"{header}{riga_testo}" if titolo else riga_testo


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return update.effective_user.id == ALLOWED_USER_ID


async def transcribe_and_title(file_path: str, mime_type: str = "audio/ogg") -> tuple[str, str]:
    model = genai.GenerativeModel("gemini-2.5-flash")
    uploaded = genai.upload_file(file_path, mime_type=mime_type)
    response = model.generate_content([
        uploaded,
        "Analizza questo audio e rispondi in questo formato esatto:\n"
        "TITOLO: [massimo 6 parole che riassumono il contenuto]\n"
        "TRASCRIZIONE: [trascrizione fedele parola per parola, testo continuo senza timestamp, senza etichette speaker, senza interruzioni]\n\n"
        "Non aggiungere timestamp, orari, nomi speaker o formattazioni. Solo testo continuo."
    ])
    text = response.text.strip()
    titolo = ""
    trascrizione = text
    for line in text.split("\n"):
        if line.startswith("TITOLO:"):
            titolo = line.replace("TITOLO:", "").strip()
    idx = text.find("TRASCRIZIONE:")
    if idx != -1:
        trascrizione = text[idx + len("TRASCRIZIONE:"):].strip()
    return titolo, trascrizione


# ─── Audio processing ─────────────────────────────────────────────────────────

async def process_audio(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        file_id: str, suffix: str, mime_type: str, label: str):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    msg = await update.message.reply_text(f"⏳ Trascrizione {label} in corso…")
    tmp_path = None
    try:
        file = await context.bot.get_file(file_id)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
        await file.download_to_drive(tmp_path)

        titolo, trascrizione = await transcribe_and_title(tmp_path, mime_type)

        TRASCRIZIONI[msg.message_id] = {
            "titolo": titolo,
            "testo": trascrizione,
            "stato": None,
            "priorita": None,
            "tipo": "audio",
            "file_id": file_id,
        }

        # Salva su Notion (asincrono)
        await crea_pagina_notion(titolo, trascrizione)

        await msg.edit_text(
            build_testo(msg.message_id),
            parse_mode="Markdown",
            reply_markup=build_keyboard_audio(msg.message_id)
        )

    except Exception as e:
        logger.error(f"Errore: {e}", exc_info=True)
        await msg.edit_text(f"❌ Errore: `{e}`", parse_mode="Markdown")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ─── Callback bottoni ─────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Gestione archiviazione link
    if query.data.startswith("archivia:"):
        msg_id = int(query.data.split(":")[1])
        dati = TRASCRIZIONI.get(msg_id)
        if not dati:
            # Fallback: recupera il testo direttamente dal messaggio (sopravvive ai restart)
            testo_msg = query.message.text or ""
            if testo_msg:
                dati = {"titolo": "", "testo": testo_msg}
        if dati:
            if dati.get("file_id"):
                await invia_audio_archivio(dati["file_id"])
            await crea_pagina_notion(dati["titolo"], dati["testo"])
            await invia_all_archivio(dati["testo"], dati["titolo"])
            try:
                await query.message.delete()
            except Exception as e:
                logger.error(f"Errore cancellazione: {e}")
            TRASCRIZIONI.pop(msg_id, None)
        else:
            await query.answer("⚠️ Dati non disponibili.", show_alert=True)
        return

    _, emoji, msg_id_str = query.data.split(":", 2)
    msg_id = int(msg_id_str)

    if msg_id not in TRASCRIZIONI:
        await query.answer("⚠️ Dati non disponibili (bot riavviato?)", show_alert=True)
        return

    dati = TRASCRIZIONI[msg_id]

    if emoji in ("👍", "✅"):
        dati["stato"] = None if dati["stato"] == emoji else emoji
    elif emoji in ("⭐", "🗑️"):
        dati["priorita"] = None if dati["priorita"] == emoji else emoji

    # Salva su Notion solo per testi, solo su ⭐ o ✅, solo quando viene ATTIVATO
    if dati.get("tipo") == "testo" and emoji in ("⭐", "✅"):
        attivo = dati.get("stato") == emoji or dati.get("priorita") == emoji
        if attivo:
            await crea_pagina_notion(dati["titolo"], dati["testo"])

    tipo = TRASCRIZIONI[msg_id].get("tipo")
    try:
        if tipo in ("testo", "link"):
            await query.message.edit_text(
                build_testo_plain(msg_id),
                reply_markup=build_keyboard(msg_id)
            )
        else:
            # Vocale/audio — usa keyboard con bottone Claude
            await query.message.edit_text(
                build_testo(msg_id),
                parse_mode="Markdown",
                reply_markup=build_keyboard_audio(msg_id)
            )
    except Exception as e:
        logger.error(f"Errore aggiornamento: {e}")


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Diario Vocale attivo!*\n\n"
        "Mandami un vocale 🎙️ o un file audio 📎\n"
        "Oppure scrivi/incolla un testo o link 📝\n\n"
        "Trascrivo i vocali e salvo su Notion 📓\n"
        "I testi vengono salvati su Notion solo se taggi ⭐ o ✅\n\n"
        "Bottoni tag:\n"
        "👍 letto  |  ✅ fatto\n"
        "⭐ importante  |  🗑️ ignora",
        parse_mode="Markdown",
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_audio(update, context,
                        file_id=update.message.voice.file_id,
                        suffix=".ogg", mime_type="audio/ogg", label="vocale")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    audio = update.message.audio
    filename = audio.file_name or "audio.mp3"
    ext = os.path.splitext(filename)[1].lower()
    mime_type = AUDIO_EXTENSIONS.get(ext, "audio/mpeg")
    await process_audio(update, context,
                        file_id=audio.file_id,
                        suffix=ext or ".mp3", mime_type=mime_type,
                        label=f"file `{filename}`")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    testo = update.message.text.strip()
    if not testo:
        return

    # Rileva se è un link
    is_link = testo.startswith("http://") or testo.startswith("https://")

    # Titolo = prime 8 parole
    parole = testo.split()
    titolo = " ".join(parole[:8]) + ("…" if len(parole) > 8 else "")

    msg = await update.message.reply_text("📝 Salvato!")

    # Cancella il messaggio originale dell'utente
    try:
        await update.message.delete()
    except Exception:
        pass

    TRASCRIZIONI[msg.message_id] = {
        "titolo": titolo,
        "testo": testo,
        "stato": None,
        "priorita": None,
        "tipo": "link" if is_link else "testo",
    }

    keyboard = build_keyboard_link(msg.message_id) if is_link else build_keyboard(msg.message_id)

    try:
        await msg.edit_text(
            build_testo_plain(msg.message_id),
            reply_markup=keyboard
        )
    except Exception as e:
        logger.error(f"Errore handle_text: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    filename = doc.file_name or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in AUDIO_EXTENSIONS:
        await update.message.reply_text(
            f"⚠️ File `{filename}` non riconosciuto come audio.\n"
            "Formati supportati: ogg, mp3, m4a, mp4, wav, flac, aac, webm",
            parse_mode="Markdown"
        )
        return
    await process_audio(update, context,
                        file_id=doc.file_id,
                        suffix=ext, mime_type=AUDIO_EXTENSIONS[ext],
                        label=f"file `{filename}`")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("🤖 Bot avviato — in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
