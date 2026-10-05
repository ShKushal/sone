"""
Cognito Post Confirmation Lambda.

Sends the password reset confirmation email after a user completes
the forgot password flow.

Handler: passwordReset.lambda_handler

Modules:

    config          environment variables
    email_template  password reset email subject + HTML
    reset_email     rendering + SES send
"""

import logging

from reset_email import render_email, send_reset_email

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TARGET_TRIGGER = "PostConfirmation_ConfirmForgotPassword"


def lambda_handler(event, context):
    trigger_source = event.get("triggerSource")
    username = event.get("userName")

    logger.info("[RESET] Trigger source: %s", trigger_source)

    if trigger_source != TARGET_TRIGGER:
        return event

    attrs = event.get("request", {}).get("userAttributes", {})
    email = attrs.get("email")

    if not email:
        logger.info("[RESET] No email for user, skipping: %s", username)
        return event

    try:
        send_reset_email(email, render_email(attrs, username))
        logger.info("[RESET] Password reset email sent to user: %s", username)

    except Exception:
        # The password is already changed at this point; raising would
        # only show the user an error, so log and let the flow finish.
        logger.exception(
            "[RESET] Failed to send password reset email for user: %s",
            username
        )

    return event
