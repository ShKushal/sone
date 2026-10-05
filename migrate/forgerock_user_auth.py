"""
ForgeRock end-user authentication.

Validates a username/password against the ForgeRock
authenticate endpoint.

Required environment variables:

    FORGEROCK_AUTH_URL
"""

import os
import json
import time
import urllib.request
import urllib.error

from forgerock_service_auth import SSL_CONTEXT


FORGEROCK_AUTH_URL = os.environ["FORGEROCK_AUTH_URL"]


# ============================================================
# FORGEROCK USER AUTHENTICATION
# ============================================================

def authenticate_user(username, password):

    start = time.time()

    print(
        "[AUTH] Starting ForgeRock authentication"
    )

    headers = {
        "X-OpenAM-Username": username,
        "X-OpenAM-Password": password,
        "Accept-API-Version":
            "resource=2.0, protocol=1.0",
        "Content-Type":
            "application/json",
        "Accept":
            "application/json"
    }

    request = urllib.request.Request(
        FORGEROCK_AUTH_URL,
        data=b"{}",
        headers=headers,
        method="POST"
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=3,
            context=SSL_CONTEXT
        ) as response:

            status = response.status

            body = (
                response
                .read()
                .decode("utf-8")
            )

        try:
            result = json.loads(body)
        except Exception:
            result = {}

        authenticated = bool(
            result.get("tokenId")
        )

        elapsed = int(
            (time.time() - start) * 1000
        )

        print(
            f"[AUTH] Completed: "
            f"status={status} "
            f"success={authenticated} "
            f"time_ms={elapsed}"
        )

        return authenticated

    except urllib.error.HTTPError as e:

        elapsed = int(
            (time.time() - start) * 1000
        )

        print(
            f"[AUTH] HTTP error: "
            f"status={e.code} "
            f"time_ms={elapsed}"
        )

        return False

    except Exception as e:

        elapsed = int(
            (time.time() - start) * 1000
        )

        print(
            f"[AUTH] Error: "
            f"{type(e).__name__} "
            f"time_ms={elapsed}"
        )

        return False
