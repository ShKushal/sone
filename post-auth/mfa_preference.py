import logging

from user_attributes import cognito

logger = logging.getLogger()

ORGANISATION_ATTRIBUTE = "custom:organisation"


def ensure_email_mfa(user_pool_id, username, attrs):
    """
    Make email the preferred MFA method for broker users (those with
    custom:organisation), so later logins send the code by email.
    Covers users migrated just-in-time from ForgeRock.

    Post authentication runs after the current login has finished,
    so this only affects later logins. Never raises, so it can't
    block login.
    """
    if not (attrs.get(ORGANISATION_ATTRIBUTE) or "").strip():
        return

    try:
        cognito.admin_set_user_mfa_preference(
            UserPoolId=user_pool_id,
            Username=username,
            EmailMfaSettings={"Enabled": True, "PreferredMfa": True}
        )
        logger.info("[MFA] Email MFA set as preferred")

    except Exception:
        logger.exception("[MFA] Failed to set email MFA preference")
