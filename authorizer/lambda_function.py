import os
import json
import time
import base64
import urllib.request
import struct

VALID_API_KEY = os.environ.get('API_KEY',      '')
USER_POOL_ID  = os.environ.get('USER_POOL_ID', 'ap-southeast-1_vi0pVitMh')
REGION        = os.environ.get('REGION',       'ap-southeast-1')

JWKS_URL = f'https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json'

# cache JWKS in Lambda memory — persists across warm invocations
_jwks_cache = None

def get_jwks():
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    try:
        print(f'[JWKS] Fetching from {JWKS_URL}')
        with urllib.request.urlopen(JWKS_URL, timeout=5) as r:
            _jwks_cache = json.loads(r.read().decode())
            print(f'[JWKS] Loaded {len(_jwks_cache.get("keys", []))} keys')
            return _jwks_cache
    except Exception as e:
        print(f'[JWKS] Fetch failed: {e}')
        return None


def base64url_decode(s):
    s += '=' * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def verify_signature(token, jwks):
    """
    Verify JWT signature using Cognito public keys (RS256).
    Uses Python's built-in libraries only — no extra dependencies.
    """
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return False, 'Invalid token structure'

        header  = json.loads(base64url_decode(parts[0]))
        kid     = header.get('kid')
        alg     = header.get('alg', '')

        if alg != 'RS256':
            return False, f'Unsupported algorithm: {alg}'

        # find matching key by kid
        keys = jwks.get('keys', [])
        key  = next((k for k in keys if k.get('kid') == kid), None)

        if not key:
            return False, f'Key not found: {kid}'

        # decode RSA public key components
        n_bytes = base64url_decode(key['n'])
        e_bytes = base64url_decode(key['e'])

        n = int.from_bytes(n_bytes, 'big')
        e = int.from_bytes(e_bytes, 'big')

        # verify signature using RSA
        import hashlib

        message   = f'{parts[0]}.{parts[1]}'.encode('utf-8')
        signature = base64url_decode(parts[2])

        # RSA verify: signature^e mod n should equal hash of message
        sig_int     = int.from_bytes(signature, 'big')
        decrypted   = pow(sig_int, e, n)
        decrypted_b = decrypted.to_bytes((decrypted.bit_length() + 7) // 8, 'big')

        # PKCS1 v1.5 padding check
        # decrypted should end with SHA256 hash of message
        msg_hash = hashlib.sha256(message).digest()

        if decrypted_b[-32:] != msg_hash:
            return False, 'Signature verification failed'

        return True, 'OK'

    except Exception as e:
        return False, f'Signature error: {str(e)}'


def validate_jwt(token):
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return { 'valid': False, 'error': 'Invalid token structure' }

        payload_b64 = parts[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64).decode('utf-8'))

        print(f"[JWT] iss={payload.get('iss')} exp={payload.get('exp')} token_use={payload.get('token_use')}")

        # 1. check expiry
        if payload.get('exp', 0) < time.time():
            return { 'valid': False, 'error': 'Token expired' }

        # 2. check issuer
        expected_issuer = f'https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}'
        if payload.get('iss') != expected_issuer:
            return { 'valid': False, 'error': f"Invalid issuer" }

        # 3. check token use
        if payload.get('token_use') not in ['id', 'access']:
            return { 'valid': False, 'error': f"Invalid token_use" }

        # 4. verify signature against Cognito public keys
        jwks = get_jwks()
        if jwks:
            valid, msg = verify_signature(token, jwks)
            if not valid:
                return { 'valid': False, 'error': f'Signature invalid: {msg}' }
            print(f'[JWT] Signature verified ✅')
        else:
            print('[JWT] WARNING: JWKS unavailable — skipping signature check')

        principal = (
            payload.get('email') or
            payload.get('username') or
            payload.get('sub') or
            'jwt-user'
        )

        return { 'valid': True, 'principal': principal }

    except Exception as e:
        return { 'valid': False, 'error': str(e) }


def lambda_handler(event, context):
    print('Authorizer event:', json.dumps(event))

    headers = event.get('headers', {})

    api_key = (
        headers.get('x-api-key') or
        headers.get('X-Api-Key') or
        headers.get('X-API-Key') or ''
    )

    auth_header = (
        headers.get('authorization') or
        headers.get('Authorization') or ''
    )

    # React app — x-api-key: jwt + Bearer token
    if api_key == 'jwt' and auth_header.startswith('Bearer '):
        token  = auth_header[7:]
        result = validate_jwt(token)
        if result['valid']:
            print(f"[AUTH] JWT valid for: {result['principal']}")
            return allow(result['principal'], 'jwt')
        else:
            print(f"[AUTH] JWT invalid: {result['error']}")
            raise Exception('Unauthorized')

    # Postman/backend — real API key
    if api_key and api_key == VALID_API_KEY:
        print('[AUTH] API key valid')
        return allow('api-key-client', 'api-key')

    print('[AUTH] No valid credentials')
    raise Exception('Unauthorized')


def allow(principal, auth_type):
    return {
        'isAuthorized': True,
        'context': {
            'apiKey':    'valid',
            'principal': principal,
            'authType':  auth_type
        }
    }