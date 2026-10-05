import logging

import boto3


logger = logging.getLogger()

cognito_client = boto3.client("cognito-idp")

USER_OPERATION_ATTRIBUTE = "custom:userOperation"
INVITATION_SENT = "INVITATION_SENT"


def mark_invitation_sent(event):
    """
    Update custom:userOperation to INVITATION_SENT
    after preparing the invitation email.
    """

    user_pool_id = event.get("userPoolId")
    username = event.get("userName")

    if not user_pool_id or not username:
        logger.error(
            "Missing userPoolId or userName. "
            "Cannot update userOperation."
        )
        raise ValueError(
            "Missing userPoolId or userName"
        )

    cognito_client.admin_update_user_attributes(
        UserPoolId=user_pool_id,
        Username=username,
        UserAttributes=[
            {
                "Name": USER_OPERATION_ATTRIBUTE,
                "Value": INVITATION_SENT
            }
        ]
    )

    logger.info(
        "%s=%s updated successfully "
        "for user: %s",
        USER_OPERATION_ATTRIBUTE,
        INVITATION_SENT,
        username
    )
