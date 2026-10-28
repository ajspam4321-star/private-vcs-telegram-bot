import os
import re
import json
import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from vehicle_lookup import fetch_vehicle_data, ensure_dirs

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["TELEGRAM_USER_ID"])

ensure_dirs()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    await update.message.reply_text(
        "Private Vehicle Search Bot is ready!\n"
        "Send a vehicle registration number to search."
    )


async def search_vehicle(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    registration = re.sub(
        r"\s+", "", update.message.text
    ).upper()

    if not re.fullmatch(r"[A-Z0-9-]{5,15}", registration):
        await update.message.reply_text(
            "Please send a valid-looking registration number."
        )
        return

    message = await update.message.reply_text("Searching...")

    try:
        result, cached = await asyncio.to_thread(
            fetch_vehicle_data, registration
        )

        if not isinstance(result, dict):
            await message.edit_text("No usable result returned.")
            return

        # Avoid sending common personal/contact information.
        hidden = {
            "owner", "owner_name", "name", "address",
            "phone", "mobile", "email", "father_name",
            "father", "dob", "date_of_birth"
        }

        def clean(value):
            if isinstance(value, dict):
                return {
                    k: clean(v)
                    for k, v in value.items()
                    if k.lower().replace(" ", "_") not in hidden
                    and not any(
                        term in k.lower()
                        for term in (
                            "phone", "mobile", "email",
                            "address", "owner"
                        )
                    )
                }
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        output = (
            f"Vehicle: {registration}\n"
            f"Cached result: {'Yes' if cached else 'No'}\n\n"
            + json.dumps(clean(result), indent=2, ensure_ascii=False)
        )

        chunks = [
            output[i:i + 3500]
            for i in range(0, len(output), 3500)
        ]

        await message.edit_text(chunks[0])

        for chunk in chunks[1:]:
            await update.message.reply_text(chunk)

    except Exception:
        await message.edit_text(
            "Search failed. Please check the bot logs."
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_vehicle
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()
