import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

PREFIX = "azap/"


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    trigger_source = event.get("triggerSource")
    request = event.get("request", {})
    user_attributes = request.get("userAttributes", {})

    # Raw scopes, exactly as Cognito expects for scopesToAdd/Suppress
    requested_scopes = request.get("scopes", []) or []

    # Stripped version — for display/custom claims ONLY, never for
    # scopesToAdd/scopesToSuppress, and never under the key "scope"
    # (that key is reserved and claimsToAddOrOverride cannot set it).
    clean_scopes = [
        scope[len(PREFIX):] if scope.startswith(PREFIX) else scope
        for scope in requested_scopes
    ]

    username = event.get("userName")
    email = user_attributes.get("email")

    # ID TOKEN CLAIMS
    id_token_claims = {}
    if username:
        id_token_claims["username"] = username
        id_token_claims["subname"] = username
    if email:
        id_token_claims["email"] = email
    if clean_scopes:
        id_token_claims["scopes_list"] = clean_scopes  # renamed from "scope"

    # ACCESS TOKEN CLAIMS
    access_token_claims = {}
    if username:
        access_token_claims["username"] = username
        access_token_claims["subname"] = username
    if email:
        access_token_claims["email"] = email

    access_token_claims["tokenName"] = "access_token"
    access_token_claims["token_type"] = "Bearer"
    access_token_claims["grant_type"] = "authorization_code"
    access_token_claims["realm"] = "/alpha"
    access_token_claims["api_products"] = ["oidc-product-identity"]

    if clean_scopes:
        access_token_claims["scopes_list"] = clean_scopes  # renamed from "scope"

    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "idTokenGeneration": {
                "claimsToAddOrOverride": id_token_claims
            },
            "accessTokenGeneration": {
                "claimsToAddOrOverride": access_token_claims,
                "scopesToAdd": requested_scopes,   # real scope claim uses raw form
                "scopesToSuppress": []
            }
        }
    }

    logger.info("ID token claims: %s", json.dumps(id_token_claims))
    logger.info("Access token claims: %s", json.dumps(access_token_claims))
    logger.info("Requested (raw) scopes: %s", json.dumps(requested_scopes))
    logger.info("Clean (display) scopes: %s", json.dumps(clean_scopes))

    return event