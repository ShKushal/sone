"""
Counts consecutive failed email deliveries per Cognito user and
blocks email to the user after EMAIL_FAIL_THRESHOLD of them in a row.

    Failed delivery    custom:emailFailCount += 1
                       at the threshold: custom:emailBlocked = "true"
    Delivery           custom:emailFailCount = 0 (unless blocked)
    anything else      ignored

Failed delivery = SES event "Bounce" (hard or soft), "Reject" or
"Rendering Failure". "DeliveryDelay" is not a failure: SES keeps
retrying and the email ends as Delivery or Bounce.

The sending Lambdas check custom:emailBlocked and skip the email.
Unblock through users-api: POST /users/{username}/email-unblock

Called by lambda_function.py (the SES event writer) after it writes
CognitoEmailLogs. It never raises, so the email log write can't be
broken by it.

Env:
    USER_POOL_ID           Cognito user pool
    EMAIL_FAIL_THRESHOLD   consecutive failed deliveries (default 5)
    REGION                 same as the event writer (default ap-southeast-1)
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Not os.environ[...]: a missing variable must not stop the event
# writer from loading and logging emails
USER_POOL_ID = os.environ.get("USER_POOL_ID", "").strip()
EMAIL_FAIL_THRESHOLD = int(os.environ.get("EMAIL_FAIL_THRESHOLD", "5"))

FAIL_COUNT_ATTRIBUTE = "custom:emailFailCount"
BLOCKED_ATTRIBUTE = "custom:emailBlocked"

FAILED_EVENTS = {"Bounce", "Reject", "Rendering Failure"}

cognito = boto3.client(
    "cognito-idp",
    region_name=os.environ.get("REGION", "ap-southeast-1")
)


def track_ses_event(ses_event):
    """
    ses_event: the SES event as a dict, i.e. the parsed SNS "Message".
    """
    if not USER_POOL_ID:
        logger.error("[DELIVERY] USER_POOL_ID not set, tracking skipped")
        return

    try:
        # Configuration set events use eventType,
        # identity notifications use notificationType
        event_type = (
            ses_event.get("eventType")
            or ses_event.get("notificationType")
        )

        if event_type in FAILED_EVENTS:
            for email in _failed_recipients(ses_event, event_type):
                for user in _find_users(email):
                    _record_failure(user, event_type)

        elif event_type == "Delivery":
            delivery = ses_event.get("delivery") or {}
            for email in delivery.get("recipients", []):
                for user in _find_users(email):
                    _reset_fail_count(user)

    except Exception:
        logger.exception("[DELIVERY] Failed to track SES event")


def is_email_blocked(attrs):
    return (attrs.get(BLOCKED_ATTRIBUTE) or "").lower() == "true"


def _failed_recipients(ses_event, event_type):
    # A bounce lists the recipients that bounced; Reject and Rendering
    # Failure apply to every recipient of the email
    if event_type == "Bounce":
        bounce = ses_event.get("bounce") or {}
        return [
            r.get("emailAddress", "")
            for r in bounce.get("bouncedRecipients", [])
        ]

    mail = ses_event.get("mail") or {}
    return mail.get("destination", [])


def _record_failure(user, event_type):
    username = user["Username"]
    attrs = _attributes(user)

    if is_email_blocked(attrs):
        logger.info("[DELIVERY] Already blocked: %s", username)
        return

    count = _fail_count(attrs) + 1
    updates = [
        {"Name": FAIL_COUNT_ATTRIBUTE, "Value": str(count)}
    ]

    blocked = count >= EMAIL_FAIL_THRESHOLD
    if blocked:
        updates.append({"Name": BLOCKED_ATTRIBUTE, "Value": "true"})

    cognito.admin_update_user_attributes(
        UserPoolId=USER_POOL_ID,
        Username=username,
        UserAttributes=updates
    )

    if blocked:
        logger.warning(
            "[DELIVERY] BLOCKED %s after %d consecutive failed deliveries",
            username,
            count
        )
    else:
        logger.info(
            "[DELIVERY] Failed delivery (%s) for %s, count=%d",
            event_type,
            username,
            count
        )


def _reset_fail_count(user):
    attrs = _attributes(user)

    # Blocked users stay blocked until the helpdesk unblocks them;
    # users without failures need no update
    if is_email_blocked(attrs) or _fail_count(attrs) == 0:
        return

    cognito.admin_update_user_attributes(
        UserPoolId=USER_POOL_ID,
        Username=user["Username"],
        UserAttributes=[
            {"Name": FAIL_COUNT_ATTRIBUTE, "Value": "0"}
        ]
    )

    logger.info("[DELIVERY] Delivered, count reset: %s", user["Username"])


def _find_users(email):
    email = (email or "").strip()

    if not email:
        return []

    # Escape quotes and backslashes for the ListUsers filter
    escaped = email.replace("\\", "\\\\").replace('"', '\\"')

    users = []
    paginator = cognito.get_paginator("list_users")

    for page in paginator.paginate(
        UserPoolId=USER_POOL_ID,
        Filter=f'email = "{escaped}"'
    ):
        users.extend(page.get("Users", []))

    if not users:
        logger.info("[DELIVERY] No Cognito user for recipient")

    return users


def _attributes(user):
    return {
        a["Name"]: a["Value"]
        for a in user.get("Attributes", [])
    }


def _fail_count(attrs):
    try:
        return int(attrs.get(FAIL_COUNT_ATTRIBUTE) or 0)
    except ValueError:
        return 0
