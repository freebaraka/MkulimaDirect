import os
import json
import base64
from urllib import request as urlrequest


def _load_env():
    """Load .env values (root then backend) without overriding existing env vars."""
    backend_dir = os.path.dirname(__file__)
    project_root = os.path.dirname(backend_dir)
    for env_path in (os.path.join(project_root, '.env'), os.path.join(backend_dir, '.env')):
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, 'r', encoding='utf-8') as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            pass


_load_env()

# Core Daraja credentials for OAuth/STK/B2C calls.
MPESA_CONSUMER_KEY = (os.getenv('MPESA_CONSUMER_KEY') or '').strip()
MPESA_CONSUMER_SECRET = (os.getenv('MPESA_CONSUMER_SECRET') or '').strip()
MPESA_SHORTCODE = (os.getenv('MPESA_SHORTCODE') or '').strip()
MPESA_BASE_URL = (os.getenv('MPESA_BASE_URL') or 'https://sandbox.safaricom.co.ke').strip()

# B2C-specific credentials and callback endpoints.
MPESA_B2C_INITIATOR = (os.getenv('MPESA_B2C_INITIATOR_NAME') or '').strip()
MPESA_B2C_CREDENTIAL = (os.getenv('MPESA_B2C_SECURITY_CREDENTIAL') or '').strip()
MPESA_B2C_RESULT_URL = (os.getenv('MPESA_B2C_RESULT_URL') or '').strip()
MPESA_B2C_TIMEOUT_URL = (os.getenv('MPESA_B2C_QUEUE_TIMEOUT_URL') or '').strip()


def get_access_token() -> str:
    """Fetch a short-lived Daraja OAuth access token for authenticated API calls."""
    if not MPESA_CONSUMER_KEY or not MPESA_CONSUMER_SECRET:
        raise ValueError('Missing MPESA_CONSUMER_KEY/MPESA_CONSUMER_SECRET in environment.')

    credentials = f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}".encode('utf-8')
    auth_b64 = base64.b64encode(credentials).decode('utf-8')

    req = urlrequest.Request(
        f"{MPESA_BASE_URL}/oauth/v1/generate?grant_type=client_credentials",
        headers={'Authorization': f'Basic {auth_b64}'},
        method='GET'
    )

    with urlrequest.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode('utf-8'))

    token = data.get('access_token', '')
    if not token:
        raise RuntimeError('Failed to obtain Daraja access token.')
    return token


def initiate_b2c_payment(phone: str, amount: int, remarks: str = 'Mkulima Payout') -> dict:
    """
    Sends money FROM your M-Pesa business account TO a farmer's phone.
    Used for farmer wallet withdrawals.

    Expected phone format at this layer: 2547XXXXXXXX.
    Validation and normalization are done by route layer before calling this.

    Returns:
    - Daraja JSON response dict (contains ConversationID/ResponseDescription)
    """
    if not MPESA_B2C_INITIATOR or not MPESA_B2C_CREDENTIAL:
        raise ValueError(
            'B2C is not configured. Set MPESA_B2C_INITIATOR_NAME and '
            'MPESA_B2C_SECURITY_CREDENTIAL in your .env file.'
        )

    token = get_access_token()

    payload = {
        'InitiatorName': MPESA_B2C_INITIATOR,
        'SecurityCredential': MPESA_B2C_CREDENTIAL,
        'CommandID': 'BusinessPayment',
        'Amount': int(amount),
        'PartyA': MPESA_SHORTCODE,
        'PartyB': str(phone),
        'Remarks': remarks[:100],
        'QueueTimeOutURL': MPESA_B2C_TIMEOUT_URL,
        'ResultURL': MPESA_B2C_RESULT_URL,
        'Occasion': 'FarmerPayout'
    }

    req = urlrequest.Request(
        f"{MPESA_BASE_URL}/mpesa/b2c/v1/paymentrequest",
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        },
        method='POST'
    )

    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        raise RuntimeError(f'B2C request failed: {exc}') from exc
