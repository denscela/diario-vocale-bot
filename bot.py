import os
import logging
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
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

# Dizionario in memoria: msg_id -> {titolo, testo}
TRASCRIZIONI: dict[int, dict] = {}

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

        TRASCRIZIONI[msg.message_id] = {"titolo": titolo, "testo": trascrizione}

        keyboard = [[
            InlineKeyboardButton("👍", callback_data=f"react:👍:{msg.message_id}"),
            InlineKeyboardButton("✅", callback_data=f"react:✅:{msg.message_id}"),
            InlineKeyboardButton("⭐", callback_data=f"react:⭐:{msg.message_id}"),
            InlineKeyboardButton("🗑️", callback_data=f"react:🗑️:{msg.message_id}"),
        ]]
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

    parts = query.data.split(":", 2)
    action = parts[0]
    emoji  = parts[1]
    msg_id = int(parts[2])

    if action == "react":
        # Prova reaction nativa Telegram
        reaction_ok = False
        try:
            await context.bot.set_message_reaction(
                chat_id=query.message.chat_id,
                message_id=msg_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
                is_big=False
            )
            reaction_ok = True
        except Exception as e:
            logger.warning(f"Reaction nativa non supportata: {e}")

        if not reaction_ok:
            # Piano B: modifica il testo aggiungendo l'emoji in cima
            dati = TRASCRIZIONI.get(msg_id)
            if dati:
                titolo = dati["titolo"]
                testo  = dati["testo"]
                testo_aggiornato = f"{emoji} *{titolo}*\n\n📝 {testo}" if titolo else f"{emoji} 📝 {testo}"
                try:
                    await query.message.edit_text(testo_aggiornato, parse_mode="Markdown")
                except Exception as e2:
                    logger.error(f"Errore piano B: {e2}")
        else:
            # Reaction riuscita: rimuovi i bottoni dal messaggio
            dati = TRASCRIZIONI.get(msg_id)
            if dati:
                titolo = dati["titolo"]
                testo  = dati["testo"]
                testo_finale = f"🏷️ *{titolo}*\n\n📝 {testo}" if titolo else f"📝 {testo}"
                try:
                    await query.message.edit_text(testo_finale, parse_mode="Markdown")
                except Exception:
                    pass


# ─── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Diario Vocale attivo!*\n\n"
        "Mandami:\n"
        "• Un *vocale* registrato in Telegram 🎙️\n"
        "• Un *file audio* allegato (mp3, m4a, wav, ogg…) 📎\n\n"
        "Trascrivo tutto, genero un titolo e puoi taggare ogni nota con 👍 ✅ ⭐ 🗑️",
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
