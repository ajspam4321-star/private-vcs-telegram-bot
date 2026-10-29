
import os
import re
import json
import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from vehicle_lookup import fetch_vehicle_data, ensure_dirs


# ---------------- CONFIGURATION ----------------

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["TELEGRAM_USER_ID"])

ensure_dirs()

# Log errors to the FadeHost console.
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ---------------- ACCESS CONTROL ----------------

def is_allowed(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == ALLOWED_USER_ID


# ---------------- DATA CLEANING ----------------

HIDDEN_FIELDS = {
    "owner",
    "owner_name",
    "ownername",
    "name",
    "address",
    "phone",
    "mobile",
    "email",
    "father_name",
    "father",
    "dob",
    "date_of_birth",
}


def clean_data(value):
    """Remove common personal/contact fields from returned data."""

    if isinstance(value, dict):
        cleaned = {}

        for key, item in value.items():
            normalized_key = re.sub(
                r"[^a-z0-9]+", "_", str(key).lower()
            ).strip("_")

            if normalized_key in HIDDEN_FIELDS:
                continue

            if any(
                term in normalized_key
                for term in (
                    "phone",
                    "mobile",
                    "email",
                    "address",
                    "owner",
                )
            ):
                continue

            cleaned[key] = clean_data(item)

        return cleaned

    if isinstance(value, list):
        return [clean_data(item) for item in value]

    return value


# ---------------- COMMANDS ----------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_allowed(update):
        if update.effective_message:
            await update.effective_message.reply_text(
                "Access denied. This is a private bot."
            )
        return

    await update.effective_message.reply_text(
        "Private Vehicle Search Bot is ready!\n\n"
        "Send a vehicle registration number to search.\n"
        "Example: KL10AG5645"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_allowed(update):
        return

    await update.effective_message.reply_text(
        "Vehicle Search Bot Help\n\n"
        "1. Send a registration number.\n"
        "2. Wait for the lookup result.\n"
        "3. If the lookup fails, check the FadeHost logs.\n\n"
        "Example: KL10AG5645"
    )


# ---------------- VEHICLE SEARCH ----------------

async def search_vehicle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_allowed(update):
        return

    if not update.effective_message:
        return

    raw_text = update.effective_message.text or ""
    registration = re.sub(r"\s+", "", raw_text).upper()

    # Basic input validation; this does not verify that
    # a registration exists in an official database.
    if not re.fullmatch(r"[A-Z0-9-]{5,15}", registration):
        await update.effective_message.reply_text(
            "Invalid registration format.\n"
            "Please check the number and try again."
        )
        return

    status_message = await update.effective_message.reply_text(
        f"Searching for {registration}..."
    )

    try:
        result, cached = await asyncio.to_thread(
            fetch_vehicle_data,
            registration,
        )

        if not isinstance(result, dict):
            await status_message.edit_text(
                "The lookup service returned an unexpected result."
            )
            return

        # Detect common error dictionaries returned by the lookup tool.
        error_value = result.get("error")

        if error_value:
            error_text = str(error_value)

            logger.warning(
                "Lookup source returned an error for %s: %s",
                registration,
                error_text[:500],
            )

            if "404" in error_text or "no vehicle data found" in error_text.lower():
                await status_message.edit_text(
                    "No vehicle data was found by the lookup source.\n\n"
                    "Check the registration number or try again later. "
                    "This does not confirm that the vehicle is unregistered."
                )
            else:
                await status_message.edit_text(
                    "The vehicle lookup source returned an error.\n"
                    "Check the FadeHost logs for details."
                )

            return

        safe_result = clean_data(result)

        output = (
            f"Vehicle: {registration}\n"
            f"Cached result: {'Yes' if cached else 'No'}\n\n"
            + json.dumps(
                safe_result,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )

        # Telegram messages have a maximum length. Split output safely.
        chunks = [
            output[i:i + 3500]
            for i in range(0, len(output), 3500)
        ]

        await status_message.edit_text(chunks[0])

        for chunk in chunks[1:]:
            await update.effective_message.reply_text(chunk)

    except Exception:
        # Avoid printing API keys, tokens, or full response bodies.
        logger.exception(
            "Vehicle lookup failed for registration %s",
            registration,
        )

        try:
            await status_message.edit_text(
                "Search failed due to an internal error.\n"
                "Please check the FadeHost console logs."
            )
        except Exception:
            logger.warning("Could not update the Telegram status message.")


# ---------------- START BOT ----------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_vehicle,
        )
    )

    logger.info("Private Vehicle Search Bot is starting.")

    app.run_polling()


if __name__ == "__main__":
    main()

