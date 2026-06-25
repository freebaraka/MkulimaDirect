"""
mpesa.py — Safaricom Daraja M-Pesa STK Push helper for Mkulima Direct
Handles: access token generation, STK push initiation, and callback parsing.

Setup: fill in your sandbox/live credentials in the config block below.
For sandbox testing, use https://sandbox.safaricom.co.ke as the BASE_URL.
For production, switch to https://api.safaricom.co.ke.
"""

import base64
import datetime
import os
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load variables from the .env file in the project root
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# ──────────────────────────────────────────────
# CONFIGURATION — loaded from .env
# ──────────────────────────────────────────────
MPESA_CONSUMER_KEY    = os.getenv("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "")
MPESA_SHORTCODE       = os.getenv("MPESA_SHORTCODE", "174379")
MPESA_PASSKEY         = os.getenv("MPESA_PASSKEY", "")
MPESA_CALLBACK_URL    = os.getenv("MPESA_CALLBACK_URL", "")
MPESA_BASE_URL        = os.getenv("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke")
# ──────────────────────────────────────────────


def _is_valid_callback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and bool(parsed.path)


def validate_mpesa_config():
    """Returns a list of missing or invalid M-Pesa settings."""
    errors = []

    if not MPESA_CONSUMER_KEY:
        errors.append("MPESA_CONSUMER_KEY is missing")
    if not MPESA_CONSUMER_SECRET:
        errors.append("MPESA_CONSUMER_SECRET is missing")
    if not MPESA_SHORTCODE:
        errors.append("MPESA_SHORTCODE is missing")
    if not MPESA_PASSKEY:
        errors.append("MPESA_PASSKEY is missing")
    if not MPESA_CALLBACK_URL:
        errors.append("MPESA_CALLBACK_URL is missing")
    elif not _is_valid_callback_url(MPESA_CALLBACK_URL):
        errors.append("MPESA_CALLBACK_URL must be a full URL with a path, e.g. https://host/api/mpesa/callback")

    return errors


def get_access_token() -> str:
    """
    Fetches a fresh OAuth access token from Safaricom Daraja.
    Tokens are valid for 1 hour; in production you'd cache this.
    """
    url = f"{MPESA_BASE_URL}/oauth/v1/generate?grant_type=client_credentials"
    credentials = base64.b64encode(
        f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}".encode()
    ).decode("utf-8")

    response = requests.get(
        url,
        headers={"Authorization": f"Basic {credentials}"},
        timeout=15
    )
    response.raise_for_status()
    return response.json()["access_token"]


def generate_password():
    """
    Generates the Base64-encoded STK push password and the timestamp.
    Formula: Base64(Shortcode + Passkey + Timestamp)
    Returns a tuple of (password, timestamp).
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    raw = f"{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}"
    password = base64.b64encode(raw.encode()).decode("utf-8")
    return password, timestamp


def initiate_stk_push(phone, amount, order_id, account_reference="MkulimaOrder"):
    """
    Sends an STK Push prompt to the buyer's phone.

    Args:
        phone:             Buyer's phone in international format, e.g. 254712345678
        amount:            Amount in KES (whole number, no decimals)
        order_id:          The order ID used in the description for traceability
        account_reference: Short label shown on the M-Pesa screen (12 chars max)

    Returns:
        The full JSON response dict from Safaricom.

    Raises:
        requests.HTTPError on non-2xx responses.
    """
    config_errors = validate_mpesa_config()
    if config_errors:
        raise ValueError("Invalid M-Pesa configuration: " + "; ".join(config_errors))

    token = get_access_token()
    password, timestamp = generate_password()

    amount = int(amount)
    if amount < 1:
        raise ValueError("Amount must be at least 1 KES")

    payload = {
        "BusinessShortCode": MPESA_SHORTCODE,
        "Password":          password,
        "Timestamp":         timestamp,
        "TransactionType":   "CustomerPayBillOnline",
        "Amount":            amount,             # Must be an integer
        "PartyA":            str(phone),         # Buyer phone
        "PartyB":            MPESA_SHORTCODE,    # Business shortcode
        "PhoneNumber":       str(phone),         # Same as PartyA for STK Push
        "CallBackURL":       MPESA_CALLBACK_URL,
        "AccountReference":  account_reference[:12],
        "TransactionDesc":   f"MkulimaOrder#{order_id}"
    }

    try:
        response = requests.post(
            f"{MPESA_BASE_URL}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            timeout=15
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "N/A")
        body = ""
        if getattr(exc, "response", None) is not None:
            body = exc.response.text
        raise RuntimeError(f"Daraja STK request failed (status={status}): {body or str(exc)}") from exc


def parse_callback(callback_data):
    """
    Parses the STK Push callback payload sent by Safaricom to your callback URL.

    Returns a normalised dict:
        {
            "success":       bool,
            "checkout_id":   str,   # MerchantRequestID
            "mpesa_code":    str,   # M-Pesa receipt e.g. QJL9XXXXXXXXX
            "phone":         str,
            "amount":        float,
            "result_code":   int,
            "result_desc":   str
        }
    """
    body = callback_data.get("Body", {}).get("stkCallback", {})
    result_code = body.get("ResultCode", -1)
    result_desc = body.get("ResultDesc", "Unknown")
    checkout_id = body.get("MerchantRequestID", "")

    if result_code != 0:
        return {
            "success":     False,
            "checkout_id": checkout_id,
            "result_code": result_code,
            "result_desc": result_desc,
            "mpesa_code":  None,
            "phone":       None,
            "amount":      None
        }

    # Extract individual callback metadata items
    items = body.get("CallbackMetadata", {}).get("Item", [])
    meta = {item["Name"]: item.get("Value") for item in items}

    return {
        "success":     True,
        "checkout_id": checkout_id,
        "mpesa_code":  meta.get("MpesaReceiptNumber"),
        "phone":       str(meta.get("PhoneNumber", "")),
        "amount":      float(meta.get("Amount", 0)),
        "result_code": result_code,
        "result_desc": result_desc
    }
