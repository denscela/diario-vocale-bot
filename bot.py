import os
import logging
import tempfile
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
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
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "0"))  # 0 = nessun filtro

genai.configure(api_key=GEMINI_API_KEY)


# ─── Helpers ───────────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    if ALLOWED_USER_ID == 0:
        return True
    return update.effective_user.id == ALLOWED_USER_ID


async def transcribe_audio(file_path: str) -> str:
    model = genai.GenerativeModel("gemini-1.5-flash")
    uploaded = genai.upload_file(file_path, mime_type="audio/ogg")
    response = model.generate_content([
        uploaded,
        "Trascrivi questo messaggio vocale parola per parola, in italiano. "
        "Non aggiungere nulla, solo la trascrizione fedele."
    ])
    return response.text.strip()


# ─── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Diario Vocale attivo!*\n\nMandami un vocale e lo trascrivo. 🎙️",
        parse_mode="Markdown",
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    msg = await update.message.reply_text("⏳ Trascrizione in corso…")

    tmp_path = None
    try:
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await file.download_to_drive(tmp_path)

        transcription = await transcribe_audio(tmp_path)

        await msg.edit_text(f"📝 {transcription}")

    except Exception as e:
        logger.error(f"Errore: {e}", exc_info=True)
        await msg.edit_text(f"❌ Errore: `{e}`", parse_mode="Markdown")

    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("🤖 Bot avviato — in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
