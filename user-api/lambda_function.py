import boto3
import html
import json
import os
import re
import time
import string
import secrets
from datetime import datetime
from urllib.parse import unquote

from email_template import PASSWORD_RESET_EMAIL_SUBJECT, PASSWORD_RESET_EMAIL_TEMPLATE

# -------------------------------------------------------
# Config
# -------------------------------------------------------
USER_POOL_ID     = os.environ.get('USER_POOL_ID',     'ap-southeast-1_vi0pVitMh')
REGION           = os.environ.get('REGION',           'ap-southeast-1')
DELETE_ENABLED   = os.environ.get('DELETE_ENABLED',   'false').lower() == 'true'
TEMP_PHONE       = os.environ.get('TEMP_PHONE',       '+6500000000')
EMAIL_LOGS_TABLE = os.environ.get('EMAIL_LOGS_TABLE', 'CognitoEmailLogs')

SES_FROM_EMAIL = os.environ.get('SES_FROM_EMAIL', 'noreply@allianz.sg')
LOGIN_URL      = os.environ.get('LOGIN_URL',      'https://policysearchintermediary.portal.allianz.sg/pms-fe/en/login-homepage')

# SES only publishes email events (send, delivery, bounce, ...) for
# messages sent with a configuration set
SES_CONFIGURATION_SET = os.environ.get('SES_CONFIGURATION_SET', '').strip()

PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")

cognito          = boto3.client('cognito-idp', region_name=REGION)
dynamodb         = boto3.resource('dynamodb',  region_name=REGION)
ses              = boto3.client('ses',         region_name=REGION)
email_logs_table = dynamodb.Table(EMAIL_LOGS_TABLE)

# cache password policy in Lambda memory
_password_policy = None

# -------------------------------------------------------
# Get password policy from Cognito — cached
# -------------------------------------------------------
def get_password_policy():
    global _password_policy
    if _password_policy:
        return _password_policy
    try:
        response = cognito.describe_user_pool(UserPoolId=USER_POOL_ID)
        policy   = response['UserPool'].get('Policies', {}).get('PasswordPolicy', {})
        _password_policy = {
            'min_length':        policy.get('MinimumLength',    8),
            'require_uppercase': policy.get('RequireUppercase', True),
            'require_lowercase': policy.get('RequireLowercase', True),
            'require_numbers':   policy.get('RequireNumbers',   True),
            'require_symbols':   policy.get('RequireSymbols',   True)
        }
        print(f"[POLICY] Loaded: {_password_policy}")
        return _password_policy
    except Exception as e:
        print(f"[POLICY] Failed to fetch: {e}")
        # safe defaults
        return {
            'min_length':        8,
            'require_uppercase': True,
            'require_lowercase': True,
            'require_numbers':   True,
            'require_symbols':   True
        }

# -------------------------------------------------------
# Generate random password matching pool policy
# -------------------------------------------------------
def generate_password():
    policy    = get_password_policy()
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits    = string.digits
    symbols   = '!@#$%^&*'

    pwd = []

    # guarantee one of each required type
    if policy['require_uppercase']: pwd.append(secrets.choice(uppercase))
    if policy['require_lowercase']: pwd.append(secrets.choice(lowercase))
    if policy['require_numbers']:   pwd.append(secrets.choice(digits))
    if policy['require_symbols']:   pwd.append(secrets.choice(symbols))

    # build pool of all allowed chars
    all_chars = lowercase
    if policy['require_uppercase']: all_chars += uppercase
    if policy['require_numbers']:   all_chars += digits
    if policy['require_symbols']:   all_chars += symbols

    # fill to min_length + 4 extra for safety
    target_len = max(policy['min_length'], len(pwd)) + 4
    while len(pwd) < target_len:
        pwd.append(secrets.choice(all_chars))

    # shuffle so required chars not always at start
    secrets.SystemRandom().shuffle(pwd)

    return ''.join(pwd)

# -------------------------------------------------------
# Create single user
# -------------------------------------------------------
def create_user(user):
    errors = []

    username             = user.get('userName',           '').strip()
    email                = user.get('mail',               '').strip()
    given_name           = user.get('givenName',          '').strip()
    sn                   = user.get('sn',                 '').strip()
    organisation         = user.get('organisation',       '').strip()
    fr_unindexed_string1 = user.get('frUnindexedString1', '').strip()
    fr_unindexed_string2 = user.get('frIndexedString2',   '').strip()
    fr_unindexed_string5 = user.get('frUnindexedString5', '').strip()

    phone_number = user.get('phoneNumber', '').strip()
    if not phone_number:
        phone_number = TEMP_PHONE

    is_active = fr_unindexed_string1.upper() == 'TRUE'

    if not username:
        return { 'success': False, 'errors': ['userName is required'] }
    if not email:
        return { 'success': False, 'errors': ['mail (email) is required'] }

    try:
        cognito.admin_create_user(
            UserPoolId=USER_POOL_ID,
            Username=username,
            DesiredDeliveryMediums=['EMAIL'],
            UserAttributes=[
                { 'Name': 'email',                     'Value': email },
                { 'Name': 'email_verified',            'Value': 'true' },
                { 'Name': 'phone_number',              'Value': phone_number },
                { 'Name': 'phone_number_verified',     'Value': 'false' },
                { 'Name': 'given_name',                'Value': given_name },
                { 'Name': 'family_name',               'Value': sn },
                { 'Name': 'custom:organisation',       'Value': organisation },
                { 'Name': 'custom:frUnindexedString1', 'Value': fr_unindexed_string1 },
                { 'Name': 'custom:frUnindexedString2', 'Value': fr_unindexed_string2 },
                { 'Name': 'custom:frUnindexedString5', 'Value': fr_unindexed_string5 },
            ]
        )
        print(f"Cognito user created: {username}")

        if not is_active:
            cognito.admin_disable_user(UserPoolId=USER_POOL_ID, Username=username)
            print(f"User disabled: {username}")
        else:
            cognito.admin_enable_user(UserPoolId=USER_POOL_ID, Username=username)
            print(f"User enabled: {username}")

        if organisation:
            try:
                cognito.admin_add_user_to_group(
                    UserPoolId=USER_POOL_ID,
                    Username=username,
                    GroupName=organisation
                )
                print(f"User {username} added to group: {organisation}")
            except Exception as e:
                print(f"Group assignment warning: {e}")

    except cognito.exceptions.UsernameExistsException:
        return { 'userName': username, 'success': False, 'errors': ['User already exists'] }
    except cognito.exceptions.TooManyRequestsException:
        return { 'userName': username, 'success': False, 'errors': ['Rate limit hit — will retry'] }
    except Exception as e:
        errors.append(f"Cognito error: {str(e)}")

    return {
        'userName': username,
        'success':  len(errors) == 0,
        'errors':   errors
    }

# -------------------------------------------------------
# Bulk create users
# -------------------------------------------------------
def bulk_create_users(users):
    success = []
    failed  = []
    skipped = []
    total   = len(users)

    print(f"Bulk create started: {total} users")

    for i, user in enumerate(users):
        if i > 0 and i % 8 == 0:
            print(f"Rate limit pause after {i}/{total} users")
            time.sleep(1)

        username    = user.get('userName', '').strip()
        max_retries = 3
        result      = None

        for attempt in range(max_retries):
            result = create_user(user)
            if (not result['success'] and
                    result['errors'] and
                    'Rate limit' in result['errors'][0]):
                wait = 2 ** attempt
                print(f"Retrying {username} in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
            else:
                break

        if result['success']:
            success.append(username)
        elif result['errors'] and result['errors'][0] == 'User already exists':
            skipped.append(username)
        else:
            failed.append({ 'userName': username, 'errors': result['errors'] })

    print(f"Bulk complete: {len(success)} created, {len(failed)} failed, {len(skipped)} skipped")

    return {
        'total':   total,
        'created': len(success),
        'failed':  len(failed),
        'skipped': len(skipped),
        'results': { 'success': success, 'failed': failed, 'skipped': skipped }
    }

# -------------------------------------------------------
# Get one user
# -------------------------------------------------------
def get_user(username):
    try:
        response = cognito.admin_get_user(
            UserPoolId=USER_POOL_ID,
            Username=username
        )
        attrs = {
            a['Name']: a['Value']
            for a in response.get('UserAttributes', [])
        }
        return {
            'userName':   response['Username'],
            'status':     response['UserStatus'],
            'enabled':    response['Enabled'],
            'active':     attrs.get('custom:frUnindexedString1', '').upper() == 'TRUE',
            'createdAt':  response['UserCreateDate'].isoformat(),
            'updatedAt':  response['UserLastModifiedDate'].isoformat(),
            'attributes': attrs
        }
    except cognito.exceptions.UserNotFoundException:
        return None
    except Exception as e:
        print(f"Get user error: {e}")
        return None

# -------------------------------------------------------
# List all users (no pagination — used internally)
# -------------------------------------------------------
def list_users():
    try:
        users     = []
        paginator = cognito.get_paginator('list_users')
        for page in paginator.paginate(UserPoolId=USER_POOL_ID):
            for user in page['Users']:
                attrs = {
                    a['Name']: a['Value']
                    for a in user.get('Attributes', [])
                }
                users.append({
                    'userName':   user['Username'],
                    'status':     user['UserStatus'],
                    'enabled':    user['Enabled'],
                    'active':     attrs.get('custom:frUnindexedString1', '').upper() == 'TRUE',
                    'createdAt':  user['UserCreateDate'].isoformat(),
                    'attributes': attrs
                })
        return users
    except Exception as e:
        print(f"List users error: {e}")
        return []

# -------------------------------------------------------
# List users paginated — for helpdesk UI
# NOTE: This replaces list_users() for the GET /users route.
#       list_users() is kept for internal use by reports-api
#       via the forgerock-shared layer.
#       Do NOT remove list_users() — reports depend on it.
# -------------------------------------------------------
def list_users_paginated(limit=10, pagination_token=None):
    try:
        kwargs = {
            'UserPoolId': USER_POOL_ID,
            'Limit':      min(limit, 60)  # Cognito max is 60
        }
        if pagination_token:
            kwargs['PaginationToken'] = pagination_token

        response = cognito.list_users(**kwargs)

        users = []
        for user in response.get('Users', []):
            attrs = {
                a['Name']: a['Value']
                for a in user.get('Attributes', [])
            }
            users.append({
                'userName':   user['Username'],
                'status':     user['UserStatus'],
                'enabled':    user['Enabled'],
                'active':     attrs.get('custom:frUnindexedString1', '').upper() == 'TRUE',
                'createdAt':  user['UserCreateDate'].isoformat(),
                'attributes': attrs
            })

        next_token = response.get('PaginationToken')

        return {
            'users':     users,
            'nextToken': next_token,
            'hasMore':   next_token is not None,
            'count':     len(users)
        }
    except Exception as e:
        print(f"List users paginated error: {e}")
        return { 'users': [], 'nextToken': None, 'hasMore': False, 'count': 0 }

# -------------------------------------------------------
# Update user
# -------------------------------------------------------
def update_user(username, updates, triggered_by=''):
    errors = []

    updates.pop('userName',     None)
    updates.pop('createdAt',    None)
    updates.pop('triggeredBy',  None)

    if 'frUnindexedString1' in updates:
        is_active = updates['frUnindexedString1'].upper() == 'TRUE'
        try:
            if is_active:
                cognito.admin_enable_user(UserPoolId=USER_POOL_ID, Username=username)
                print(f"User enabled: {username}")
            else:
                cognito.admin_disable_user(UserPoolId=USER_POOL_ID, Username=username)
                print(f"User disabled: {username}")
        except cognito.exceptions.UserNotFoundException:
            return { 'userName': username, 'success': False, 'errors': ['User not found'] }
        except Exception as e:
            errors.append(f"Status update error: {str(e)}")

    cognito_attr_map = {
        'mail':               'email',
        'givenName':          'given_name',
        'sn':                 'family_name',
        'organisation':       'custom:organisation',
        'frUnindexedString1': 'custom:frUnindexedString1',
        'frIndexedString2':   'custom:frUnindexedString2',
        'frUnindexedString5': 'custom:frUnindexedString5',
        'phoneNumber':        'phone_number',
    }

    cognito_attrs = []
    for key, value in updates.items():
        if key in cognito_attr_map:
            cognito_attrs.append({
                'Name':  cognito_attr_map[key],
                'Value': str(value)
            })

    # a new email address starts with a clean delivery history
    if 'mail' in updates:
        cognito_attrs.append({ 'Name': FAIL_COUNT_ATTRIBUTE, 'Value': '0' })
        cognito_attrs.append({ 'Name': BLOCKED_ATTRIBUTE,      'Value': 'false' })

    if cognito_attrs:
        try:
            cognito.admin_update_user_attributes(
                UserPoolId=USER_POOL_ID,
                Username=username,
                UserAttributes=cognito_attrs
            )
            print(f"Cognito user updated: {username}")
        except cognito.exceptions.UserNotFoundException:
            return { 'userName': username, 'success': False, 'errors': ['User not found'] }
        except Exception as e:
            errors.append(f"Cognito error: {str(e)}")

    if 'organisation' in updates and not errors:
        try:
            current_groups = cognito.admin_list_groups_for_user(
                UserPoolId=USER_POOL_ID,
                Username=username
            ).get('Groups', [])

            for group in current_groups:
                cognito.admin_remove_user_from_group(
                    UserPoolId=USER_POOL_ID,
                    Username=username,
                    GroupName=group['GroupName']
                )

            if updates['organisation']:
                cognito.admin_add_user_to_group(
                    UserPoolId=USER_POOL_ID,
                    Username=username,
                    GroupName=updates['organisation']
                )
            print(f"Group updated for: {username}")
        except Exception as e:
            errors.append(f"Group update error: {str(e)}")

    return {
        'userName': username,
        'success':  len(errors) == 0,
        'errors':   errors
    }

# -------------------------------------------------------
# Delete user
# -------------------------------------------------------
def delete_user(username):
    errors = []
    try:
        cognito.admin_delete_user(UserPoolId=USER_POOL_ID, Username=username)
        print(f"Cognito user deleted: {username}")
    except cognito.exceptions.UserNotFoundException:
        return { 'userName': username, 'success': False, 'errors': ['User not found'] }
    except Exception as e:
        errors.append(f"Cognito error: {str(e)}")
    return { 'userName': username, 'success': len(errors) == 0, 'errors': errors }

# -------------------------------------------------------
# Email blocked after repeated failed deliveries — set by the SES
# event delivery tracker, cleared by POST .../email-unblock
# -------------------------------------------------------
FAIL_COUNT_ATTRIBUTE = 'custom:emailFailCount'
BLOCKED_ATTRIBUTE      = 'custom:emailBlocked'

def is_email_blocked(attrs):
    return (attrs.get(BLOCKED_ATTRIBUTE) or '').lower() == 'true'

def unblock_email(username, triggered_by=''):
    try:
        cognito.admin_update_user_attributes(
            UserPoolId=USER_POOL_ID,
            Username=username,
            UserAttributes=[
                { 'Name': FAIL_COUNT_ATTRIBUTE, 'Value': '0' },
                { 'Name': BLOCKED_ATTRIBUTE,      'Value': 'false' },
            ]
        )
        print(f"[EMAIL] Unblocked: {username} by {triggered_by}")
        return { 'userName': username, 'success': True }
    except cognito.exceptions.UserNotFoundException:
        return { 'userName': username, 'success': False, 'errors': ['User not found'] }
    except Exception as e:
        return { 'userName': username, 'success': False, 'errors': [str(e)] }

# -------------------------------------------------------
# Render password reset email — same layout as the other
# AZConnect emails (see email_template.py)
# -------------------------------------------------------
def render_password_reset_email(username, attrs, password):
    display_name = ' '.join(
        name.strip()
        for name in (attrs.get('given_name', ''), attrs.get('family_name', ''))
        if name.strip()
    ) or username

    values = {
        'displayName': display_name,
        'userName':    username,
        'password':    password,
        'loginUrl':    LOGIN_URL,
    }

    # Single pass, and every value is escaped: the password can contain '&'
    return PLACEHOLDER_PATTERN.sub(
        lambda match: html.escape(values[match.group(1)], quote=True),
        PASSWORD_RESET_EMAIL_TEMPLATE
    )

# -------------------------------------------------------
# Send password reset email via SES
# -------------------------------------------------------
def send_password_reset_email(email, message):
    extra = { 'ConfigurationSetName': SES_CONFIGURATION_SET } if SES_CONFIGURATION_SET else {}
    try:
        ses.send_email(
            **extra,
            Source=SES_FROM_EMAIL,
            Destination={ 'ToAddresses': [email] },
            Message={
                'Subject': {
                    'Data':    PASSWORD_RESET_EMAIL_SUBJECT,
                    'Charset': 'UTF-8'
                },
                'Body': {
                    'Html': {
                        'Data':    message,
                        'Charset': 'UTF-8'
                    }
                }
            }
        )
        print(f"[EMAIL] Password reset email sent to: {email}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed to send email to {email}: {e}")
        return False

# -------------------------------------------------------
# Reset password — generates random password from pool policy
# Permanent=True — no force change on next login
# -------------------------------------------------------
def reset_user_password(username, triggered_by=''):
    try:
        password = generate_password()

        cognito.admin_set_user_password(
            UserPoolId=USER_POOL_ID,
            Username=username,
            Password=password,
            Permanent=True   # no force reset on next login
        )

        print(f"[RESET] Password set for: {username} by {triggered_by}")

        # get user email and send notification
        user  = get_user(username)
        attrs = user['attributes'] if user else {}
        email = attrs.get('email', '')

        # no email after repeated failed deliveries (custom:emailBlocked)
        if is_email_blocked(attrs):
            print(f"[EMAIL] Blocked after repeated failed deliveries, not sent: {username}")
            return {
                'userName':     username,
                'success':      True,
                'emailSent':    False,
                'emailBlocked': True,
                'message':      'New password set — email blocked after repeated failed deliveries'
            }

        email_sent = False
        if email:
            message    = render_password_reset_email(username, attrs, password)
            email_sent = send_password_reset_email(email, message)

        return {
            'userName':  username,
            'success':   True,
            'emailSent': email_sent,
            'message':   'New password set and emailed to user' if email_sent else 'New password set — email notification failed'
        }

    except cognito.exceptions.UserNotFoundException:
        return { 'userName': username, 'success': False, 'errors': ['User not found'] }
    except cognito.exceptions.InvalidPasswordException as e:
        return { 'userName': username, 'success': False, 'errors': [f'Password policy violation: {str(e)}'] }
    except Exception as e:
        return { 'userName': username, 'success': False, 'errors': [str(e)] }

# -------------------------------------------------------
# Get email logs
# -------------------------------------------------------
def get_email_logs(username):
    try:
        response = email_logs_table.query(
            KeyConditionExpression='email = :e',
            ExpressionAttributeValues={ ':e': username.lower() },
            ScanIndexForward=False,
            Limit=50
        )
        return response.get('Items', [])
    except Exception as e:
        print(f"[EMAIL LOGS] Error: {e}")
        return []

# -------------------------------------------------------
# Lambda handler
# -------------------------------------------------------
def lambda_handler(event, context):
    print('Event:', json.dumps(event))

    route_key        = event.get('routeKey', 'GET /users')
    method           = route_key.split(' ')[0]
    path             = route_key.split(' ')[1]
    body             = json.loads(event.get('body') or '{}')
    path_parameters  = event.get('pathParameters') or {}
    query_parameters = event.get('queryStringParameters') or {}
    username         = unquote(path_parameters.get('username', ''))

    # POST /users/bulk
    if method == 'POST' and path == '/users/bulk':
        users = body.get('users', [])
        if not users:
            return resp(400, { 'error': 'users array is required' })
        if not isinstance(users, list):
            return resp(400, { 'error': 'users must be an array' })
        if len(users) > 500:
            return resp(400, { 'error': 'maximum 500 users per bulk request' })
        result = bulk_create_users(users)
        return resp(200, result)

    # POST /users
    elif method == 'POST' and path == '/users':
        if not body.get('userName'):
            return resp(400, { 'error': 'userName is required' })
        if not body.get('mail'):
            return resp(400, { 'error': 'mail (email) is required' })
        result = create_user(body)
        return resp(201 if result['success'] else 500, result)

    # GET /users — paginated
    elif method == 'GET' and path == '/users':
        limit            = int(query_parameters.get('limit', 10))
        pagination_token = query_parameters.get('paginationToken')
        result           = list_users_paginated(limit, pagination_token)
        return resp(200, result)

    # GET /users/{username}
    elif method == 'GET' and path == '/users/{username}':
        if not username:
            return resp(400, { 'error': 'username is required' })
        user = get_user(username)
        return resp(200 if user else 404, user or { 'error': 'User not found' })

    # PUT /users/{username}
    elif method == 'PUT' and path == '/users/{username}':
        if not username:
            return resp(400, { 'error': 'username is required' })
        if not body:
            return resp(400, { 'error': 'No fields to update' })
        triggered_by = body.pop('triggeredBy', '')
        result = update_user(username, body, triggered_by)
        return resp(200 if result['success'] else 500, result)

    # DELETE /users/{username}
    elif method == 'DELETE' and path == '/users/{username}':
        if not DELETE_ENABLED:
            return resp(403, { 'error': 'Delete operation is disabled' })
        if not username:
            return resp(400, { 'error': 'username is required' })
        result = delete_user(username)
        return resp(200 if result['success'] else 500, result)

    # POST /users/{username}/reset-password
    elif method == 'POST' and path == '/users/{username}/reset-password':
        if not username:
            return resp(400, { 'error': 'username is required' })
        triggered_by = body.get('triggeredBy', '')
        result = reset_user_password(username, triggered_by)
        return resp(200 if result['success'] else 500, result)

    # POST /users/{username}/email-unblock
    elif method == 'POST' and path == '/users/{username}/email-unblock':
        if not username:
            return resp(400, { 'error': 'username is required' })
        triggered_by = body.get('triggeredBy', '')
        result = unblock_email(username, triggered_by)
        if result['success']:
            return resp(200, result)
        return resp(404 if result['errors'] == ['User not found'] else 500, result)

    # GET /users/{username}/email-logs
    elif method == 'GET' and path == '/users/{username}/email-logs':
        if not username:
            return resp(400, { 'error': 'username is required' })
        logs = get_email_logs(username)
        return resp(200, { 'email': username, 'logs': logs })

    return resp(400, { 'error': 'Invalid request' })

# -------------------------------------------------------
# Response helper
# -------------------------------------------------------
def resp(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type':                'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body, default=str)
    }