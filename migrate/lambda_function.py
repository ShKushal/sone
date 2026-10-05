"""
Cognito User Migration Lambda (just-in-time migration
from ForgeRock).

Handler: user_migration_handler.lambda_handler

Modules:

    forgerock_service_auth   service account JWT + access token
    forgerock_user_auth      end-user password check
    forgerock_idm            IDM profile lookup
    cognito_attributes       IDM user -> Cognito attributes
"""

import time
from concurrent.futures import ThreadPoolExecutor

from forgerock_user_auth import authenticate_user
from forgerock_idm import load_user_profile
from cognito_attributes import build_cognito_attributes


# ============================================================
# MAIN LAMBDA
# ============================================================

def lambda_handler(
    event,
    context
):

    start_time = time.time()

    trigger = event.get(
        "triggerSource"
    )

    username = event.get(
        "userName"
    )

    request_data = event.get(
        "request",
        {}
    )

    password = request_data.get(
        "password"
    )

    print(
        "========================================"
    )

    print(
        "=== COGNITO USER MIGRATION ==="
    )

    print(
        "========================================"
    )

    print(
        f"[MIGRATION] Trigger: {trigger}"
    )

    print(
        f"[MIGRATION] Username: {username}"
    )

    # ========================================================
    # FORGOT PASSWORD
    # ========================================================

    if trigger == "UserMigration_ForgotPassword":

        print(
            "[MIGRATION] "
            "Forgot password migration"
        )

        user = load_user_profile(
            username
        )

        if not user:

            print(
                "[MIGRATION] "
                "User not found"
            )

            return event

        attrs = {}

        email = user.get(
            "mail"
        )

        if email:

            attrs["email"] = str(
                email
            )

            attrs["email_verified"] = "true"

        event["response"] = {

            "userAttributes":
                attrs
        }

        print(
            "[COGNITO] "
            "Forgot password attributes:",
            list(attrs.keys())
        )

        total_time = int(
            (time.time() - start_time)
            * 1000
        )

        print(
            f"[MIGRATION] COMPLETE: "
            f"total_time_ms={total_time}"
        )

        return event

    # ========================================================
    # USER MIGRATION AUTHENTICATION
    # ========================================================

    if trigger != "UserMigration_Authentication":

        print(
            "[MIGRATION] "
            "Unsupported trigger"
        )

        return event

    # ========================================================
    # AUTH + PROFILE IN PARALLEL
    # ========================================================

    print(
        "[MIGRATION] "
        "Starting AUTH + PROFILE in parallel"
    )

    with ThreadPoolExecutor(
        max_workers=2
    ) as executor:

        auth_future = executor.submit(
            authenticate_user,
            username,
            password
        )

        profile_future = executor.submit(
            load_user_profile,
            username
        )

        authenticated = (
            auth_future.result()
        )

        user = (
            profile_future.result()
        )

    elapsed_parallel = int(
        (time.time() - start_time)
        * 1000
    )

    print(
        f"[MIGRATION] "
        f"Parallel operations completed: "
        f"authenticated={authenticated} "
        f"profile_found={bool(user)} "
        f"time_ms={elapsed_parallel}"
    )

    # ========================================================
    # AUTH FAILED
    # ========================================================

    if not authenticated:

        print(
            "[MIGRATION] "
            "ForgeRock authentication failed"
        )

        return event

    # ========================================================
    # PROFILE NOT FOUND
    # ========================================================

    if not user:

        print(
            "[MIGRATION] "
            "ForgeRock profile not found"
        )

        return event

    # ========================================================
    # BUILD ATTRIBUTES
    # ========================================================

    attrs = build_cognito_attributes(
        user
    )

    print(
        "[COGNITO] "
        "Attributes being returned:",
        list(attrs.keys())
    )

    # Don't print attribute values because some
    # may contain sensitive user information.

    # ========================================================
    # COGNITO MIGRATION RESPONSE
    # ========================================================

    event["response"] = {

        "userAttributes":
            attrs,

        "finalUserStatus":
            "CONFIRMED",

        "messageAction":
            "SUPPRESS"
    }

    # ========================================================
    # COMPLETE
    # ========================================================

    total_time = int(
        (time.time() - start_time)
        * 1000
    )

    print(
        f"[MIGRATION] COMPLETE: "
        f"total_time_ms={total_time}"
    )

    return event
 