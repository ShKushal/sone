import html
import re

from config import REDIRECT_URI
from email_templates import INVITATION_EMAIL_TEMPLATE

PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def build_greeting(attrs):
    given_name = (
        attrs.get("given_name") or ""
    ).strip()

    family_name = (
        attrs.get("family_name") or ""
    ).strip()

    email = (
        attrs.get("email") or ""
    ).strip()

    # Safely escape user-provided values for HTML
    given_name_html = html.escape(given_name)
    family_name_html = html.escape(family_name)
    email_html = html.escape(email)

    if given_name and family_name:
        return (
            f"Dear {given_name_html} "
            f"{family_name_html},"
        )

    if given_name:
        return f"Dear {given_name_html},"

    if family_name:
        return f"Dear {family_name_html},"

    if email:
        return f"Dear {email_html},"

    return "Dear AZConnect User,"


def build_activation_link():
    # Link to the app, not the Cognito /login page: the app starts the
    # sign-in itself (state, PKCE, scopes), so it can exchange the code.
    return REDIRECT_URI


def render_email(greeting, request):
    # Cognito placeholders
    # Cognito replaces these after the Lambda returns.
    values = {
        "greeting": greeting,
        "activationLink": html.escape(
            build_activation_link(),
            quote=True
        ),
        "usernameParameter": request.get(
            "usernameParameter",
            "{username}"
        ),
        "codeParameter": request.get(
            "codeParameter",
            "{####}"
        ),
    }

    # Single pass, so a {{...}} inside a user's name is never expanded
    return PLACEHOLDER_PATTERN.sub(
        lambda match: values[match.group(1)],
        INVITATION_EMAIL_TEMPLATE
    )
