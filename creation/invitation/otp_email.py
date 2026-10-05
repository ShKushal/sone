import re

from invitation_email import build_greeting

PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def render_otp_email(attrs, request, template):
    values = {
        "greeting": build_greeting(attrs),
        # Cognito replaces this with the OTP after the Lambda returns
        "codeParameter": request.get("codeParameter", "{####}"),
    }

    # Single pass, so a {{...}} inside a user's name is never expanded
    return PLACEHOLDER_PATTERN.sub(
        lambda match: values[match.group(1)],
        template
    )