"""
ForgeRock IDM (OpenIDM) user lookup, using the
service account access token.

Required environment variables:

    FORGEROCK_IDM_URL
    FORGEROCK_SERVICE_*   (see forgerock_service_auth)
"""

import os
import json
import time
import urllib.parse
import urllib.request
import urllib.error

from forgerock_service_auth import (
    SSL_CONTEXT,
    get_service_access_token,
    clear_service_token_cache
)


FORGEROCK_IDM_URL = os.environ["FORGEROCK_IDM_URL"]


# ============================================================
# OPENIDM USER LOOKUP
# ============================================================

def get_openidm_user(
    username,
    access_token
):

    start = time.time()

    print(
        "[OPENIDM] Starting user lookup"
    )

    params = {

        "_queryFilter":
            f'userName eq "{username}"',

        "_pageSize":
            "10",

        "_totalPagedResultsPolicy":
            "EXACT",

        "_sortKeys":
            "userName",

        "_fields":
            "*,manager/*,organisation/*,_meta/"
    }

    url = (
        FORGEROCK_IDM_URL
        + "?"
        + urllib.parse.urlencode(params)
    )

    headers = {
        "Authorization":
            "Bearer " + access_token,

        "Accept":
            "application/json"
    }

    request = urllib.request.Request(
        url,
        headers=headers,
        method="GET"
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

        result = json.loads(
            body
        )

        users = result.get(
            "result",
            []
        )

        elapsed = int(
            (time.time() - start) * 1000
        )

        print(
            f"[OPENIDM] Completed: "
            f"status={status} "
            f"users={len(users)} "
            f"time_ms={elapsed}"
        )

        if not users:

            return None

        return users[0]

    except urllib.error.HTTPError as e:

        elapsed = int(
            (time.time() - start) * 1000
        )

        print(
            f"[OPENIDM] HTTP error: "
            f"status={e.code} "
            f"time_ms={elapsed}"
        )

        # Cached service token was revoked or expired
        # early; fetch a fresh one on the next call.
        if e.code == 401:
            clear_service_token_cache()

        return None

    except Exception as e:

        elapsed = int(
            (time.time() - start) * 1000
        )

        print(
            f"[OPENIDM] Error: "
            f"{type(e).__name__} "
            f"time_ms={elapsed}"
        )

        return None


# ============================================================
# LOAD USER PROFILE
# ============================================================

def load_user_profile(username):

    print(
        "[PROFILE] Loading OpenIDM profile"
    )

    service_token = (
        get_service_access_token()
    )

    if not service_token:

        print(
            "[PROFILE] "
            "Service token generation failed"
        )

        return None

    user = get_openidm_user(
        username,
        service_token
    )

    if not user:

        print(
            "[PROFILE] "
            "OpenIDM user not found"
        )

        return None

    print(
        "[PROFILE] OpenIDM user found"
    )

    return user
