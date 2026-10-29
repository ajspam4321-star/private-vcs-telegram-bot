
#!/usr/bin/env python3

import os
import re
import json
import time
import hashlib
import logging
import requests

from datetime import datetime

# Existing vehicle lookup API
API_BASE = "https://vehicleinfobyterabaap.vercel.app/lookup"

# Directories
RESULTS_DIR = "results"
LOGS_DIR = "logs"
CACHE_DIR = "cache"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


# ---------------- DIRECTORY SETUP ----------------

def ensure_dirs():
    for directory in (RESULTS_DIR, LOGS_DIR, CACHE_DIR):
        os.makedirs(directory, exist_ok=True)


def log(msg):
    ensure_dirs()

    try:
        with open(
            os.path.join(LOGS_DIR, "vehicle_lookup.log"),
            "a",
            encoding="utf-8",
        ) as file:
            file.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except OSError:
        logger.exception("Unable to write lookup log")


# ---------------- REGISTRATION ----------------

def normalize_registration(rc):
    """
    Normalize an Indian vehicle registration number.
    This checks formatting only, not registration validity.
    """
    rc = re.sub(r"[\s-]+", "", str(rc)).upper()

    if not re.fullmatch(r"[A-Z0-9]{5,15}", rc):
        raise ValueError("Invalid vehicle registration format.")

    return rc


# ---------------- CACHE ----------------

def cache_path(rc):
    # Hash the registration to avoid putting it in the filename.
    filename = hashlib.sha256(rc.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{filename}.json")


def load_cache(rc):
    path = cache_path(rc)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict) and not data.get("error"):
            return data

    except (OSError, json.JSONDecodeError):
        logger.warning("Cache read failed")

    return None


def save_cache(rc, data):
    if not isinstance(data, dict) or data.get("error"):
        return

    path = cache_path(rc)
    temp_path = path + ".tmp"

    try:
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)

        os.replace(temp_path, path)

    except OSError:
        logger.exception("Cache write failed")

        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


# ---------------- VEHICLE LOOKUP ----------------

def fetch_vehicle_data(rc):
    """
    Returns:
        (result_dictionary, cached_boolean)

    The upstream API must support the existing /lookup?rc=... endpoint.
    """

    ensure_dirs()

    try:
        rc = normalize_registration(rc)
    except ValueError as exc:
        return {"error": str(exc)}, False

    cached_data = load_cache(rc)

    if cached_data is not None:
        return cached_data, True

    started = time.monotonic()

    try:
        response = requests.get(
            API_BASE,
            params={"rc": rc},
            timeout=(5, 20),
            headers={
                "User-Agent": "VehicleLookupBot/1.0",
                "Accept": "application/json",
            },
        )

    except requests.Timeout:
        log(f"Request timed out for registration {rc}")
        return {
            "error": "The vehicle lookup service timed out."
        }, False

    except requests.RequestException as exc:
        # Avoid logging complete request URLs or credentials.
        log(
            f"Request failed for {rc}: "
            f"{type(exc).__name__}"
        )
        return {
            "error": "Unable to connect to the vehicle lookup service."
        }, False

    duration_ms = round(
        (time.monotonic() - started) * 1000,
        2,
    )

    if response.status_code == 404:
        # The HTTP status alone cannot distinguish an invalid endpoint
        # from a missing record. Keep the body short for diagnostics.
        log(
            f"HTTP 404 for {rc}; "
            f"response={response.text[:300]}"
        )

        return {
            "error": (
                "The lookup source returned HTTP 404. "
                "The requested record or API endpoint may be unavailable."
            )
        }, False

    if response.status_code in (401, 403):
        log(f"HTTP {response.status_code} from lookup source")

        return {
            "error": (
                "The lookup service denied the request. "
                "Check its documented access requirements."
            )
        }, False

    if response.status_code == 429:
        log("Lookup source rate limit reached")

        return {
            "error": "The lookup service is rate-limiting requests."
        }, False

    if response.status_code >= 500:
        log(f"Upstream server error: HTTP {response.status_code}")

        return {
            "error": "The vehicle lookup service has a server error."
        }, False

    if response.status_code != 200:
        log(f"Unexpected HTTP status: {response.status_code}")

        return {
            "error": (
                f"The lookup service returned HTTP "
                f"{response.status_code}."
            )
        }, False

    try:
        data = response.json()

    except ValueError:
        log("Lookup source returned invalid JSON")

        return {
            "error": "The lookup service returned invalid JSON."
        }, False

    if not isinstance(data, dict):
        return {
            "error": "The lookup service returned an unexpected format."
        }, False

    # Respect error responses supplied inside HTTP 200 JSON.
    if data.get("error"):
        log(f"Lookup API reported an error for {rc}")

        return {
            "error": str(data["error"])[:300]
        }, False

    data["_api_time"] = duration_ms

    save_cache(rc, data)

    log(f"Successful lookup for {rc}")

    return data, False


# ---------------- OPTIONAL JSON EXPORT ----------------

def export_json(rc, data):
    ensure_dirs()

    try:
        rc = normalize_registration(rc)
    except ValueError:
        return False

    if not isinstance(data, dict) or data.get("error"):
        return False

    path = os.path.join(RESULTS_DIR, f"{rc}.json")

    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False,
            )

        return True

    except OSError:
        logger.exception("Unable to export lookup result")
        return False
