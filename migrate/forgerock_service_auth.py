"""
ForgeRock service account authentication.

Shared by Lambda functions that call ForgeRock APIs (IDM, AM)
using the service account instead of an end-user session.

Usage:

    from forgerock_service_auth import get_service_access_token

    token = get_service_access_token()

Required environment variables:

    FORGEROCK_SERVICE_CLIENT_ID
    FORGEROCK_SERVICE_JWK
    FORGEROCK_SERVICE_SCOPE
    FORGEROCK_SERVICE_TOKEN_URL

Optional:

    SKIP_TLS_VERIFY   ("true" disables TLS verification)

Standard library only, so it can be dropped into any
Lambda package or published as a Lambda layer.
"""

import os
import json
import time
import uuid
import base64
import hashlib
import ssl
import threading
import urllib.parse
import urllib.request
import urllib.error


# ============================================================
# SSL
# ============================================================

SKIP_TLS_VERIFY = os.environ.get(
    "SKIP_TLS_VERIFY",
    "false"
).lower() == "true"

if SKIP_TLS_VERIFY:
    print(
        "[SERVICE-TOKEN] WARNING: "
        "TLS verification is disabled"
    )
    SSL_CONTEXT = ssl._create_unverified_context()
else:
    SSL_CONTEXT = ssl.create_default_context()


# ============================================================
# TOKEN CACHE
#
# Lives for the lifetime of the Lambda execution
# environment, so warm invocations reuse the token.
# ============================================================

# Refresh this many seconds before the token expires.
TOKEN_EXPIRY_MARGIN_SECONDS = 60

# Used when the token response has no expires_in.
DEFAULT_TOKEN_LIFETIME_SECONDS = 300

_token_cache = {
    "access_token": None,
    "expires_at": 0
}

_token_lock = threading.Lock()


# ============================================================
# BASE64URL
# ============================================================

def base64url_encode(data):

    if isinstance(data, str):
        data = data.encode("utf-8")

    return base64.urlsafe_b64encode(
        data
    ).rstrip(b"=").decode("ascii")


def base64url_decode(value):

    padding = "=" * (-len(value) % 4)

    return base64.urlsafe_b64decode(
        value + padding
    )


# ============================================================
# JWK INTEGER
# ============================================================

def jwk_int(value):

    return int.from_bytes(
        base64url_decode(value),
        byteorder="big"
    )


# ============================================================
# RSA SHA256 / RS256
# ============================================================

def rsa_sha256_sign(message, jwk):

    n = jwk_int(jwk["n"])
    e = jwk_int(jwk["e"])

    digest = hashlib.sha256(
        message
    ).digest()

    digest_info = bytes.fromhex(
        "3031300d060960864801650304020105000420"
    ) + digest

    key_size = (
        n.bit_length() + 7
    ) // 8

    padding_length = (
        key_size
        - len(digest_info)
        - 3
    )

    if padding_length < 8:
        raise ValueError(
            "RSA key too small for RS256"
        )

    encoded_message = (
        b"\x00\x01"
        + (b"\xff" * padding_length)
        + b"\x00"
        + digest_info
    )

    message_int = int.from_bytes(
        encoded_message,
        "big"
    )

    # Use RSA CRT parameters when available.
    if all(
        key in jwk
        for key in ["p", "q", "dp", "dq", "qi"]
    ):

        p = jwk_int(jwk["p"])
        q = jwk_int(jwk["q"])
        dp = jwk_int(jwk["dp"])
        dq = jwk_int(jwk["dq"])
        qi = jwk_int(jwk["qi"])

        m1 = pow(message_int, dp, p)
        m2 = pow(message_int, dq, q)

        h = ((m1 - m2) * qi) % p

        signature_int = m2 + q * h

    else:

        d = jwk_int(jwk["d"])

        signature_int = pow(
            message_int,
            d,
            n
        )

    # Verify before releasing the signature. A faulty CRT
    # result would otherwise leak the private key factors.
    if pow(signature_int, e, n) != message_int:
        raise ValueError(
            "RSA signature verification failed"
        )

    return signature_int.to_bytes(
        key_size,
        byteorder="big"
    )


# ============================================================
# CREATE SERVICE ACCOUNT JWT
# ============================================================

def create_service_jwt(
    client_id=None,
    jwk_json=None,
    token_url=None,
    lifetime_seconds=899
):
    """
    Build a signed RS256 client assertion for the
    jwt-bearer grant. Arguments default to the
    FORGEROCK_SERVICE_* environment variables.
    """

    client_id = client_id or os.environ[
        "FORGEROCK_SERVICE_CLIENT_ID"
    ]

    jwk_json = jwk_json or os.environ[
        "FORGEROCK_SERVICE_JWK"
    ]

    token_url = token_url or os.environ[
        "FORGEROCK_SERVICE_TOKEN_URL"
    ]

    jwk = json.loads(jwk_json)

    now = int(time.time())

    header = {
        "alg": "RS256",
        "typ": "JWT"
    }

    if jwk.get("kid"):
        header["kid"] = jwk["kid"]

    payload = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_url,
        "exp": now + lifetime_seconds,
        "jti": str(uuid.uuid4())
    }

    encoded_header = base64url_encode(
        json.dumps(header, separators=(",", ":"))
    )

    encoded_payload = base64url_encode(
        json.dumps(payload, separators=(",", ":"))
    )

    signing_input = (
        encoded_header
        + "."
        + encoded_payload
    ).encode("ascii")

    signature = rsa_sha256_sign(
        signing_input,
        jwk
    )

    return (
        encoded_header
        + "."
        + encoded_payload
        + "."
        + base64url_encode(signature)
    )


# ============================================================
# GET SERVICE ACCESS TOKEN
# ============================================================

def _request_service_access_token(timeout):

    start = time.time()

    token_url = os.environ[
        "FORGEROCK_SERVICE_TOKEN_URL"
    ]

    print("[SERVICE-TOKEN] Creating service JWT")

    try:
        assertion = create_service_jwt()
    except Exception as e:
        print(
            f"[SERVICE-TOKEN] JWT creation failed: "
            f"{type(e).__name__}"
        )
        return None, 0

    form_data = urllib.parse.urlencode({

        # IMPORTANT:
        # The ForgeRock token endpoint expects
        # "service-account" here.
        #
        # The actual service client UUID is used
        # inside the JWT iss/sub claims.
        "client_id":
            "service-account",

        "grant_type":
            "urn:ietf:params:oauth:grant-type:jwt-bearer",

        "assertion":
            assertion,

        "scope":
            os.environ["FORGEROCK_SERVICE_SCOPE"]

    }).encode("utf-8")

    headers = {
        "Content-Type":
            "application/x-www-form-urlencoded",
        "Accept":
            "application/json"
    }

    request = urllib.request.Request(
        token_url,
        data=form_data,
        headers=headers,
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=SSL_CONTEXT
        ) as response:

            status = response.status
            body = response.read().decode("utf-8")

        result = json.loads(body)

        access_token = result.get("access_token")

        elapsed = int((time.time() - start) * 1000)

        print(
            f"[SERVICE-TOKEN] Completed: "
            f"status={status} "
            f"success={bool(access_token)} "
            f"time_ms={elapsed}"
        )

        if not access_token:
            print("[SERVICE-TOKEN] No access token returned")
            return None, 0

        try:
            expires_in = int(result.get("expires_in"))
        except (TypeError, ValueError):
            expires_in = DEFAULT_TOKEN_LIFETIME_SECONDS

        return access_token, expires_in

    except urllib.error.HTTPError as e:

        elapsed = int((time.time() - start) * 1000)

        print(
            f"[SERVICE-TOKEN] HTTP error: "
            f"status={e.code} "
            f"time_ms={elapsed}"
        )

        return None, 0

    except Exception as e:

        elapsed = int((time.time() - start) * 1000)

        print(
            f"[SERVICE-TOKEN] Error: "
            f"{type(e).__name__} "
            f"time_ms={elapsed}"
        )

        return None, 0


def get_service_access_token(
    timeout=3,
    force_refresh=False
):
    """
    Return a ForgeRock service account access token,
    or None on failure. Tokens are cached until shortly
    before they expire. Safe to call from multiple threads.
    """

    with _token_lock:

        now = time.time()

        if (
            not force_refresh
            and _token_cache["access_token"]
            and now < _token_cache["expires_at"]
        ):
            print("[SERVICE-TOKEN] Using cached token")
            return _token_cache["access_token"]

        access_token, expires_in = (
            _request_service_access_token(timeout)
        )

        if not access_token:
            return None

        _token_cache["access_token"] = access_token

        _token_cache["expires_at"] = (
            now
            + expires_in
            - TOKEN_EXPIRY_MARGIN_SECONDS
        )

        return access_token


def clear_service_token_cache():
    """
    Drop the cached token, e.g. after IDM returns 401.
    """

    with _token_lock:
        _token_cache["access_token"] = None
        _token_cache["expires_at"] = 0
