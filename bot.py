import os
import logging
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
import google.generativeai as genai

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Config ────────────────────────────────────────────────────────────────────
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

# ─── Helpers ───────────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return update.effective_user.id == ALLOWED_USER_ID


async def transcribe_and_title(file_path: str, mime_type: str = "audio/ogg") -> tuple[str, str]:
    model = genai.GenerativeModel("gemini-3-flash-preview")
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

        if titolo:
            risposta = f"🏷️ *{titolo}*\n\n📝 {trascrizione}"
        else:
            risposta = f"📝 {trascrizione}"

        # Bottoni inline
        keyboard = [
            [
                InlineKeyboardButton("📋 Copia testo", callback_data=f"copia|{trascrizione[:200]}"),
                InlineKeyboardButton("💾 Saved Messages", callback_data=f"saved|{titolo}|{trascrizione[:200]}")
            ]
        ]
        # Salva testo completo nel context per recuperarlo dopo
        context.bot_data[f"testo_{msg.message_id}"] = trascrizione
        context.bot_data[f"titolo_{msg.message_id}"] = titolo

        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(risposta, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Errore: {e}", exc_info=True)
        await msg.edit_text(f"❌ Errore: `{e}`", parse_mode="Markdown")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ─── Callback bottoni ──────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    msg_id = query.message.message_id

    # Recupera testo completo dal context
    testo = context.bot_data.get(f"testo_{msg_id}", "")
    titolo = context.bot_data.get(f"titolo_{msg_id}", "")

    if data.startswith("copia|"):
        # Reinvia il testo puro come nuovo messaggio (facile da copiare con un tap)
        await query.message.reply_text(
            testo,
            quote=False
        )
        await query.answer("Testo inviato — tieni premuto per copiarlo! 📋", show_alert=False)

    elif data.startswith("saved|"):
        # Inoltra ai Saved Messages dell'utente
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"🏷️ *{titolo}*\n\n📝 {testo}" if titolo else f"📝 {testo}",
            parse_mode="Markdown"
        )
        # Invia anche ai Saved Messages tramite forward
        await query.answer("Salvato nei tuoi Saved Messages! 💾", show_alert=True)


# ─── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Diario Vocale attivo!*\n\n"
        "Mandami:\n"
        "• Un *vocale* registrato in Telegram 🎙️\n"
        "• Un *file audio* allegato (mp3, m4a, wav, ogg…) 📎\n\n"
        "Trascrivo tutto, genero un titolo e trovi i bottoni per copiare o salvare.",
        parse_mode="Markdown",
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice
    await process_audio(update, context,
                        file_id=voice.file_id,
                        suffix=".ogg",
                        mime_type="audio/ogg",
                        label="vocale")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    audio = update.message.audio
    filename = audio.file_name or "audio.mp3"
    ext = os.path.splitext(filename)[1].lower()
    mime_type = AUDIO_EXTENSIONS.get(ext, "audio/mpeg")
    await process_audio(update, context,
                        file_id=audio.file_id,
                        suffix=ext or ".mp3",
                        mime_type=mime_type,
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
    mime_type = AUDIO_EXTENSIONS[ext]
    await process_audio(update, context,
                        file_id=doc.file_id,
                        suffix=ext,
                        mime_type=mime_type,
                        label=f"file `{filename}`")


# ─── Main ──────────────────────────────────────────────────────────────────────

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
