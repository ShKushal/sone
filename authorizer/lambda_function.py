import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request

VALID_API_KEY = os.environ.get('API_KEY',      '')
USER_POOL_ID  = os.environ.get('USER_POOL_ID', '')
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

# -------------------------------------------------------
# Who may call which route — for people signing in with a token.
# A call with the API key is trusted on every route and never looks at this table.
# A route that is not in this table is refused for token callers (so a new route is closed until
# it is listed here). The group names are the portal groups; users-api repeats the user rules
# and adds the finer ones (own account, which fields) that need the request body.
# -------------------------------------------------------
SUPER_ADMIN = 'SUPER_ADMIN'
ADMIN_ONLY  = frozenset({SUPER_ADMIN})
HELPDESK    = frozenset({'HELPDESK',      SUPER_ADMIN})
REPORTS     = frozenset({'REPORTS_ADMIN', SUPER_ADMIN})
EMAIL_LOGS  = frozenset({'EMAIL_LOGS',    SUPER_ADMIN})

ROUTE_GROUPS = {
    # users
    'GET /users':                              HELPDESK,
    'GET /users/{username}':                   HELPDESK,
    'GET /users/{username}/email-logs':        HELPDESK,
    'GET /users/{username}/groups':            HELPDESK,
    'PUT /users/{username}':                   HELPDESK,
    'POST /users/{username}/reset-password':   HELPDESK,
    'POST /users/{username}/email-unblock':    HELPDESK,
    'PUT /users/{username}/groups':            ADMIN_ONLY,
    'POST /users':                             ADMIN_ONLY,
    'POST /users/bulk':                        ADMIN_ONLY,
    'DELETE /users/{username}':                ADMIN_ONLY,
    # email logs for all recipients
    'GET /email-logs':                         EMAIL_LOGS,
    # reports (including the temporary ForgeRock ones)
    'GET /reports/users':                      REPORTS,
    'GET /reports/users/summary':              REPORTS,
    'GET /reports/inactive':                   REPORTS,
    'GET /reports/organisations':              REPORTS,
    'GET /reports/migration':                  REPORTS,
    'GET /reports/migration/pending':          REPORTS,
    'GET /reports/migration/complete':         REPORTS,
    'GET /reports/migration/summary':          REPORTS,
    'GET /reports/forgerock/organisations':    REPORTS,
    'GET /reports/forgerock/gap':              REPORTS,
    # broker firms: reading for the reports group, every change for super admin only
    'GET /broker-firms':                       REPORTS,
    'GET /broker-firms/{brokerId}':            REPORTS,
    'POST /broker-firms':                      ADMIN_ONLY,
    'POST /broker-firms/bulk':                 ADMIN_ONLY,
    'PUT /broker-firms/{brokerId}':            ADMIN_ONLY,
    'DELETE /broker-firms/{brokerId}':         ADMIN_ONLY,
}

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
    # Cognito lists the user's groups in the token; anything that is not a list of names counts as no groups
    raw_groups = payload.get('cognito:groups')
    groups     = [g for g in raw_groups if isinstance(g, str)] if isinstance(raw_groups, list) else []
    return { 'valid': True, 'principal': principal, 'groups': groups }


def lambda_handler(event, context):
    # Never log the whole event: it carries the Authorization token and the x-api-key value.
    route = event.get('routeKey') or '?'
    print(f'[AUTH] {route}')

    headers     = { str(k).lower(): v for k, v in (event.get('headers') or {}).items() }
    api_key     = headers.get('x-api-key', '')
    auth_header = headers.get('authorization', '')

    # React app — x-api-key: jwt + Bearer token
    if api_key == 'jwt' and auth_header.startswith('Bearer '):
        result = validate_jwt(auth_header[7:])
        if not result['valid']:
            print(f"[AUTH] JWT invalid: {result['error']}")
            return deny()

        # a valid token is not enough: the person must hold a group this route allows
        needed = ROUTE_GROUPS.get(route)
        if needed is None:
            print(f"[AUTH] Refused {result['principal']}: no rule for route {route}")
            return deny()
        if not needed.intersection(result['groups']):
            print(f"[AUTH] Refused {result['principal']} on {route}: needs one of {sorted(needed)}")
            return deny()

        print(f"[AUTH] JWT valid for: {result['principal']}")
        return allow(result['principal'], 'jwt', result['groups'])

    # Postman / backend — the real API key. Trusted on every route: the route table does not apply.
    if api_key and VALID_API_KEY and hmac.compare_digest(api_key.encode('utf-8'), VALID_API_KEY.encode('utf-8')):
        print('[AUTH] API key valid')
        return allow('api-key-client', 'api-key')

    print('[AUTH] No valid credentials')
    return deny()


def allow(principal, auth_type, groups=()):
    return {
        'isAuthorized': True,
        'context': {
            'apiKey':    'valid',
            'principal': principal,
            'authType':  auth_type,
            'groups':    ','.join(sorted(groups))     # verified groups of a token caller; empty for the API key
        }
    }


def deny():
    # A clean 403. Raising instead is reported as a server error (500) by HTTP APIs
    # and shows up as a failure in the authorizer's error metrics.
    return { 'isAuthorized': False }
