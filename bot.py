import os
import logging
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
import google.generativeai as genai

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))

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
# stato:    None | "👍" | "✅"
# priorita: None | "⭐" | "🗑️"
TRASCRIZIONI: dict[int, dict] = {}


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


def build_testo(msg_id: int) -> str:
    dati     = TRASCRIZIONI.get(msg_id, {})
    titolo   = dati.get("titolo", "")
    testo    = dati.get("testo", "")
    stato    = dati.get("stato") or ""
    priorita = dati.get("priorita") or ""

    # Badge emoji davanti al testo (ricercabile)
    badge = "".join(filter(None, [stato, priorita]))
    riga_testo = f"{badge} {testo}" if badge else testo

    if titolo:
        return f"🏷️ *{titolo}*\n\n{riga_testo}"
    return riga_testo


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
        "TRASCRIZIONE: [trascrizione fedele parola per parola in italiano]\n\n"
        "Non aggiungere altro."
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
        }

        await msg.edit_text(
            build_testo(msg.message_id),
            parse_mode="Markdown",
            reply_markup=build_keyboard(msg.message_id)
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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, emoji, msg_id_str = query.data.split(":", 2)
    msg_id = int(msg_id_str)

    if msg_id not in TRASCRIZIONI:
        await query.answer("⚠️ Dati non disponibili (bot riavviato?)", show_alert=True)
        return

    dati = TRASCRIZIONI[msg_id]

    # Gruppo 1: stato (👍 / ✅) — si escludono
    if emoji in ("👍", "✅"):
        dati["stato"] = None if dati["stato"] == emoji else emoji

    # Gruppo 2: priorità (⭐ / 🗑️) — si escludono
    elif emoji in ("⭐", "🗑️"):
        dati["priorita"] = None if dati["priorita"] == emoji else emoji

    try:
        await query.message.edit_text(
            build_testo(msg_id),
            parse_mode="Markdown",
            reply_markup=build_keyboard(msg_id)
        )
    except Exception as e:
        logger.error(f"Errore aggiornamento: {e}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Diario Vocale attivo!*\n\n"
        "Mandami un vocale 🎙️ o un file audio 📎\n\n"
        "Dopo la trascrizione puoi taggare ogni nota:\n"
        "👍 letto  |  ✅ fatto\n"
        "⭐ importante  |  🗑️ ignora\n\n"
        "I tag appaiono davanti al testo — cercali direttamente in chat!",
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


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(CallbackQueryHandler(handle_callback))
    logger.info("🤖 Bot avviato — in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
