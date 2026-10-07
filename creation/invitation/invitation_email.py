import html
import re

from config import REDIRECT_URI
from email_templates import INVITATION_EMAIL_TEMPLATE

PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")

MIGRATION_TYPE_ATTRIBUTE = "custom:migrationType"
BULK_MIGRATION = "bulk"


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


def is_bulk_migration(attrs):
    """
    Users bulk-migrated from ForgeRock carry custom:migrationType = Bulk
    and get the migration invitation instead of the standard one.
    """
    migration_type = (
        attrs.get(MIGRATION_TYPE_ATTRIBUTE) or ""
    ).strip()

    return migration_type.lower() == BULK_MIGRATION


def render_email(greeting, request, template=INVITATION_EMAIL_TEMPLATE):
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
        template
    )
