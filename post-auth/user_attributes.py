import logging
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()

cognito = boto3.client("cognito-idp")

USER_OPERATION_ATTRIBUTE = "custom:userOperation"
INVITATION_SENT = "INVITATION_SENT"

# Set by the SES event delivery tracker after repeated failed deliveries
EMAIL_BLOCKED_ATTRIBUTE = "custom:emailBlocked"


def is_email_blocked(attrs):
    return (attrs.get(EMAIL_BLOCKED_ATTRIBUTE) or "").lower() == "true"


def should_send_confirmation(attrs):
    user_operation = attrs.get(USER_OPERATION_ATTRIBUTE, "")
    return user_operation == INVITATION_SENT and bool(attrs.get("email"))


def last_login_update():
    return {
        "Name": "custom:lastLogin",
        "Value": (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    }


def update_user_attributes(user_pool_id, username, updates):
    cognito.admin_update_user_attributes(
        UserPoolId=user_pool_id,
        Username=username,
        UserAttributes=updates
    )

    logger.info("User attributes updated for: %s", username)


def clear_user_operation(user_pool_id, username):
    cognito.admin_delete_user_attributes(
        UserPoolId=user_pool_id,
        Username=username,
        UserAttributeNames=[USER_OPERATION_ATTRIBUTE]
    )

    logger.info("%s cleared for: %s", USER_OPERATION_ATTRIBUTE, username)
