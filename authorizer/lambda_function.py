import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request

VALID_API_KEY = os.environ.get('API_KEY',      '')
USER_POOL_ID  = os.environ.get('USER_POOL_ID', 'ap-southeast-1_vi0pVitMh')
REGION        = os.environ.get('REGION',       'ap-southeast-1')

# App client IDs allowed to call this API with a USER token (comma-separated), e.g. the admin
# portal's client only — so a token from another app client of the same user pool (such as the
# broker portal) is refused. Empty = any app client of this pool (the previous behaviour).
ALLOWED_CLIENT_IDS = {
    c.strip() for c in os.environ.get('ALLOWED_CLIENT_IDS', '').split(',') if c.strip()
}

ISSUER   = f'https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}'
JWKS_URL = f'{ISSUER}/.well-known/jwks.json'

# PKCS#1 v1.5 DigestInfo prefix for SHA-256 (RFC 8017, section 9.2)
SHA256_DIGEST_INFO = bytes.fromhex('3031300d060960864801650304020105000420')

JWKS_REFETCH_MIN_SECONDS = 60   # an unknown key id may be a rotation — but never refetch on every request

# kept across warm invocations
_jwks_cache      = None
_jwks_fetched_at = 0.0


def b64url_decode(segment):
    return base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))


def fetch_jwks():
    global _jwks_cache, _jwks_fetched_at
    _jwks_fetched_at = time.time()
    try:
        with urllib.request.urlopen(JWKS_URL, timeout=5) as response:
            _jwks_cache = json.loads(response.read().decode())
        print(f"[JWKS] Loaded {len(_jwks_cache.get('keys', []))} keys")
    except Exception as e:
        print(f'[JWKS] Fetch failed: {e}')
    return _jwks_cache


def find_key(kid):
    """Public key for this key id. An unknown id triggers (at most one per minute) refetch,
    because Cognito rotates its keys. If the key set cannot be had, there is no key —
    and no key means the token is refused (fail closed)."""
    jwks = _jwks_cache or fetch_jwks()
    for attempt in (1, 2):
        for key in (jwks or {}).get('keys', []):
            if key.get('kid') == kid:
                return key
        if attempt == 1:
            if time.time() - _jwks_fetched_at < JWKS_REFETCH_MIN_SECONDS:
                break
            jwks = fetch_jwks()
    return None


def verify_rs256(signing_input, signature, n, e):
    """RSASSA-PKCS1-v1_5 with SHA-256 (RFC 8017, section 8.2.2), standard library only.
    The WHOLE encoded block is compared — leading 00 01, the FF padding, the DigestInfo and
    the hash — not just the trailing hash."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    s = int.from_bytes(signature, 'big')
    if s >= n:
        return False
    encoded = pow(s, e, n).to_bytes(k, 'big')

    digest_info = SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    pad_len     = k - len(digest_info) - 3
    if pad_len < 8:
        return False
    expected = b'\x00\x01' + b'\xff' * pad_len + b'\x00' + digest_info
    return hmac.compare_digest(encoded, expected)


def validate_jwt(token):
    parts = token.split('.')
    if len(parts) != 3:
        return { 'valid': False, 'error': 'Invalid token structure' }

    try:
        header    = json.loads(b64url_decode(parts[0]))
        payload   = json.loads(b64url_decode(parts[1]))
        signature = b64url_decode(parts[2])
    except Exception:
        return { 'valid': False, 'error': 'Token is not valid base64url / JSON' }

    if not isinstance(header, dict) or not isinstance(payload, dict):
        return { 'valid': False, 'error': 'Invalid token structure' }

    # 1. only RS256 — never "none", never a symmetric algorithm
    if header.get('alg') != 'RS256':
        return { 'valid': False, 'error': f"Unsupported algorithm: {header.get('alg')}" }

    # 2. signature, against Cognito's public key (fail closed if the key cannot be had)
    key = find_key(header.get('kid'))
    if not key:
        return { 'valid': False, 'error': 'Signing key not found or key set unavailable' }
    try:
        n = int.from_bytes(b64url_decode(key['n']), 'big')
        e = int.from_bytes(b64url_decode(key['e']), 'big')
    except Exception:
        return { 'valid': False, 'error': 'Unusable signing key' }
    if not verify_rs256(f'{parts[0]}.{parts[1]}'.encode('utf-8'), signature, n, e):
        return { 'valid': False, 'error': 'Signature invalid' }

    # 3. claims — only trusted now that the signature checks out
    exp = payload.get('exp')
    if not isinstance(exp, (int, float)) or exp < time.time():
        return { 'valid': False, 'error': 'Token expired' }

    if payload.get('iss') != ISSUER:
        return { 'valid': False, 'error': 'Invalid issuer' }

    token_use = payload.get('token_use')
    if token_use not in ('id', 'access'):
        return { 'valid': False, 'error': 'Invalid token_use' }

    if ALLOWED_CLIENT_IDS:
        client = payload.get('aud') if token_use == 'id' else payload.get('client_id')
        if client not in ALLOWED_CLIENT_IDS:
            return { 'valid': False, 'error': 'Token was issued to a different app client' }

    principal = (
        payload.get('email') or
        payload.get('username') or
        payload.get('sub') or
        'jwt-user'
    )
    return { 'valid': True, 'principal': principal }


def lambda_handler(event, context):
    # Never log the whole event: it carries the Authorization token and the x-api-key value.
    print(f"[AUTH] {event.get('routeKey') or event.get('requestContext', {}).get('http', {}).get('path', '?')}")

    headers     = { str(k).lower(): v for k, v in (event.get('headers') or {}).items() }
    api_key     = headers.get('x-api-key', '')
    auth_header = headers.get('authorization', '')

    # React app — x-api-key: jwt + Bearer token
    if api_key == 'jwt' and auth_header.startswith('Bearer '):
        result = validate_jwt(auth_header[7:])
        if result['valid']:
            print(f"[AUTH] JWT valid for: {result['principal']}")
            return allow(result['principal'], 'jwt')
        print(f"[AUTH] JWT invalid: {result['error']}")
        return deny()

    # Postman / backend — the real API key
    if api_key and VALID_API_KEY and hmac.compare_digest(api_key.encode('utf-8'), VALID_API_KEY.encode('utf-8')):
        print('[AUTH] API key valid')
        return allow('api-key-client', 'api-key')

    print('[AUTH] No valid credentials')
    return deny()


def allow(principal, auth_type):
    return {
        'isAuthorized': True,
        'context': {
            'apiKey':    'valid',
            'principal': principal,
            'authType':  auth_type
        }
    }


def deny():
    # A clean 403. Raising instead is reported as a server error (500) by HTTP APIs
    # and shows up as a failure in the authorizer's error metrics.
    return { 'isAuthorized': False }
