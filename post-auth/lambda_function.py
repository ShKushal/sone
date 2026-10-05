"""
Cognito Post Authentication Lambda.

On every login:
    1. adds migrated users to their broker group (never blocks login)
    2. sends the account confirmation email on the first login after
       an invitation
    3. records the login in custom:lastLogin

Handler: postAuth.lambda_handler

Modules:

    config               environment variables
    email_template       confirmation email subject + HTML
    confirmation_email   rendering + SES send
    broker_group         custom:migrationBrokerId -> Cognito group
    user_attributes      custom:userOperation / custom:lastLogin
    invocation_logging   event + context logging
"""

import logging
import time

from broker_group import assign_broker_group
from confirmation_email import render_email, send_confirmation_email
from invocation_logging import log_invocation
from user_attributes import (
    clear_user_operation,
    is_email_blocked,
    last_login_update,
    should_send_confirmation,
    update_user_attributes,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TARGET_TRIGGER = "PostAuthentication_Authentication"


def lambda_handler(event, context):
    start_time = time.time()
    log_invocation(event, context)

    if event.get("triggerSource") != TARGET_TRIGGER:
        return event

    attrs = event.get("request", {}).get("userAttributes", {})
    user_pool_id = event["userPoolId"]
    username = event["userName"]

    assign_broker_group(user_pool_id, username, attrs)

    updates = [last_login_update()]
    clear_operation = False

    try:
        if should_send_confirmation(attrs) and is_email_blocked(attrs):
            # Keep custom:userOperation so the email is sent on the
            # first login after the helpdesk unblocks the user
            logger.info(
                "Confirmation email blocked after repeated failed deliveries, "
                "not sent for user: %s",
                username
            )
        elif should_send_confirmation(attrs):
            send_confirmation_email(attrs["email"], render_email(attrs, username))
            # Clear the operation only after the email was sent successfully
            clear_operation = True
            logger.info("Confirmation email sent to user: %s", username)
        else:
            logger.info("Confirmation email skipped for user: %s", username)

        update_user_attributes(user_pool_id, username, updates)

        if clear_operation:
            clear_user_operation(user_pool_id, username)

    except Exception:
        logger.exception(
            "Post Authentication failed for user: %s",
            username
        )
        raise

    logger.info(
        "[POST-AUTH] COMPLETE: total_time_ms=%s",
        int((time.time() - start_time) * 1000)
    )

    return event
