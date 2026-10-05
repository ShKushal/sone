"""
Maps a ForgeRock IDM user record to the Cognito
UserAttributes list expected by the User Migration
Lambda response.
"""

import os

TEMP_PHONE = os.environ.get('TEMP_PHONE', '+6500000000')


# ============================================================
# HELPERS
# ============================================================

def _str(value):
    """
    Safely convert a value to string.
    Returns empty string for None.
    """
    if value is None:
        return ""
    return str(value).strip()


def _bool_str(value):
    """
    Convert a boolean-like value to
    Cognito's expected "true" / "false" string.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return "true" if value.lower() == "true" else "false"
    if isinstance(value, int):
        return "true" if value else "false"
    return "false"


def _get_org_broker_id(user):
    """
    Extract brokerId from the nested organisation object.

    ForgeRock returns organisation as:
    {
        "brokerId":    "100100CCC",
        "name":        "...",
        "displayName": "...",
        "_ref":        "managed/organisation/uuid"
    }
    """
    org = user.get("organisation")

    if not org:
        return ""

    if isinstance(org, dict):
        broker_id = org.get("brokerId", "")
        if broker_id:
            return _str(broker_id)
        ref = org.get("_ref", "")
        if ref:
            return ref.split("/")[-1]
        return ""

    return _str(org)


def _get_phone(user):
    """
    Get phone number from ForgeRock telephoneNumber field.
    Falls back to TEMP_PHONE placeholder if not provided.
    Phone is always stored unverified — this allows forgot
    password to fall back to email when SMS fails.

    ForgeRock telephoneNumber may be:
      - null / None  → use placeholder
      - ""           → use placeholder
      - "+6512345678" → use as is (already E.164)
      - "6512345678"  → add + prefix
      - "012-3456789" → strip dashes, add + prefix
    """
    phone = user.get("telephoneNumber")

    if not phone:
        return TEMP_PHONE

    phone = _str(phone)

    if not phone:
        return TEMP_PHONE

    # strip common separators
    cleaned = (
        phone
        .replace("-", "")
        .replace(" ", "")
        .replace("(", "")
        .replace(")", "")
        .replace(".", "")
    )

    # already E.164
    if cleaned.startswith("+"):
        return cleaned

    # add + prefix
    return "+" + cleaned


# ============================================================
# MAIN MAPPING
# ============================================================

def build_cognito_attributes(user):
    """
    Map a ForgeRock IDM user dict to a Cognito
    UserAttributes dict for the migration Lambda response.

    All values must be strings.
    """

    broker_id   = _get_org_broker_id(user)
    phone       = _get_phone(user)

    attrs = {}

    # --------------------------------------------------------
    # Standard attributes
    # --------------------------------------------------------
    mail = _str(user.get("mail"))
    if mail:
        attrs["email"]          = mail
        attrs["email_verified"] = "true"

    given_name = _str(user.get("givenName"))
    if given_name:
        attrs["given_name"] = given_name

    family_name = _str(user.get("sn"))
    if family_name:
        attrs["family_name"] = family_name

    # --------------------------------------------------------
    # Phone number — always set (real or placeholder)
    # Stored unverified so SMS recovery fails gracefully
    # and falls back to email
    # --------------------------------------------------------
    attrs["phone_number"]          = phone
    attrs["phone_number_verified"] = "false"

    # --------------------------------------------------------
    # Custom attributes
    # --------------------------------------------------------
    fr1 = _bool_str(user.get("frUnindexedString1"))
    if fr1:
        attrs["custom:frUnindexedString1"] = fr1

    fr2 = _str(user.get("frUnindexedString2"))
    if fr2:
        attrs["custom:frUnindexedString2"] = fr2

    fr3 = _str(user.get("frUnindexedString3"))
    if fr3:
        attrs["custom:frUnindexedString3"] = fr3

    fr4 = _str(user.get("frUnindexedString4"))
    if fr4:
        attrs["custom:frUnindexedString4"] = fr4

    fr5 = _str(user.get("frUnindexedString5"))
    if fr5:
        attrs["custom:frUnindexedString5"] = fr5

    # --------------------------------------------------------
    # Migration tracking
    # --------------------------------------------------------
    attrs["custom:migrationType"] = "JIT"

    if broker_id:
        attrs["custom:migrationBrokerId"] = broker_id

        print(
            f"[GROUP] migrationBrokerId populated"
        )

    return attrs