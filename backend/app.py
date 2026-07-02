from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db_connection  
import json
import os
import hmac
import base64
import re
import random
import secrets
import time
import smtplib
from datetime import datetime
from urllib import request as urlrequest
from urllib import error as urlerror
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def load_local_env_file():
    """Loads key=value pairs from project-root/.env then backend/.env if not already set."""
    backend_dir = os.path.dirname(__file__)
    project_root = os.path.dirname(backend_dir)
    candidate_paths = [
        os.path.join(project_root, '.env'),
        os.path.join(backend_dir, '.env')
    ]

    for env_path in candidate_paths:
        if not os.path.exists(env_path):
            continue

        try:
            with open(env_path, 'r', encoding='utf-8') as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue

                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    existing = os.environ.get(key)
                    # Allow later env files to override blank placeholders.
                    if key and (existing is None or str(existing).strip() == ''):
                        os.environ[key] = value
        except Exception as e:
            print(f"Warning: could not load .env file {env_path}: {e}")


load_local_env_file()

# ------------
# EMAIL SETUP 
# ------------
GMAIL_ADDRESS = "sirwoossah@gmail.com" 
GMAIL_APP_PASSWORD = "psbf qfry urkm hzud"

def send_email(to_email, subject, body):
    """Sends an email using Gmail's secure SMTP server"""
    try:
        msg = MIMEMultipart()
        msg['From'] = f"Mkulima Direct "
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Connect to Gmail
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls() # Secure the connection
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        print(f"✅ Email successfully sent to {to_email}")
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {str(e)}")
        return False


def send_receipt_email(to_email, order_details, role):
    receipt_body = f"""
--- MKULIMA DIRECT RECEIPT ---
Order ID: {order_details['order_id']}
Buyer: {order_details['buyer_name']}
Farmer: {order_details['farmer_name']}
Items: {order_details['item_name']} (Qty: {order_details['quantity']})
Total Amount: KSh {order_details['total']}
Delivery Address: {order_details['address']}
-----------------------------
Thank you for using Mkulima Direct!
"""

    subject = f"Mkulima Direct: {role} Receipt for Order #{order_details['order_id']}"
    if not send_email(to_email, subject, receipt_body):
        raise RuntimeError("Receipt email delivery failed")

OTP_PURPOSE_SIGNUP = 'signup'
OTP_PURPOSE_LOGIN = 'login'
SIGNUP_OTP_TTL_SECONDS = 10 * 60
LOGIN_OTP_TTL_SECONDS = 5 * 60

NAME_REGEX = re.compile(r'^[A-Za-z]+\s+[A-Za-z]+$')
EMAIL_REGEX = re.compile(r'^[\w\.-]+@[\w\.-]+\.\w+$') # NEW: Validates an email address

app = Flask(__name__)
CORS(app)


SESSION_STORE = {}
SESSION_TTL_SECONDS = 60 * 60 * 8
ADMIN_API_KEY = (os.environ.get('MKULIMA_ADMIN_API_KEY') or '').strip()
DEFAULT_ADMIN_API_KEY = 'SECURE-ADMIN-KEY-2026'
MPESA_ENV = (os.environ.get('MPESA_ENV') or 'sandbox').strip().lower()
MPESA_BASE_URL = (os.environ.get('MPESA_BASE_URL') or '').strip()
MPESA_CONSUMER_KEY = (os.environ.get('MPESA_CONSUMER_KEY') or '').strip()
MPESA_CONSUMER_SECRET = (os.environ.get('MPESA_CONSUMER_SECRET') or '').strip()
MPESA_SHORTCODE = (os.environ.get('MPESA_SHORTCODE') or '').strip()
MPESA_PASSKEY = (os.environ.get('MPESA_PASSKEY') or '').strip()
MPESA_CALLBACK_URL = (os.environ.get('MPESA_CALLBACK_URL') or '').strip()


def _mpesa_base_url():
    if MPESA_BASE_URL:
        return MPESA_BASE_URL
    if MPESA_ENV == 'live':
        return 'https://api.safaricom.co.ke'
    return 'https://sandbox.safaricom.co.ke'


def _normalize_mpesa_phone(phone_number):
    digits = ''.join(ch for ch in str(phone_number or '') if ch.isdigit())
    if digits.startswith('0') and len(digits) == 10:
        digits = '254' + digits[1:]
    elif digits.startswith('7') and len(digits) == 9:
        digits = '254' + digits

    if not digits.startswith('254') or len(digits) != 12:
        raise ValueError('Phone number must be a valid Kenyan mobile number.')
    return digits


def _mpesa_timestamp():
    return datetime.now().strftime('%Y%m%d%H%M%S')


def _get_mpesa_access_token():
    if not MPESA_CONSUMER_KEY or not MPESA_CONSUMER_SECRET:
        raise RuntimeError('M-Pesa credentials are not configured.')

    credentials = f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}".encode('utf-8')
    auth_b64 = base64.b64encode(credentials).decode('utf-8')

    req = urlrequest.Request(
        f"{_mpesa_base_url()}/oauth/v1/generate?grant_type=client_credentials",
        headers={
            'Authorization': f'Basic {auth_b64}'
        },
        method='GET'
    )

    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except urlerror.HTTPError as e:
        err_body = ''
        try:
            err_body = e.read().decode('utf-8', errors='ignore')
        except Exception:
            err_body = ''
        raise RuntimeError(f'M-Pesa token request failed ({e.code}): {err_body or e.reason}')

    access_token = payload.get('access_token')
    if not access_token:
        raise RuntimeError('Failed to obtain M-Pesa access token.')

    return access_token


def _initiate_mpesa_stk_push(phone_number, amount, account_reference, transaction_desc):
    missing = []
    if not MPESA_SHORTCODE:
        missing.append('MPESA_SHORTCODE')
    if not MPESA_PASSKEY:
        missing.append('MPESA_PASSKEY')
    if not MPESA_CALLBACK_URL:
        missing.append('MPESA_CALLBACK_URL')

    if missing:
        raise RuntimeError('M-Pesa STK settings are not fully configured. Missing: ' + ', '.join(missing))

    normalized_phone = _normalize_mpesa_phone(phone_number)
    timestamp = _mpesa_timestamp()
    password = base64.b64encode(f"{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}".encode('utf-8')).decode('utf-8')
    access_token = _get_mpesa_access_token()

    payload = {
        'BusinessShortCode': MPESA_SHORTCODE,
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': 'CustomerPayBillOnline',
        'Amount': int(round(float(amount))),
        'PartyA': normalized_phone,
        'PartyB': MPESA_SHORTCODE,
        'PhoneNumber': normalized_phone,
        'CallBackURL': MPESA_CALLBACK_URL,
        'AccountReference': account_reference,
        'TransactionDesc': transaction_desc
    }

    req = urlrequest.Request(
        f"{_mpesa_base_url()}/mpesa/stkpush/v1/processrequest",
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        },
        method='POST'
    )

    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urlerror.HTTPError as e:
        err_body = ''
        try:
            err_body = e.read().decode('utf-8', errors='ignore')
        except Exception:
            err_body = ''
        raise RuntimeError(f'M-Pesa STK request failed ({e.code}): {err_body or e.reason}')


def issue_session_token(user_name, role, user_email):
    token = secrets.token_urlsafe(32)
    SESSION_STORE[token] = {
        "user_name": user_name,
        "role": role,
        "user_email": user_email,
        "expires_at": time.time() + SESSION_TTL_SECONDS
    }
    return token


def get_authenticated_session(required_role=None):
    auth_header = (request.headers.get('Authorization') or '').strip()
    token = ''
    if auth_header.lower().startswith('bearer '):
        token = auth_header[7:].strip()

    if not token:
        token = (request.headers.get('X-Session-Token') or '').strip()

    if not token:
        return None, (jsonify({"error": "Unauthorized. Missing session token."}), 401)

    session = SESSION_STORE.get(token)
    if not session:
        return None, (jsonify({"error": "Unauthorized. Invalid session token."}), 401)

    if session.get("expires_at", 0) < time.time():
        SESSION_STORE.pop(token, None)
        return None, (jsonify({"error": "Session expired. Please log in again."}), 401)

    if required_role and session.get("role") != required_role:
        return None, (jsonify({"error": "Forbidden. Role mismatch."}), 403)

    if not session.get("user_email"):
        return None, (jsonify({"error": "Session identity is incomplete. Please log in again."}), 401)

    return session, None


def is_valid_admin_key(candidate_key):
    provided_key = (candidate_key or '').strip()
    if not provided_key:
        return False

    expected_key = ADMIN_API_KEY or DEFAULT_ADMIN_API_KEY
    return bool(expected_key) and hmac.compare_digest(provided_key, expected_key)


@app.before_request
def check_admin_access():
    # For admin APIs, allow either admin session token or X-Admin-Key.
    if request.path.startswith('/api/admin'):
        # Let browser CORS preflight pass through.
        if request.method == 'OPTIONS':
            return None

        auth_header = (request.headers.get('Authorization') or '').strip()
        token = ''
        if auth_header.lower().startswith('bearer '):
            token = auth_header[7:].strip()

        if token:
            session = SESSION_STORE.get(token)
            if session and session.get('expires_at', 0) >= time.time() and session.get('role') == 'admin':
                return None

        admin_key = request.headers.get('X-Admin-Key')
        if not is_valid_admin_key(admin_key):
            return jsonify({"error": "Unauthorized"}), 401


def get_admin_authorization():
    """Allows admin access via admin session token or configured API key."""
    auth_header = (request.headers.get('Authorization') or '').strip()
    token = ''
    if auth_header.lower().startswith('bearer '):
        token = auth_header[7:].strip()

    if token:
        session = SESSION_STORE.get(token)
        if session and session.get('expires_at', 0) >= time.time() and session.get('role') == 'admin':
            return session, None

    provided_key = request.headers.get('X-Admin-Key')
    if is_valid_admin_key(provided_key):
        return {"role": "admin", "auth": "api_key"}, None

    return None, (jsonify({"error": "Forbidden. Admin access required."}), 403)


def ensure_contact_columns_support_email():
    """Migrates legacy phone_number columns to email and enforces basic email checks."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'farmer' AND column_name = 'phone_number'
        """)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE farmer RENAME COLUMN phone_number TO email")

        cursor.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'buyer' AND column_name = 'phone_number'
        """)
        if cursor.fetchone():
            cursor.execute("ALTER TABLE buyer RENAME COLUMN phone_number TO email")

        cursor.execute("ALTER TABLE farmer ALTER COLUMN email TYPE VARCHAR(255)")
        cursor.execute("ALTER TABLE buyer ALTER COLUMN email TYPE VARCHAR(255)")

        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'farmer_email_must_have_at'
                ) THEN
                    ALTER TABLE farmer
                    ADD CONSTRAINT farmer_email_must_have_at
                    CHECK (POSITION('@' IN email) > 1);
                END IF;
            END $$;
        """)

        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'buyer_email_must_have_at'
                ) THEN
                    ALTER TABLE buyer
                    ADD CONSTRAINT buyer_email_must_have_at
                    CHECK (POSITION('@' IN email) > 1);
                END IF;
            END $$;
        """)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not apply contact-column migration: {e}")
    finally:
        cursor.close()
        conn.close()


ensure_contact_columns_support_email()


def ensure_checkout_contact_columns():
    """Ensures checkout contact columns exist on order_details."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'order_details' AND column_name = 'delivery_address'
        """)
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE order_details ADD COLUMN delivery_address TEXT")

        cursor.execute("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'order_details' AND column_name = 'buyer_phone'
        """)
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE order_details ADD COLUMN buyer_phone VARCHAR(50)")

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not apply checkout contact migration: {e}")
    finally:
        cursor.close()
        conn.close()


ensure_checkout_contact_columns()


def ensure_produce_stock_is_numeric():
    """Normalizes produce.stock_quantity to NUMERIC so arithmetic updates are safe."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            ALTER TABLE produce
            ALTER COLUMN stock_quantity TYPE NUMERIC(12, 2)
            USING (
                CASE
                    WHEN TRIM(stock_quantity::text) ~ '^[0-9]+(\\.[0-9]+)?$'
                        THEN TRIM(stock_quantity::text)::NUMERIC
                    ELSE 0
                END
            )
        """)

        cursor.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'produce_stock_quantity_non_negative'
                ) THEN
                    ALTER TABLE produce
                    ADD CONSTRAINT produce_stock_quantity_non_negative
                    CHECK (stock_quantity >= 0);
                END IF;
            END $$;
        """)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not normalize produce stock_quantity: {e}")
    finally:
        cursor.close()
        conn.close()


ensure_produce_stock_is_numeric()


def ensure_audit_logs_table():
    """Ensures audit_logs exists for login/logout session tracking."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                log_id SERIAL PRIMARY KEY,
                user_name VARCHAR(255) NOT NULL,
                user_role VARCHAR(50) NOT NULL,
                login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                logout_time TIMESTAMP,
                session_duration_minutes DECIMAL(10, 2)
            )
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not ensure audit_logs table: {e}")
    finally:
        cursor.close()
        conn.close()


ensure_audit_logs_table()


def ensure_otp_codes_table():
    """Persists OTPs with expiry so verification survives app restarts."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS otp_codes (
                otp_id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                purpose VARCHAR(50) NOT NULL,
                code VARCHAR(10) NOT NULL,
                payload_json TEXT,
                user_role VARCHAR(50),
                user_name VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                used_at TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_otp_codes_lookup
            ON otp_codes (email, purpose, created_at DESC)
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not ensure otp_codes table: {e}")
    finally:
        cursor.close()
        conn.close()


ensure_otp_codes_table()


def ensure_farmer_wallet_tables():
    """Ensures wallet tables exist for farmer credits and withdrawals."""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS farmer_wallet (
                farmer_id INT PRIMARY KEY REFERENCES farmer(farmer_id) ON DELETE CASCADE,
                balance NUMERIC(14, 2) NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                txn_id SERIAL PRIMARY KEY,
                farmer_id INT NOT NULL REFERENCES farmer(farmer_id) ON DELETE CASCADE,
                type VARCHAR(20) NOT NULL,
                amount NUMERIC(14, 2) NOT NULL,
                reference VARCHAR(255),
                status VARCHAR(50) NOT NULL DEFAULT 'Pending',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not ensure farmer wallet tables: {e}")
    finally:
        cursor.close()
        conn.close()


ensure_farmer_wallet_tables()


def store_otp_code(email, purpose, code, ttl_seconds, payload_json=None, user_role=None, user_name=None):
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()

        # Keep only one active OTP per email+purpose to avoid ambiguity.
        cursor.execute(
            """
            UPDATE otp_codes
            SET used_at = CURRENT_TIMESTAMP
            WHERE email = %s
              AND purpose = %s
              AND used_at IS NULL
            """,
            (email, purpose)
        )

        cursor.execute(
            """
            INSERT INTO otp_codes (email, purpose, code, payload_json, user_role, user_name, expires_at)
            VALUES (
                %s, %s, %s, %s, %s, %s,
                CURRENT_TIMESTAMP + (%s || ' seconds')::interval
            )
            """,
            (email, purpose, code, payload_json, user_role, user_name, str(int(ttl_seconds)))
        )

        # Opportunistic cleanup of stale rows.
        cursor.execute("DELETE FROM otp_codes WHERE expires_at < CURRENT_TIMESTAMP OR used_at IS NOT NULL")

        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not store OTP code: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def get_active_otp(email, purpose):
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT otp_id, code, payload_json, user_role, user_name
            FROM otp_codes
            WHERE email = %s
              AND purpose = %s
              AND used_at IS NULL
              AND expires_at > CURRENT_TIMESTAMP
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email, purpose)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "otp_id": row[0],
            "code": row[1],
            "payload_json": row[2],
            "user_role": row[3],
            "user_name": row[4]
        }
    except Exception as e:
        print(f"Warning: could not fetch OTP code: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def mark_otp_used(otp_id):
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE otp_codes SET used_at = CURRENT_TIMESTAMP WHERE otp_id = %s AND used_at IS NULL",
            (otp_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not mark OTP as used: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


def record_login_audit(user_name, user_role):
    """Creates a login audit row and returns its id when possible."""
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cursor = conn.cursor()

        # Close any stale open sessions for this user before recording a new login.
        cursor.execute(
            """
            UPDATE audit_logs
            SET logout_time = CURRENT_TIMESTAMP,
                session_duration_minutes = ROUND((EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - login_time)) / 60.0)::numeric, 2)
            WHERE user_name = %s
              AND user_role = %s
              AND logout_time IS NULL
            """,
            (user_name, user_role)
        )

        cursor.execute(
            "INSERT INTO audit_logs (user_name, user_role) VALUES (%s, %s) RETURNING log_id",
            (user_name, user_role)
        )
        log_id = cursor.fetchone()[0]
        conn.commit()
        return log_id
    except Exception as e:
        conn.rollback()
        print(f"Warning: could not insert audit login row: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


@app.route('/api/auth/login', methods=['POST'])
def audit_login():
    data = request.json or {}
    username = (data.get('userName') or data.get('username') or '').strip()
    role = (data.get('role') or data.get('userRole') or '').strip().lower()

    if not username or not role:
        return jsonify({"error": "userName and role are required."}), 400

    record_login_audit(username, role)
    return jsonify({"message": "Logged in"}), 200


@app.route('/api/auth/logout', methods=['POST'])
def audit_logout():
    data = request.json or {}
    username = (data.get('userName') or data.get('username') or '').strip()
    user_role = (data.get('role') or data.get('userRole') or '').strip().lower()

    if not username:
        return jsonify({"error": "userName is required."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    cursor = None
    try:
        cursor = conn.cursor()

        if user_role:
            cursor.execute("""
                UPDATE audit_logs
                SET logout_time = CURRENT_TIMESTAMP,
                    session_duration_minutes = ROUND((EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - login_time)) / 60.0)::numeric, 2)
                WHERE log_id = (
                    SELECT log_id
                    FROM audit_logs
                    WHERE user_name = %s AND user_role = %s AND logout_time IS NULL
                    ORDER BY login_time DESC
                    LIMIT 1
                )
            """, (username, user_role))
        else:
            cursor.execute("""
                UPDATE audit_logs
                SET logout_time = CURRENT_TIMESTAMP,
                    session_duration_minutes = ROUND((EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - login_time)) / 60.0)::numeric, 2)
                WHERE log_id = (
                    SELECT log_id
                    FROM audit_logs
                    WHERE user_name = %s AND logout_time IS NULL
                    ORDER BY login_time DESC
                    LIMIT 1
                )
            """, (username,))

        conn.commit()
        return jsonify({"message": "Logged out", "updated": cursor.rowcount}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE: SIGNUP REQUEST OTP 
# ----------------------------------------------------
@app.route('/api/signup/request_otp', methods=['POST'])
def signup_request_otp():
    data = request.json or {}
    full_name = data.get('fullName')
    location = data.get('location')
    email = data.get('email')
    password = data.get('password')

    # 1. Strict Validations
    if not all([full_name, location, email, password]):
        return jsonify({"error": "All fields are required, including location."}), 400

    if not NAME_REGEX.match(full_name):
        return jsonify({"error": "Name must be exactly two words with no numbers or special characters."}), 400

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Please enter a valid email address containing an '@' symbol."}), 400

    # 2. Generate code and persist an expiring OTP row.
    otp_code = str(random.randint(100000, 999999))
    signup_payload = {
        "fullName": full_name,
        "location": location,
        "email": email,
        "role": data.get('role'),
        "passwordHash": generate_password_hash(password)
    }
    saved = store_otp_code(
        email=email,
        purpose=OTP_PURPOSE_SIGNUP,
        code=otp_code,
        ttl_seconds=SIGNUP_OTP_TTL_SECONDS,
        payload_json=json.dumps(signup_payload)
    )
    if not saved:
        return jsonify({"error": "Could not create verification code. Please try again."}), 500

    # 3. Send the Email
    subject = "Mkulima Direct Verification Code"
    body = f"Hello {full_name},\n\nYour 6-digit verification code is: {otp_code}\n\nWelcome to Mkulima Direct!"

    if send_email(email, subject, body):
        return jsonify({"message": "Verification code sent to your email!"}), 200
    
    return jsonify({"error": "Failed to send email. Check your console."}), 500

# ----------------------------------------------------
# ROUTE: VERIFY EMAIL & CREATE ACCOUNT
# ----------------------------------------------------
@app.route('/api/signup/verify', methods=['POST'])
def signup_verify():
    data = request.json or {}
    email = data.get('email')
    user_code = data.get('code')
    role = data.get('role')

    if not email or not user_code:
        return jsonify({"error": "Email and code are required."}), 400

    stored_data = get_active_otp(email, OTP_PURPOSE_SIGNUP)

    if not stored_data or stored_data['code'] != user_code:
        return jsonify({"error": "Invalid or expired verification code."}), 400

    # If code matches, insert them into the database
    signup_data = {}
    try:
        signup_data = json.loads(stored_data.get('payload_json') or '{}')
    except json.JSONDecodeError:
        signup_data = {}

    full_name = signup_data.get('fullName')
    location = signup_data.get('location')
    role = (role or signup_data.get('role') or '').lower()
    password_hash = signup_data.get('passwordHash')

    if not all([full_name, location, password_hash]):
        return jsonify({"error": "Verification data expired or corrupted. Please request a new code."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        if role == 'farmer':
            cursor.execute(
                "INSERT INTO farmer (full_name, email, farmer_location, password_hash) VALUES (%s, %s, %s, %s)",
                (full_name, email, location, password_hash)
            )
        else:
            cursor.execute(
                "INSERT INTO buyer (full_name, email, password_hash) VALUES (%s, %s, %s)",
                (full_name, email, password_hash)
            )

        conn.commit()
        mark_otp_used(stored_data['otp_id'])
        return jsonify({"message": "Account verified and created successfully!"}), 200

    except Exception as e:
        conn.rollback()
        if "unique constraint" in str(e).lower():
            return jsonify({"error": "This email is already registered."}), 409
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ------------------------
# ROUTE 1: REGISTRATION 
# -----------------------
@app.route('/api/register', methods=['POST'])
def register_user():
    data = request.json
    
    full_name = data.get('fullName')
    email = data.get('email') or data.get('phone')
    role = data.get('role')
    password = data.get('password')

    if not all([full_name, email, role, password]):
        return jsonify({"error": "All fields are required!"}), 400

    if not EMAIL_REGEX.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    hashed_password = generate_password_hash(password)

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        if role == 'farmer':
            cursor.execute(
                "INSERT INTO farmer (full_name, email, password_hash) VALUES (%s, %s, %s)",
                (full_name, email, hashed_password)
            )
        elif role == 'buyer':
            cursor.execute(
                "INSERT INTO buyer (full_name, email, password_hash) VALUES (%s, %s, %s)",
                (full_name, email, hashed_password)
            )
        else:
            return jsonify({"error": "Invalid role selected"}), 400

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"message": "Registration successful!"}), 201

    except Exception as e:
        if "unique constraint" in str(e).lower():
            return jsonify({"error": "This email is already registered."}), 409
        return jsonify({"error": str(e)}), 500


# ----------------------------------------------------
# ROUTE 2: LOGIN (STEP 1 - Check Password & Send OTP)
# ----------------------------------------------------
@app.route('/api/login', methods=['POST'])
def login_user():
    data = request.json or {}
    name = (data.get('name') or '').strip()
    username = (data.get('username') or '').strip()
    password = data.get('password')

    # Admin login mode (username/email + password) for admin dashboard access.
    # This runs first and returns immediate success without OTP.
    if username and password:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Database connection failed"}), 500

        try:
            cursor = conn.cursor()
            cursor.execute("SELECT admin_id, password_hash FROM admin WHERE email = %s", (username,))
            admin_row = cursor.fetchone()

            if admin_row:
                stored_hash = admin_row[1] or ''
                password_ok = (stored_hash == password)
                if not password_ok:
                    try:
                        password_ok = check_password_hash(stored_hash, password)
                    except Exception:
                        password_ok = False

                if password_ok:
                    record_login_audit('admin', 'admin')
                    return jsonify({
                        "status": "success",
                        "message": "Login successful",
                        "role": "admin",
                        "adminKey": ADMIN_API_KEY or DEFAULT_ADMIN_API_KEY
                    }), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        finally:
            cursor.close()
            conn.close()

    if not name or not password:
        return jsonify({"error": "Full Name and password are required!"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        user_role = None
        user_email = None

        # 1. Check if the user is a Farmer
        cursor.execute("SELECT password_hash, email FROM farmer WHERE full_name = %s", (name,))
        farmer = cursor.fetchone()
        
        if farmer and check_password_hash(farmer[0], password):
            user_role = "farmer"
            user_email = farmer[1]
        else:
            # 2. Check if the user is a Buyer
            cursor.execute("SELECT password_hash, email FROM buyer WHERE full_name = %s", (name,))
            buyer = cursor.fetchone()
            
            if buyer and check_password_hash(buyer[0], password):
                user_role = "buyer"
                user_email = buyer[1]

        # 3. If password or name is wrong
        if not user_role:
            return jsonify({"error": "Invalid name or password."}), 401
            
        if not user_email:
            return jsonify({"error": "No email associated with this account."}), 400

        # 4. Generate and send the Login OTP
        otp_code = str(random.randint(100000, 999999))
        
        saved = store_otp_code(
            email=user_email,
            purpose=OTP_PURPOSE_LOGIN,
            code=otp_code,
            ttl_seconds=LOGIN_OTP_TTL_SECONDS,
            user_role=user_role,
            user_name=name
        )
        if not saved:
            return jsonify({"error": "Failed to generate login code. Please try again."}), 500

        subject = "Mkulima Direct - Login Attempt"
        body = f"Hello {name},\n\nSomeone is attempting to log into your account.\nYour 6-digit login code is: {otp_code}\n\nIf this was not you, please secure your account."
        
        if send_email(user_email, subject, body):
            return jsonify({
                "message": "OTP sent to your registered email!", 
                "email": user_email,
                "requireOtp": True
            }), 200
        else:
            return jsonify({"error": "Failed to send login email."}), 500

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE 2B: LOGIN VERIFY (STEP 2 - Check Code & Grant Access)
# ----------------------------------------------------
@app.route('/api/login/verify', methods=['POST'])
def login_verify():
    data = request.json or {}
    email = data.get('email')
    user_code = data.get('code')

    if not email or not user_code:
         return jsonify({"error": "Email and code are required."}), 400

    stored_data = get_active_otp(email, OTP_PURPOSE_LOGIN)

    if not stored_data or stored_data['code'] != user_code:
        return jsonify({"error": "Invalid or expired login code."}), 400

    # Success! Grab the role so the frontend knows where to redirect
    role = stored_data.get('user_role')
    user_name = stored_data.get('user_name')

    if not role or not user_name:
        return jsonify({"error": "Login verification data is invalid. Please request a new code."}), 400

    # Record a login audit row for this new authenticated session.
    record_login_audit(user_name, role)
    
    # Consume OTP so it can't be reused.
    mark_otp_used(stored_data['otp_id'])

    session_token = issue_session_token(user_name, role, email)
    return jsonify({
        "message": "Login successful!",
        "role": role,
        "userName": user_name,
        "token": session_token
    }), 200
# ----------------------------------------------------
# ROUTE 3: GET ALL USERS (For the Admin Dashboard)
# ----------------------------------------------------
@app.route('/api/users', methods=['GET'])
def get_all_users():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        
        # We use UNION ALL to combine the farmer and buyer tables into one big list!
        # We also sort them so the newest users appear at the top.
        query = """
            SELECT full_name, 'farmer' as role, email, joined_date FROM farmer
            UNION ALL
            SELECT full_name, 'buyer' as role, email, joined_date FROM buyer
            ORDER BY joined_date DESC;
        """
        cursor.execute(query)
        users = cursor.fetchall()

        # Format the raw database data into a clean list of dictionaries
        user_list = []
        for user in users:
            user_list.append({
                "fullName": user[0],
                "role": user[1],
                "email": user[2],
                # Format the timestamp so it looks nice (e.g., "2026-06-16 11:30")
                "joinedDate": user[3].strftime("%Y-%m-%d %H:%M") if user[3] else "Unknown"
            })

        return jsonify(user_list), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
    # ----------------------------------------------------
# ROUTE 4: GET DASHBOARD STATS (For Admin)
# ----------------------------------------------------
@app.route('/api/admin/stats', methods=['GET'])
def get_admin_stats():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Count total farmers
        cursor.execute("SELECT COUNT(*) FROM farmer;")
        total_farmers = cursor.fetchone()[0]

        # 2. Count total buyers
        cursor.execute("SELECT COUNT(*) FROM buyer;")
        total_buyers = cursor.fetchone()[0]

        # 3. Count total produce listings
        cursor.execute("SELECT COUNT(*) FROM produce;")
        total_produce = cursor.fetchone()[0]

        # 4. Fetch Recent Activity Log (Simplified to avoid timestamp column issues)
        activity_query = """
            SELECT 'Order Placed' AS type, 'Order #' || order_id || ' (' || order_status || ')' AS description, order_id AS sort_key
            FROM orders
            UNION ALL
            SELECT 'New User', full_name || ' registered as a Farmer', farmer_id
            FROM farmer
            UNION ALL
            SELECT 'New Produce', name || ' listed on the market', produce_id
            FROM produce
            ORDER BY sort_key DESC 
            LIMIT 6;
        """
        cursor.execute(activity_query)
        activities = cursor.fetchall()
        
        recent_activities = []
        for a in activities:
            recent_activities.append({
                "type": a[0],
                "description": a[1],
                "time": "Recent"
            })

        return jsonify({
            "totalUsers": total_farmers + total_buyers,
            "totalFarmers": total_farmers,
            "totalBuyers": total_buyers,
            "totalProduce": total_produce,
            "activities": recent_activities
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/farmers', methods=['GET'])
def get_farmers():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT farmer_id, full_name, email, farmer_location, joined_date FROM farmer ORDER BY joined_date DESC")
        farmers = [{
            "userId": r[0],
            "fullName": r[1],
            "email": r[2],
            "location": r[3] if r[3] else "Not set",
            "joinedDate": r[4].strftime("%Y-%m-%d") if r[4] else "Unknown"
        } for r in cursor.fetchall()]
        return jsonify(farmers)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/buyers', methods=['GET'])
def get_buyers():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT buyer_id, full_name, email, delivery_address, joined_date FROM buyer ORDER BY joined_date DESC")
        buyers = [{
            "userId": r[0],
            "fullName": r[1],
            "email": r[2],
            "location": r[3] if r[3] else "Not set",
            "joinedDate": r[4].strftime("%Y-%m-%d") if r[4] else "Unknown"
        } for r in cursor.fetchall()]
        return jsonify(buyers)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/orders', methods=['GET'])
def get_orders():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.order_id, b.full_name, o.total_amount, o.order_status
            FROM orders o JOIN buyer b ON o.buyer_id = b.buyer_id
        """)
        orders = [{
            "fullName": r[1],
            "email": f"Order #{r[0]} - KSh {r[2]}",
            "joinedDate": r[3]
        } for r in cursor.fetchall()]
        return jsonify(orders)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/users/<role>/<int:user_id>', methods=['PUT'])
def admin_update_user(role, user_id):
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    role = (role or '').lower()
    if role not in ['farmer', 'buyer']:
        return jsonify({"error": "Invalid role."}), 400

    data = request.json or {}
    full_name = data.get('fullName')
    email = data.get('email')
    location = data.get('location')

    if full_name is None and email is None and location is None:
        return jsonify({"error": "No fields provided to update."}), 400

    if full_name is not None and not NAME_REGEX.match(full_name):
        return jsonify({"error": "Name must be exactly two words with no numbers or special characters."}), 400

    if email is not None and not EMAIL_REGEX.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        table_name = 'farmer' if role == 'farmer' else 'buyer'
        id_column = 'farmer_id' if role == 'farmer' else 'buyer_id'

        update_parts = []
        values = []
        if full_name is not None:
            update_parts.append('full_name = %s')
            values.append(full_name)
        if email is not None:
            update_parts.append('email = %s')
            values.append(email)
        if location is not None:
            location_column = 'farmer_location' if role == 'farmer' else 'delivery_address'
            update_parts.append(f"{location_column} = %s")
            values.append(location)

        values.append(user_id)
        query = f"UPDATE {table_name} SET {', '.join(update_parts)} WHERE {id_column} = %s"
        cursor.execute(query, tuple(values))

        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "User not found."}), 404

        conn.commit()
        return jsonify({"message": "User updated successfully."}), 200
    except Exception as e:
        conn.rollback()
        if "unique constraint" in str(e).lower():
            return jsonify({"error": "This email is already registered."}), 409
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/users/<role>/<int:user_id>', methods=['DELETE'])
def admin_delete_user(role, user_id):
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    role = (role or '').lower()
    if role not in ['farmer', 'buyer']:
        return jsonify({"error": "Invalid role."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        table_name = 'farmer' if role == 'farmer' else 'buyer'
        id_column = 'farmer_id' if role == 'farmer' else 'buyer_id'

        cursor.execute(f"DELETE FROM {table_name} WHERE {id_column} = %s", (user_id,))
        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "User not found."}), 404

        conn.commit()
        return jsonify({"message": "User deleted successfully."}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE 5: ADD NEW PRODUCE (Farmer Dashboard)
# ----------------------------------------------------
@app.route('/api/produce', methods=['POST'])
def add_produce():
    session, auth_error = get_authenticated_session(required_role='farmer')
    if auth_error:
        return auth_error

    data = request.json

    farmer_email = session['user_email']
    name = data.get('name')
    description = data.get('description')
    category = data.get('category')
    price = data.get('price')
    unit = data.get('unit')
    quantity = data.get('quantity')

    try:
        quantity = float(quantity)
        if quantity <= 0:
            return jsonify({"error": "Quantity must be greater than 0."}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "Quantity must be a valid number."}), 400

    # Basic validation
    if not all([name, price, unit, quantity]):
        return jsonify({"error": "Please fill in all required fields!"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Resolve the authenticated farmer using unique email.
        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (farmer_email,))
        farmer = cursor.fetchone()

        if not farmer:
            return jsonify({"error": "Farmer account not found."}), 404

        farmer_id = farmer[0]

        # 2. Insert the new produce into the database attached to this farmer_id
        cursor.execute("""
            INSERT INTO produce (farmer_id, name, description, category, price_per_unit, unit_type, stock_quantity)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (farmer_id, name, description, category, price, unit, quantity))

        conn.commit()
        return jsonify({"message": f"Successfully added {name} to the market!"}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE: GET ALL PRODUCE FOR MARKETPLACE (UPDATED)
# ----------------------------------------------------
@app.route('/api/produce', methods=['GET'])
def get_all_produce():
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # Join produce, farmer, and farmer_ratings to get average rating and review count
        query = """
            SELECT 
                p.produce_id,
                p.name,
                p.description,
                p.price_per_unit,
                p.stock_quantity,
                p.unit_type,
                f.full_name,
                f.farmer_location,
                f.email,
                COALESCE(ROUND(AVG(fr.rating_value), 1), 0) AS avg_rating,
                COUNT(fr.rating_value) AS review_count
            FROM produce p
            JOIN farmer f ON p.farmer_id = f.farmer_id
            LEFT JOIN farmer_ratings fr ON f.farmer_id = fr.farmer_id
            GROUP BY 
                p.produce_id,
                p.name,
                p.description,
                p.price_per_unit,
                p.stock_quantity,
                p.unit_type,
                p.listed_date,
                f.full_name,
                f.farmer_location,
                f.email
            ORDER BY p.listed_date DESC;
        """
        cursor.execute(query)
        produce_records = cursor.fetchall()

        produce_list = []
        for row in produce_records:
            produce_list.append({
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "price": float(row[3]),
                "stock": row[4],
                "unit": row[5],
                "farmerName": row[6],
                "location": row[7] if row[7] else "Not Specified",
                "phone": row[8],
                "rating": float(row[9]),
                "reviews": int(row[10])
            })

        return jsonify(produce_list), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 7: ADD TO CART (Buyer Dashboard)
# ----------------------------------------------------
@app.route('/api/cart', methods=['POST'])
def add_to_cart():
    session, auth_error = get_authenticated_session(required_role='buyer')
    if auth_error:
        return auth_error

    data = request.json
    buyer_email = session['user_email']
    produce_id = data.get('produceId')

    if not produce_id:
        return jsonify({"error": "Produce ID is required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Resolve the authenticated buyer using unique email.
        cursor.execute("SELECT buyer_id FROM buyer WHERE email = %s", (buyer_email,))
        buyer = cursor.fetchone()

        if not buyer:
            return jsonify({"error": "Buyer account not found."}), 404

        buyer_id = buyer[0]

        # 2. Insert the item into the cart table
        # We default the quantity to 1 for now. They can change it at checkout.
        cursor.execute("""
            INSERT INTO cart (buyer_id, produce_id, quantity)
            VALUES (%s, %s, %s)
        """, (buyer_id, produce_id, 1))

        conn.commit()
        return jsonify({"message": "Item added to your cart successfully!"}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()    
# ----------------------------------------------------
# ROUTE 8: VIEW CART (Buyer Dashboard)
# ----------------------------------------------------
@app.route('/api/cart/<buyer_name>', methods=['GET'])
def view_cart(buyer_name):
    session, auth_error = get_authenticated_session(required_role='buyer')
    if auth_error:
        return auth_error

    if buyer_name != session['user_name']:
        return jsonify({"error": "Forbidden. You can only access your own cart."}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Resolve the authenticated buyer using unique email.
        cursor.execute("SELECT buyer_id FROM buyer WHERE email = %s", (session['user_email'],))
        buyer = cursor.fetchone()

        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404

        buyer_id = buyer[0]

        # 2. Get the cart items linked to this buyer
        query = """
            SELECT c.cart_id,
                   c.produce_id,
                   p.name,
                   p.price_per_unit,
                   p.unit_type,
                   c.quantity,
                   (p.price_per_unit * c.quantity) as subtotal,
                   COALESCE(p.stock_quantity, 0) as available_stock
            FROM cart c
            JOIN produce p ON c.produce_id = p.produce_id
            WHERE c.buyer_id = %s
            ORDER BY c.added_at DESC;
        """
        cursor.execute(query, (buyer_id,))
        items = cursor.fetchall()

        cart_list = []
        grand_total = 0

        for item in items:
            subtotal = item[6]
            grand_total += subtotal
            available_stock = float(item[7] or 0)
            cart_list.append({
                "cartId": item[0],
                "produceId": item[1],
                "name": item[2],
                "price": item[3],
                "unit": item[4],
                "quantity": item[5],
                "subtotal": subtotal,
                "availableStock": available_stock,
                "inStock": available_stock > 0
            })

        return jsonify({"items": cart_list, "grandTotal": grand_total}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/cart/update', methods=['POST'])
def update_cart_quantity():
    session, auth_error = get_authenticated_session(required_role='buyer')
    if auth_error:
        return auth_error

    data = request.json or {}
    buyer_email = session['user_email']
    produce_id = data.get('produceId')
    new_quantity = data.get('newQuantity')

    if produce_id is None or new_quantity is None:
        return jsonify({"error": "produceId and newQuantity are required."}), 400

    try:
        produce_id = int(produce_id)
        new_quantity = int(new_quantity)
        if new_quantity < 1:
            return jsonify({"error": "Quantity must be at least 1."}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "produceId and newQuantity must be valid numbers."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT buyer_id FROM buyer WHERE email = %s", (buyer_email,))
        buyer = cursor.fetchone()
        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404

        buyer_id = buyer[0]

        cursor.execute("SELECT stock_quantity FROM produce WHERE produce_id = %s", (produce_id,))
        stock_row = cursor.fetchone()
        if not stock_row:
            return jsonify({"error": "Produce item not found."}), 404

        available_stock = float(stock_row[0] or 0)
        if available_stock <= 0:
            return jsonify({"error": f"Item #{produce_id} is out of stock."}), 409

        if new_quantity > available_stock:
            return jsonify({"error": f"Only {available_stock} units are available for item #{produce_id}."}), 409

        cursor.execute(
            "UPDATE cart SET quantity = %s WHERE buyer_id = %s AND produce_id = %s",
            (new_quantity, buyer_id, produce_id)
        )

        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Cart item not found."}), 404

        conn.commit()
        return jsonify({"message": "Cart quantity updated successfully."}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE 9: REMOVE FROM CART
# ----------------------------------------------------
@app.route('/api/cart/<int:cart_id>', methods=['DELETE'])
def remove_from_cart(cart_id):
    session, auth_error = get_authenticated_session(required_role='buyer')
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT buyer_id FROM buyer WHERE email = %s", (session['user_email'],))
        buyer = cursor.fetchone()
        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404

        cursor.execute("DELETE FROM cart WHERE cart_id = %s AND buyer_id = %s", (cart_id, buyer[0]))
        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Cart item not found for this account."}), 404
        conn.commit()
        return jsonify({"message": "Item removed from cart"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 10: PROCESS CHECKOUT (Buyer Dashboard)
# ----------------------------------------------------
@app.route('/api/checkout', methods=['POST'])
def process_checkout():
    session, auth_error = get_authenticated_session(required_role='buyer')
    if auth_error:
        return auth_error

    data = request.json or {}
    buyer_name = session['user_name']
    buyer_email = session['user_email']
    delivery_address = (data.get('deliveryAddress') or '').strip()
    phone_number = (data.get('phoneNumber') or '').strip()

    if not delivery_address or not phone_number:
        return jsonify({"error": "All checkout details are required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Buyer ID and email
        cursor.execute("SELECT buyer_id, email FROM buyer WHERE email = %s", (buyer_email,))
        buyer = cursor.fetchone()
        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404
        buyer_id = buyer[0]
        buyer_email = buyer[1]

        # 2. Fetch all items currently in this buyer's cart
        cursor.execute("""
            SELECT c.produce_id, c.quantity, p.price_per_unit 
            FROM cart c
            JOIN produce p ON c.produce_id = p.produce_id
            WHERE c.buyer_id = %s
        """, (buyer_id,))
        cart_items = cursor.fetchall()

        if not cart_items:
            return jsonify({"error": "Your cart is empty!"}), 400

        # Calculate the Grand Total
        grand_total = sum(item[1] * item[2] for item in cart_items)

        # 3. Create the Master Order in the `orders` table
        cursor.execute("""
            INSERT INTO orders (buyer_id, order_status, total_amount)
            VALUES (%s, 'Pending', %s) RETURNING order_id
        """, (buyer_id, grand_total))
        order_id = cursor.fetchone()[0]

        # 4. Move items from Cart to `order_details` and keep delivery address and phone per line item
        for item in cart_items:
            produce_id, quantity, price = item

            # Lock the produce row and validate available stock before creating order lines.
            cursor.execute(
                "SELECT stock_quantity FROM produce WHERE produce_id = %s FOR UPDATE",
                (produce_id,)
            )
            stock_row = cursor.fetchone()
            if not stock_row:
                return jsonify({"error": f"Produce item #{produce_id} was not found."}), 404

            available_stock = float(stock_row[0] or 0)
            if available_stock < quantity:
                return jsonify({"error": f"Insufficient stock for item #{produce_id}. Available: {available_stock}, requested: {quantity}."}), 409

            cursor.execute(
                "UPDATE produce SET stock_quantity = stock_quantity - %s WHERE produce_id = %s",
                (quantity, produce_id)
            )

            subtotal = quantity * price
            cursor.execute("""
                INSERT INTO order_details (
                    order_id,
                    produce_id,
                    quantity,
                    price_at_time_of_order,
                    subtotal,
                    delivery_address,
                    buyer_phone
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (order_id, produce_id, quantity, price, subtotal, delivery_address, phone_number))
        # 5. Create a Pending Payment record
        cursor.execute("""
            INSERT INTO payments (order_id, amount, payment_method, payment_status)
            VALUES (%s, %s, 'M-Pesa (Pending)', 'Pending')
            RETURNING payment_id
        """, (order_id, grand_total))
        payment_id = cursor.fetchone()[0]

        # 6. Wipe the user's cart clean!
        cursor.execute("DELETE FROM cart WHERE buyer_id = %s", (buyer_id,))

        # Save all these steps to the database at the exact same time
        conn.commit()

        return jsonify({
            "message": f"Order #{order_id} placed successfully! Total: KSh {grand_total}. Complete payment via M-Pesa prompt.",
            "orderId": order_id,
            "paymentId": payment_id,
            "totalAmount": float(grand_total)
        }), 201

    except Exception as e:
        # If any of the steps above fail, cancel ALL of them so the database doesn't break
        conn.rollback() 
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


def parse_callback(payload):
    """
    Parse Safaricom STK callback JSON into one consistent shape.

    Why this exists:
    - Safaricom callback payloads are nested and can vary slightly in structure.
    - The rest of the code should not care about nesting details.

    Returned keys:
    - checkout_id: CheckoutRequestID sent from STK initiation.
    - merchant_id: MerchantRequestID from Daraja.
    - result_code/result_desc: callback outcome code and message.
    - success: True only when result_code == 0.
    - mpesa_code: receipt number (MpesaReceiptNumber) when payment succeeds.
    """
    stk_callback = (payload.get("Body") or {}).get("stkCallback") or payload.get("stkCallback") or {}
    metadata_items = ((stk_callback.get("CallbackMetadata") or {}).get("Item") or [])
    metadata_map = {}
    for item in metadata_items:
        name = item.get("Name")
        if name:
            metadata_map[name] = item.get("Value")

    raw_result_code = stk_callback.get("ResultCode")
    try:
        result_code = int(raw_result_code)
    except (TypeError, ValueError):
        result_code = raw_result_code

    return {
        "checkout_id": stk_callback.get("CheckoutRequestID", ""),
        "merchant_id": stk_callback.get("MerchantRequestID", ""),
        "result_code": result_code,
        "result_desc": stk_callback.get("ResultDesc", ""),
        "success": result_code == 0,
        "mpesa_code": str(metadata_map.get("MpesaReceiptNumber") or "")
    }


# ----------------------------------------------------
# ROUTE 10A: INITIATE M-PESA STK PUSH
# ----------------------------------------------------
@app.route('/api/mpesa-pay', methods=['POST'])
def mpesa_pay():
    """
    Start an STK push for a buyer's order and persist CheckoutRequestID.

    Flow:
    1) Authenticate buyer session from token.
    2) Validate order ownership and amount.
    3) Trigger Daraja STK push.
    4) Save CheckoutRequestID in payments.transaction_reference.

    The saved CheckoutRequestID is critical because Safaricom callback uses it
    as the primary correlation key. Without this persistence, callback matching
    would fail or become unreliable.
    """
    session, auth_error = get_authenticated_session(required_role='buyer')
    if auth_error:
        return auth_error

    data = request.json or {}
    order_id = data.get('orderId')
    phone_number = (data.get('phoneNumber') or '').strip()

    if not order_id or not phone_number:
        return jsonify({"error": "orderId and phoneNumber are required."}), 400

    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return jsonify({"error": "orderId must be a valid number."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT buyer_id FROM buyer WHERE email = %s", (session['user_email'],))
        buyer_row = cursor.fetchone()
        if not buyer_row:
            return jsonify({"error": "Buyer account not found."}), 404

        buyer_id = buyer_row[0]
        cursor.execute(
            """
            SELECT total_amount, order_status
            FROM orders
            WHERE order_id = %s AND buyer_id = %s
            """,
            (order_id, buyer_id)
        )
        order_row = cursor.fetchone()
        if not order_row:
            return jsonify({"error": "Order not found for this account."}), 404

        amount = float(order_row[0] or 0)
        if amount <= 0:
            return jsonify({"error": "Order amount is invalid for payment initiation."}), 400

        cursor.execute(
            """
            SELECT payment_id
            FROM payments
            WHERE order_id = %s
            ORDER BY payment_id DESC
            LIMIT 1
            """,
            (order_id,)
        )
        payment_row = cursor.fetchone()
        if not payment_row:
            return jsonify({"error": "Payment record not found for this order."}), 404

        payment_id = payment_row[0]
        account_reference = f"Order{order_id}"
        stk_response = _initiate_mpesa_stk_push(
            phone_number=phone_number,
            amount=amount,
            account_reference=account_reference,
            transaction_desc=f"MkulimaDirect Order #{order_id}"
        )

        checkout_request_id = stk_response.get('CheckoutRequestID', '')
        if not checkout_request_id:
            conn.rollback()
            return jsonify({
                "error": "M-Pesa did not return CheckoutRequestID.",
                "mpesaResponse": stk_response
            }), 502

        cursor.execute(
            """
            UPDATE payments
            SET transaction_reference = %s,
                payment_method = 'M-Pesa (STK Push)',
                payment_status = 'Pending'
            WHERE payment_id = %s
            """,
            (checkout_request_id, payment_id)
        )

        conn.commit()
        return jsonify({
            "message": "M-Pesa STK push initiated. Complete payment on your phone.",
            "orderId": order_id,
            "paymentId": payment_id,
            "checkoutRequestID": checkout_request_id,
            "merchantRequestID": stk_response.get('MerchantRequestID', ''),
            "customerMessage": stk_response.get('CustomerMessage', '')
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"M-Pesa initiation failed: {str(e)}"}), 500
    finally:
        if cursor:
            cursor.close()
        conn.close()


# ----------------------------------------------------
# ROUTE 10B: M-PESA CALLBACK
# ----------------------------------------------------
@app.route('/api/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """
    Receive STK callback and finalize payment/order/wallet updates.

    Success path (ResultCode = 0):
    - mark payment Completed
    - replace transaction_reference with Mpesa receipt (if present)
    - mark order Confirmed
    - credit each farmer's wallet based on order_details subtotals
    - insert wallet credit transactions for auditability

    Failure path:
    - mark payment Failed using CheckoutRequestID lookup

    Callback always returns HTTP 200 with accepted payload response so Daraja
    does not keep retrying due to transient app-side processing issues.
    """
    callback_data = request.json or {}
    result = parse_callback(callback_data)

    conn = get_db_connection()
    if not conn:
        print("Callback received but DB connection failed.")
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200

    cursor = None
    try:
        cursor = conn.cursor()

        if result["success"]:
            # 1. Confirm the payment record and get the order_id
            cursor.execute("""
                UPDATE payments
                SET payment_status       = 'Completed',
                    transaction_reference = %s
                WHERE transaction_reference = %s
                RETURNING order_id
            """, (result["mpesa_code"] or result["checkout_id"], result["checkout_id"]))

            row = cursor.fetchone()
            if row:
                order_id = row[0]

                # 2. Confirm the order
                cursor.execute("""
                    UPDATE orders SET order_status = 'Confirmed'
                    WHERE order_id = %s
                """, (order_id,))

                # 3. Credit each farmer's wallet for their items in this order
                cursor.execute("""
                    SELECT p.farmer_id, SUM(od.subtotal) as farmer_earnings
                    FROM order_details od
                    JOIN produce p ON od.produce_id = p.produce_id
                    WHERE od.order_id = %s
                    GROUP BY p.farmer_id
                """, (order_id,))

                farmer_earnings = cursor.fetchall()

                for farmer_id, earnings in farmer_earnings:
                    # Credit the wallet balance
                    cursor.execute("""
                        INSERT INTO farmer_wallet (farmer_id, balance)
                        VALUES (%s, %s)
                        ON CONFLICT (farmer_id)
                        DO UPDATE SET
                            balance    = farmer_wallet.balance + EXCLUDED.balance,
                            updated_at = CURRENT_TIMESTAMP
                    """, (farmer_id, earnings))

                    # Record the credit transaction
                    cursor.execute("""
                        INSERT INTO wallet_transactions
                            (farmer_id, type, amount, reference, status, description)
                        VALUES (%s, 'credit', %s, %s, 'Completed', %s)
                    """, (
                        farmer_id,
                        earnings,
                        result["mpesa_code"] or result["checkout_id"],
                        f"Payment received for Order #{order_id}"
                    ))

                print(f"Payment confirmed for order {order_id}. Receipt: {result['mpesa_code']}")
        else:
            # Payment failed - mark as Failed
            cursor.execute("""
                UPDATE payments SET payment_status = 'Failed'
                WHERE transaction_reference = %s
            """, (result["checkout_id"],))
            print(f"Payment failed. Code: {result['result_code']} - {result['result_desc']}")

        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"Callback processing error: {e}")
    finally:
        if cursor:
            cursor.close()
        conn.close()

    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"}), 200
# ----------------------------------------------------
# ROUTE 11: FARMER DASHBOARD STATS (UPGRADED)
# ----------------------------------------------------
@app.route('/api/farmer/stats/<farmer_name>', methods=['GET'])
def get_farmer_stats(farmer_name):
    session, auth_error = get_authenticated_session(required_role='farmer')
    if auth_error:
        return auth_error

    if farmer_name != session['user_name']:
        return jsonify({"error": "Forbidden. You can only access your own dashboard."}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Farmer ID
        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (session['user_email'],))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404
        farmer_id = farmer[0]

        # 2. Count Active Listings
        cursor.execute("SELECT COUNT(*) FROM produce WHERE farmer_id = %s", (farmer_id,))
        active_listings = cursor.fetchone()[0]

        # 3. Calculate Total Earnings
        cursor.execute("""
            SELECT COALESCE(SUM(od.subtotal), 0)
            FROM order_details od
            JOIN produce p ON od.produce_id = p.produce_id
            JOIN orders o ON od.order_id = o.order_id
            WHERE p.farmer_id = %s AND o.order_status != 'Cancelled'
        """, (farmer_id,))
        total_earnings = cursor.fetchone()[0]

        # 4. Count Pending Orders
        cursor.execute("""
            SELECT COUNT(DISTINCT o.order_id)
            FROM orders o
            JOIN order_details od ON o.order_id = od.order_id
            JOIN produce p ON od.produce_id = p.produce_id
            WHERE p.farmer_id = %s AND o.order_status = 'Pending'
        """, (farmer_id,))
        pending_orders = cursor.fetchone()[0]

        # 5. NEW: Calculate Average Rating and Total Reviews
        cursor.execute("""
            SELECT COALESCE(AVG(rating_value), 0), COUNT(rating_value)
            FROM farmer_ratings
            WHERE farmer_id = %s
        """, (farmer_id,))
        rating_data = cursor.fetchone()
        avg_rating = round(float(rating_data[0]), 1) # Rounds to 1 decimal place (e.g., 4.5)
        total_reviews = rating_data[1]

        return jsonify({
            "totalEarnings": float(total_earnings),
            "activeListings": active_listings,
            "pendingOrders": pending_orders,
            "averageRating": avg_rating,
            "totalReviews": total_reviews
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 12: FARMER ORDER HISTORY
# ----------------------------------------------------
@app.route('/api/farmer/orders/<farmer_name>', methods=['GET'])
def get_farmer_orders(farmer_name):
    session, auth_error = get_authenticated_session(required_role='farmer')
    if auth_error:
        return auth_error

    if farmer_name != session['user_name']:
        return jsonify({"error": "Forbidden. You can only access your own orders."}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Farmer ID
        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (session['user_email'],))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404
        farmer_id = farmer[0]

        # 2. Fetch ALL order details linked to this farmer's produce
        query = """
            SELECT 
                o.order_id, 
                TO_CHAR(o.order_date, 'YYYY-MM-DD HH24:MI'), 
                p.name, 
                od.quantity, 
                p.unit_type,
                od.subtotal, 
                b.full_name, 
                COALESCE(od.buyer_phone, b.email), 
                o.order_status
            FROM order_details od
            JOIN produce p ON od.produce_id = p.produce_id
            JOIN orders o ON od.order_id = o.order_id
            JOIN buyer b ON o.buyer_id = b.buyer_id
            WHERE p.farmer_id = %s
            ORDER BY o.order_date DESC;
        """
        cursor.execute(query, (farmer_id,))
        orders = cursor.fetchall()

        order_list = []
        for order in orders:
            order_list.append({
                "orderId": order[0],
                "date": order[1],
                "produceName": order[2],
                "quantity": order[3],
                "unit": order[4],
                "subtotal": order[5],
                "buyerName": order[6],
                "buyerPhone": order[7],
                "status": order[8]
            })

        return jsonify(order_list), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/farmer/orders/<farmer_name>/<int:order_id>/status', methods=['PUT'])
def update_farmer_order_status(farmer_name, order_id):
    session, auth_error = get_authenticated_session(required_role='farmer')
    if auth_error:
        return auth_error

    if farmer_name != session['user_name']:
        return jsonify({"error": "Forbidden. You can only update your own orders."}), 403

    data = request.json or {}
    requested_status = (data.get('status') or '').strip().lower()

    allowed_status_map = {
        'pending': 'Pending',
        'delivered': 'Delivered',
        'cancelled': 'Cancelled',
        'completed': 'Completed'
    }

    if requested_status not in allowed_status_map:
        return jsonify({"error": "Invalid status. Allowed: pending, delivered, cancelled, completed."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (session['user_email'],))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404
        farmer_id = farmer[0]

        # Ensure this order belongs to at least one produce item owned by the farmer.
        cursor.execute("""
            SELECT o.order_status,
                   COALESCE(SUM(od.subtotal), 0) AS farmer_order_amount
            FROM orders o
            JOIN order_details od ON o.order_id = od.order_id
            JOIN produce p ON od.produce_id = p.produce_id
            WHERE o.order_id = %s AND p.farmer_id = %s
            GROUP BY o.order_status
            LIMIT 1
        """, (order_id, farmer_id))
        order_row = cursor.fetchone()

        if not order_row:
            return jsonify({"error": "Order not found for this farmer."}), 404

        current_status = (order_row[0] or '').strip().lower()
        farmer_order_amount = order_row[1] or 0

        new_status = allowed_status_map[requested_status]

        # Wallet reconciliation for cancellation:
        # if status transitions into Cancelled, reverse this farmer's credited earnings.
        if requested_status == 'cancelled' and current_status != 'cancelled' and farmer_order_amount > 0:
            cursor.execute("""
                INSERT INTO farmer_wallet (farmer_id, balance)
                VALUES (%s, %s)
                ON CONFLICT (farmer_id)
                DO UPDATE SET
                    balance = farmer_wallet.balance - %s,
                    updated_at = CURRENT_TIMESTAMP
            """, (farmer_id, 0, farmer_order_amount))

            cursor.execute("""
                INSERT INTO wallet_transactions
                    (farmer_id, type, amount, reference, status, description)
                VALUES (%s, 'debit', %s, %s, 'Completed', %s)
            """, (
                farmer_id,
                farmer_order_amount,
                f"ORDER-{order_id}",
                f"Wallet reconciliation for cancelled Order #{order_id}"
            ))

        cursor.execute(
            "UPDATE orders SET order_status = %s WHERE order_id = %s",
            (new_status, order_id)
        )

        conn.commit()
        return jsonify({"message": "Order status updated successfully.", "orderId": order_id, "status": new_status}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/farmer/wallet/<farmer_name>', methods=['GET'])
def get_wallet(farmer_name):
    session, auth_error = get_authenticated_session(required_role='farmer')
    if auth_error:
        return auth_error

    if farmer_name != session['user_name']:
        return jsonify({"error": "Forbidden. You can only access your own wallet."}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # Resolve authenticated farmer id via unique session email.
        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (session['user_email'],))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404

        farmer_id = farmer[0]

        # Keep wallet balance aligned with dashboard earnings logic.
        cursor.execute("""
            SELECT COALESCE(SUM(od.subtotal), 0)
            FROM order_details od
            JOIN produce p ON od.produce_id = p.produce_id
            JOIN orders o ON od.order_id = o.order_id
            WHERE p.farmer_id = %s AND o.order_status != 'Cancelled'
        """, (farmer_id,))
        earnings_row = cursor.fetchone()
        balance = float(earnings_row[0]) if earnings_row else 0.0

        # Wallet transactions (most recent first).
        cursor.execute("""
            SELECT
                TO_CHAR(created_at, 'YYYY-MM-DD HH24:MI:SS') AS date,
                type,
                amount,
                status,
                reference,
                description
            FROM wallet_transactions
            WHERE farmer_id = %s
            ORDER BY created_at DESC
        """, (farmer_id,))

        transactions = [
            {
                "date": r[0],
                "type": r[1],
                "amount": str(r[2]),
                "status": r[3],
                "reference": r[4],
                "description": r[5]
            }
            for r in cursor.fetchall()
        ]

        return jsonify({"balance": balance, "transactions": transactions}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


# ----------------------------------------------------
# ROUTE 13: BUYER ORDER HISTORY
# ----------------------------------------------------
@app.route('/api/buyer/orders/<buyer_name>', methods=['GET'])
def get_buyer_orders(buyer_name):
    session, auth_error = get_authenticated_session(required_role='buyer')
    if auth_error:
        return auth_error

    if buyer_name != session['user_name']:
        return jsonify({"error": "Forbidden. You can only access your own order history."}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()

        # 1. Get the Buyer ID
        cursor.execute("SELECT buyer_id FROM buyer WHERE email = %s", (session['user_email'],))
        buyer = cursor.fetchone()
        if not buyer:
            return jsonify({"error": "Buyer not found."}), 404
        buyer_id = buyer[0]

        # 2. Fetch ALL order details linked to this buyer
        # We join with the farmer table so the buyer knows who to contact for delivery!
        query = """
            SELECT 
                o.order_id, 
                TO_CHAR(o.order_date, 'YYYY-MM-DD HH24:MI'), 
                p.name, 
                od.quantity, 
                p.unit_type,
                od.subtotal, 
                f.full_name, 
                f.email, 
                o.order_status
            FROM orders o
            JOIN order_details od ON o.order_id = od.order_id
            JOIN produce p ON od.produce_id = p.produce_id
            JOIN farmer f ON p.farmer_id = f.farmer_id
            WHERE o.buyer_id = %s
            ORDER BY o.order_date DESC;
        """
        cursor.execute(query, (buyer_id,))
        orders = cursor.fetchall()

        order_list = []
        for order in orders:
            order_list.append({
                "orderId": order[0],
                "date": order[1],
                "produceName": order[2],
                "quantity": order[3],
                "unit": order[4],
                "subtotal": order[5],
                "farmerName": order[6],
                "farmerPhone": order[7],
                "status": order[8]
            })

        return jsonify(order_list), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 15: RATE A FARMER (Buyer Dashboard)
# ----------------------------------------------------
@app.route('/api/rate', methods=['POST'])
def rate_farmer():
    session, auth_error = get_authenticated_session(required_role='buyer')
    if auth_error:
        return auth_error

    data = request.json
    farmer_name = data.get('farmerName')
    farmer_email = (data.get('farmerEmail') or '').strip()
    rating = data.get('rating')

    if not all([farmer_name, farmer_email, rating]):
        return jsonify({"error": "Missing rating data"}), 400

    try:
        rating_val = int(rating)
        if rating_val < 1 or rating_val > 5:
            return jsonify({"error": "Rating must be between 1 and 5."}), 400
    except ValueError:
        return jsonify({"error": "Invalid rating number."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        
        # 1. Resolve buyer identity via unique session email.
        cursor.execute("SELECT buyer_id FROM buyer WHERE email = %s", (session['user_email'],))
        buyer = cursor.fetchone()

        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (farmer_email,))
        farmer = cursor.fetchone()

        if not buyer or not farmer:
            return jsonify({"error": "Could not find buyer or farmer in the database."}), 404
            
        b_id, f_id = buyer[0], farmer[0]

        # 2. Insert the rating (or update it if they already rated this farmer)
        cursor.execute("""
            INSERT INTO farmer_ratings (farmer_id, buyer_id, rating_value)
            VALUES (%s, %s, %s)
            ON CONFLICT (farmer_id, buyer_id) 
            DO UPDATE SET rating_value = EXCLUDED.rating_value;
        """, (f_id, b_id, rating_val))
        
        conn.commit()
        return jsonify({"message": f"Success! You rated {farmer_name} {rating_val}/5."}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()
# ----------------------------------------------------
# ROUTE 17: GET SPECIFIC FARMER'S PRODUCE
# ----------------------------------------------------
@app.route('/api/farmer/produce/<farmer_name>', methods=['GET'])
def get_farmer_inventory(farmer_name):
    session, auth_error = get_authenticated_session(required_role='farmer')
    if auth_error:
        return auth_error

    if farmer_name != session['user_name']:
        return jsonify({"error": "Forbidden. You can only access your own inventory."}), 403

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        
        # Get Farmer ID using unique authenticated email.
        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (session['user_email'],))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404
            
        # Fetch their produce
        cursor.execute("""
                SELECT produce_id, name, description, price_per_unit, stock_quantity, unit_type, TO_CHAR(listed_date, 'YYYY-MM-DD')
            FROM produce WHERE farmer_id = %s ORDER BY listed_date DESC
        """, (farmer[0],))
        
        produce_records = cursor.fetchall()
        
        inventory = []
        for p in produce_records:
            inventory.append({
                "id": p[0], "name": p[1], "description": p[2], 
                "price": float(p[3]), "stock": p[4], "unit": p[5], "date": p[6]
            })
            
        return jsonify(inventory), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# ROUTE 18: DELETE PRODUCE LISTING
# ----------------------------------------------------
@app.route('/api/produce/<int:produce_id>', methods=['DELETE'])
def delete_produce(produce_id):
    session, auth_error = get_authenticated_session(required_role='farmer')
    if auth_error:
        return auth_error

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (session['user_email'],))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404

        cursor.execute(
            "DELETE FROM produce WHERE produce_id = %s AND farmer_id = %s",
            (produce_id, farmer[0])
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Produce not found for this account."}), 404
        conn.commit()
        return jsonify({"message": "Produce deleted."}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/farmer/update_stock', methods=['POST'])
def update_stock():
    session, auth_error = get_authenticated_session(required_role='farmer')
    if auth_error:
        return auth_error

    data = request.json or {}
    produce_id = data.get('produceId')
    new_stock = data.get('newStock')

    if produce_id is None or new_stock is None:
        return jsonify({"error": "produceId and newStock are required."}), 400

    try:
        produce_id = int(produce_id)
        new_stock = float(new_stock)
        if new_stock < 0:
            return jsonify({"error": "Stock cannot be negative."}), 400
    except (TypeError, ValueError):
        return jsonify({"error": "produceId and newStock must be valid numbers."}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT farmer_id FROM farmer WHERE email = %s", (session['user_email'],))
        farmer = cursor.fetchone()
        if not farmer:
            return jsonify({"error": "Farmer not found."}), 404

        cursor.execute(
            "UPDATE produce SET stock_quantity = %s WHERE produce_id = %s AND farmer_id = %s",
            (new_stock, produce_id, farmer[0])
        )

        if cursor.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Produce item not found."}), 404

        conn.commit()
        return jsonify({"message": "Stock updated"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/complaints', methods=['GET'])
def get_complaints():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_name, user_role, subject, description, status FROM complaints ORDER BY created_at DESC")
        complaints = [
            {
                "userName": r[0],
                "userRole": r[1],
                "subject": r[2],
                "description": r[3],
                "status": r[4]
            }
            for r in cursor.fetchall()
        ]
        return jsonify(complaints)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/ratings', methods=['GET'])
def get_ratings():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT f.full_name, b.full_name, r.rating_value, r.created_at
            FROM farmer_ratings r
            JOIN farmer f ON r.farmer_id = f.farmer_id
            JOIN buyer b ON r.buyer_id = b.buyer_id
            ORDER BY r.created_at DESC
        """)
        ratings = [
            {
                "farmerName": r[0],
                "buyerName": r[1],
                "ratingValue": r[2],
                "date": r[3].strftime("%Y-%m-%d") if r[3] else "Unknown"
            }
            for r in cursor.fetchall()
        ]
        return jsonify(ratings)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/payments', methods=['GET'])
def get_payments():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.order_id, b.full_name, o.total_amount, o.order_date, o.order_status
            FROM orders o
            JOIN buyer b ON o.buyer_id = b.buyer_id
            ORDER BY o.order_date DESC
        """)
        payments = [
            {
                "orderId": r[0],
                "buyerName": r[1],
                "amount": str(r[2]),
                "date": r[3].strftime("%Y-%m-%d") if r[3] else "Unknown",
                "status": r[4]
            }
            for r in cursor.fetchall()
        ]
        return jsonify(payments)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/report/<report_type>', methods=['GET'])
def get_admin_report(report_type):
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        normalized = (report_type or '').strip().lower()

        if normalized not in {'performance', 'revenue', 'activity'}:
            return jsonify({"error": "Unknown report type."}), 400

        # Daily performance view: total orders and total revenue per day.
        cursor.execute("""
            SELECT DATE(order_date) AS day,
                   COUNT(order_id) AS total_orders,
                   COALESCE(SUM(total_amount), 0) AS daily_revenue
            FROM orders
            GROUP BY DATE(order_date)
            ORDER BY day DESC
        """)

        report_rows = [
            {
                "day": r[0].strftime("%Y-%m-%d") if r[0] else "Unknown",
                "totalOrders": int(r[1]) if r[1] is not None else 0,
                "dailyRevenue": str(r[2])
            }
            for r in cursor.fetchall()
        ]
        return jsonify(report_rows), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/admin/audit', methods=['GET'])
def get_audit_logs():
    _admin, auth_error = get_admin_authorization()
    if auth_error:
        return auth_error

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_name, user_role, login_time, logout_time, session_duration_minutes
            FROM audit_logs
            ORDER BY login_time DESC
        """)

        logs = [
            {
                "userName": row[0],
                "userRole": row[1],
                "login": row[2].strftime("%Y-%m-%d %H:%M:%S") if row[2] else "Unknown",
                "logout": row[3].strftime("%Y-%m-%d %H:%M:%S") if row[3] else None,
                "duration": float(row[4]) if row[4] is not None else 0
            }
            for row in cursor.fetchall()
        ]

        return jsonify(logs), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ----------------------------------------------------
# START THE SERVER
# ----------------------------------------------------
if __name__ == '__main__':
    print("Mkulima Direct API is running on http://localhost:5000")
    app.run(debug=True, port=5000)