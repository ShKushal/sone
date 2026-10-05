import html
from urllib.parse import urlencode

import boto3

from config import RESET_PASSWORD_PATH, SENDER_EMAIL, get_required_env
from email_template import EMAIL_SUBJECT, EMAIL_TEMPLATE

ses = boto3.client("ses")


def _build_reset_password_url():
    domain = get_required_env("COGNITO_DOMAIN")
    query = {
        "client_id": get_required_env("COGNITO_CLIENT_ID"),
        "response_type": "code",
        "redirect_uri": get_required_env("COGNITO_REDIRECT_URI"),
    }

    return f"https://{domain}{RESET_PASSWORD_PATH}?{urlencode(query)}"


def _get_display_name(attrs, username):
    full_name = " ".join(
        name.strip()
        for name in (attrs.get("given_name", ""), attrs.get("family_name", ""))
        if name.strip()
    )
    return full_name or username


def _get_login_id(attrs, username):
    # When the pool signs in with email/phone, event["userName"] is the
    # internal UUID, so prefer the identifier the user actually types.
    for attribute in ("preferred_username", "email", "phone_number"):
        value = attrs.get(attribute, "").strip()
        if value:
            return value
    return username


def render_email(attrs, username):
    login_id = _get_login_id(attrs, username)
    replacements = {
        "{{displayName}}": _get_display_name(attrs, login_id),
        "{{object.userName}}": login_id,
        "{{resetPasswordUrl}}": _build_reset_password_url(),
    }

    message = EMAIL_TEMPLATE
    for placeholder, value in replacements.items():
        message = message.replace(placeholder, html.escape(value))
    return message


def send_confirmation_email(email, message):
    print("----sending email----")
    return ses.send_email(
        Source=SENDER_EMAIL,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {
                "Data": EMAIL_SUBJECT,
                "Charset": "UTF-8"
            },
            "Body": {
                "Html": {
                    "Data": message,
                    "Charset": "UTF-8"
                }
            }
        }
    )
