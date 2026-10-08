import logging

from user_attributes import cognito

logger = logging.getLogger()

MIGRATION_BROKER_ID_ATTRIBUTE = "custom:migrationBrokerId"


def assign_broker_group(user_pool_id, username, attrs):
    """
    Add a migrated user to their broker's Cognito group, then clear
    custom:migrationBrokerId. Never raises, so it can't block login.
    """
    broker_id = (attrs.get(MIGRATION_BROKER_ID_ATTRIBUTE) or "").strip()

    if not broker_id:
        logger.info("[GROUP] migrationBrokerId not found")
        return

    logger.info("[GROUP] Adding user to group: %s", broker_id)

    try:
        cognito.admin_add_user_to_group(
            UserPoolId=user_pool_id,
            Username=username,
            GroupName=broker_id
        )
        logger.info("[GROUP] User added to group successfully")

    except cognito.exceptions.ResourceNotFoundException:
        # No group to assign the user to, so the broker id is cleared
        # anyway. Warn so the missing group can be investigated.
        logger.warning(
            "[GROUP] Cognito group not found: %s. "
            "Clearing migrationBrokerId without assignment",
            broker_id
        )

    except Exception:
        # Keep migrationBrokerId so it is retried on the next login
        logger.exception("[GROUP] Failed to add user to group: %s", broker_id)
        return

    _clear_broker_id(user_pool_id, username)


def _clear_broker_id(user_pool_id, username):
    try:
        cognito.admin_delete_user_attributes(
            UserPoolId=user_pool_id,
            Username=username,
            UserAttributeNames=[MIGRATION_BROKER_ID_ATTRIBUTE]
        )
        logger.info("[GROUP] migrationBrokerId cleared successfully")

    except Exception:
        # Group membership is kept; adding again on the next login is
        # harmless, so the attribute can be cleaned up then.
        logger.exception("[GROUP] Failed to clear migrationBrokerId")
