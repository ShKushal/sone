"""
Cognito Custom Message Lambda.

    CustomMessage_AdminCreateUser   invitation email
    CustomMessage_Authentication    sign-in OTP email
    CustomMessage_ForgotPassword    forgot password OTP email

Handler: inviation.lambda_handler

Modules:

    config               environment variables
    email_templates      all email subjects + HTML
    invitation_email     greeting, activation link, rendering
    otp_email            OTP email rendering
    user_attributes      custom:userOperation update
"""

import logging

from botocore.exceptions import ClientError

from email_templates import (
    FORGOT_PASSWORD_EMAIL_SUBJECT,
    FORGOT_PASSWORD_EMAIL_TEMPLATE,
    INVITATION_EMAIL_SUBJECT,
    OTP_EMAIL_SUBJECT,
    OTP_EMAIL_TEMPLATE,
)
from invitation_email import build_greeting, render_email
from otp_email import render_otp_email
from user_attributes import mark_invitation_sent


logger = logging.getLogger()
logger.setLevel(logging.INFO)

INVITATION_TRIGGER = "CustomMessage_AdminCreateUser"

# OTP triggers -> (subject, template)
OTP_EMAILS = {
    "CustomMessage_Authentication": (
        OTP_EMAIL_SUBJECT,
        OTP_EMAIL_TEMPLATE
    ),
    "CustomMessage_ForgotPassword": (
        FORGOT_PASSWORD_EMAIL_SUBJECT,
        FORGOT_PASSWORD_EMAIL_TEMPLATE
    ),
}


def lambda_handler(event, context):

    logger.info("=== Lambda invoked ===")

    trigger_source = event.get("triggerSource")

    logger.info(
        "Trigger source: %s",
        trigger_source
    )

    if trigger_source == INVITATION_TRIGGER:
        return _handle_invitation(event)

    if trigger_source in OTP_EMAILS:
        subject, template = OTP_EMAILS[trigger_source]
        return _handle_otp(event, subject, template)

    # Cognito sends its default message for anything not handled here
    logger.info(
        "Trigger source not handled: %s",
        trigger_source
    )
    return event


def _handle_otp(event, subject, template):
    request = event.get("request", {})
    attrs = request.get("userAttributes", {})

    message = render_otp_email(attrs, request, template)

    event.setdefault("response", {})

    event["response"]["emailSubject"] = subject
    event["response"]["emailMessage"] = message

    # Do not log the email content; it carries the OTP placeholder
    logger.info(
        "OTP email configured, length: %d",
        len(message)
    )

    logger.info("=== Lambda finished ===")

    return event


def _handle_invitation(event):

    request = event.get("request", {})
    attrs = request.get("userAttributes", {})

    greeting = build_greeting(attrs)
    message = render_email(greeting, request)

    # Set Cognito custom response
    event.setdefault("response", {})

    event["response"]["emailSubject"] = INVITATION_EMAIL_SUBJECT
    event["response"]["emailMessage"] = message

    logger.info(
        "Custom email subject: %s",
        INVITATION_EMAIL_SUBJECT
    )

    logger.info(
        "Custom email length: %d",
        len(message)
    )

    logger.info(
        "Greeting: %s",
        greeting
    )

    # Update custom attribute
    # custom:userOperation = INVITATION_SENT
    try:
        mark_invitation_sent(event)

    except ClientError:
        logger.exception(
            "AWS error while updating "
            "custom:userOperation"
        )

        # Fail the invocation so the update issue
        # is visible and can be investigated.
        raise

    except Exception:
        logger.exception(
            "Unexpected error while updating "
            "custom:userOperation"
        )
        raise

    # Do not log temporary passwords
    # or the full email content.
    logger.info(
        "Custom email response configured successfully"
    )

    logger.info("=== Lambda finished ===")

    return event
