import base64
import boto3
import html
import json
import os
import re
import time
import string
import secrets
from datetime import datetime
from decimal import Decimal
from urllib.parse import unquote

from boto3.dynamodb.conditions import Attr, Key

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

# errorCode → HTTP status. Anything not listed here is a 500.
ERROR_STATUS = {
    'USER_EXISTS':    409,
    'USER_NOT_FOUND': 404,
    'BAD_REQUEST':    400,
    'FORBIDDEN':      403,
}

# User payload fields — a caller must send each of these as a string
USER_STRING_FIELDS = (
    'userName', 'mail', 'givenName', 'sn', 'organisation', 'phoneNumber',
    'frUnindexedString1', 'frIndexedString2', 'frUnindexedString5',
)

MAX_PAGE_SIZE = 60   # Cognito ListUsers maximum

# Email delivery activity (GET /email-logs)
EMAIL_LOG_VIEW_GROUPS   = {'EMAIL_LOGS', 'SUPER_ADMIN'}   # Cognito groups allowed to read it
EMAIL_LOG_DEFAULT_LIMIT = 500      # newest N events returned when the caller sends no limit
EMAIL_LOG_MAX_ITEMS     = 1000     # hard ceiling on events returned per request
EMAIL_LOG_MAX_SCAN      = 20000    # stop reading the table after this many items (reported as incomplete)

# -------------------------------------------------------
# Request validation helpers
# -------------------------------------------------------
# Raised for a malformed request; lambda_handler turns it into a 400
class BadRequest(Exception):
    pass

def bad_request(errors, **extra):
    return { **extra, 'success': False, 'errorCode': 'BAD_REQUEST', 'errors': errors }

def not_found(username):
    return {
        'userName':  username,
        'success':   False,
        'errorCode': 'USER_NOT_FOUND',
        'errors':    ['User not found']
    }

def validate_string_fields(payload):
    return [
        f'{field} must be a string'
        for field in USER_STRING_FIELDS
        if field in payload and not isinstance(payload[field], str)
    ]

def parse_body(event):
    raw = event.get('body')
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise BadRequest('Request body must be valid JSON')
    if not isinstance(body, dict):
        raise BadRequest('Request body must be a JSON object')
    return body

def parse_limit(params, default=10):
    raw = params.get('limit')
    if raw is None:
        return default
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        raise BadRequest('limit must be a whole number')
    if limit < 1:
        raise BadRequest('limit must be at least 1')
    return min(limit, MAX_PAGE_SIZE)

def respond_with(result, success_status=200):
    if result['success']:
        return resp(success_status, result)
    return resp(ERROR_STATUS.get(result.get('errorCode'), 500), result)

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

    # a wrong-typed field would crash on .strip() below — reject it up front
    problems = validate_string_fields(user)
    if problems:
        return bad_request(problems)

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
        return bad_request(['userName is required'])
    if not email:
        return bad_request(['mail (email) is required'])
    if portal_group_clash(organisation):
        return bad_request([f'organisation cannot be the name of a portal group ({organisation})'])

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
        return {
            'userName':  username,
            'success':   False,
            'errorCode': 'USER_EXISTS',
            'errors':    ['User already exists']
        }
    except cognito.exceptions.TooManyRequestsException:
        return { 'userName': username, 'success': False, 'errors': ['Rate limit hit — will retry'] }
    except cognito.exceptions.InvalidParameterException as e:
        message = e.response['Error']['Message']
        print(f"Invalid parameter for {username}: {message}")
        return bad_request([message], userName=username)
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

        raw_name    = user.get('userName', '')
        username    = raw_name.strip() if isinstance(raw_name, str) else ''
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
        elif result.get('errorCode') == 'USER_EXISTS':
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
    except cognito.exceptions.InvalidParameterException:
        raise BadRequest('paginationToken is not valid')
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

    problems = validate_string_fields(updates)
    if problems:
        return bad_request(problems, userName=username)
    if portal_group_clash(updates.get('organisation')):
        return bad_request([f"organisation cannot be the name of a portal group ({updates['organisation'].strip()})"],
                           userName=username)

    # audit trail in CloudWatch: who changed which fields (names only, not the values)
    print(f"[UPDATE] {username} fields={sorted(updates)} by {triggered_by or 'unknown'}")

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
            return not_found(username)
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
            return not_found(username)
        except cognito.exceptions.InvalidParameterException as e:
            message = e.response['Error']['Message']
            print(f"Invalid parameter for {username}: {message}")
            return bad_request([message], userName=username)
        except Exception as e:
            errors.append(f"Cognito error: {str(e)}")

    if 'organisation' in updates and not errors:
        try:
            current_groups = cognito.admin_list_groups_for_user(
                UserPoolId=USER_POOL_ID,
                Username=username
            ).get('Groups', [])

            for group in current_groups:
                if is_portal_group(group.get('Description')) or group['GroupName'] in POLICY_GROUPS:
                    continue          # portal roles (HELPDESK, ...) are not tied to the organisation
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
        return not_found(username)
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
        return not_found(username)
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
        return not_found(username)
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
# User groups: list, and grant / remove portal groups
# -------------------------------------------------------
def list_user_groups(username):
    """Every Cognito group the user is in, or None if there is no such user."""
    names, token = [], None
    try:
        while True:
            kwargs = { 'UserPoolId': USER_POOL_ID, 'Username': username }
            if token:
                kwargs['NextToken'] = token
            response = cognito.admin_list_groups_for_user(**kwargs)
            names   += [g['GroupName'] for g in response.get('Groups', [])]
            token    = response.get('NextToken')
            if not token:
                return names
    except cognito.exceptions.UserNotFoundException:
        return None

def update_user_groups(username, body, actor):
    add    = body.get('add',    [])
    remove = body.get('remove', [])

    problems = [
        f'{name} must be a list of group names'
        for name, value in (('add', add), ('remove', remove))
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value)
    ]
    if problems:
        return bad_request(problems, userName=username)

    add    = list(dict.fromkeys(v.strip() for v in add    if v.strip()))
    remove = list(dict.fromkeys(v.strip() for v in remove if v.strip()))
    if not add and not remove:
        return bad_request(['add or remove must list at least one group'], userName=username)

    try:
        portal = portal_groups()
        if any(g not in portal for g in add + remove):
            portal = portal_groups(force=True)        # perhaps a group created a moment ago
    except Exception as e:
        return { 'userName': username, 'success': False, 'errors': [f'Could not check which groups are portal groups: {e}'] }
    unknown = sorted({g for g in add + remove if g not in portal})
    if unknown:
        return bad_request([f"Not a group that can be managed here (its description must start with {PORTAL_GROUP_MARKER}): {', '.join(unknown)}"],
                           userName=username)
    both = sorted(set(add) & set(remove))
    if both:
        return bad_request([f"Cannot add and remove the same group: {', '.join(both)}"], userName=username)

    current = list_user_groups(username)
    if current is None:
        return not_found(username)

    added, removed, errors = [], [], []
    for group in (g for g in add if g not in current):
        try:
            cognito.admin_add_user_to_group(UserPoolId=USER_POOL_ID, Username=username, GroupName=group)
            added.append(group)
        except cognito.exceptions.ResourceNotFoundException:
            errors.append(f'Group {group} does not exist in the user pool')
        except Exception as e:
            errors.append(f'Could not add {group}: {e}')
    for group in (g for g in remove if g in current):
        try:
            cognito.admin_remove_user_from_group(UserPoolId=USER_POOL_ID, Username=username, GroupName=group)
            removed.append(group)
        except Exception as e:
            errors.append(f'Could not remove {group}: {e}')

    print(f"[GROUPS] {username} added={added} removed={removed} by {actor}")
    return {
        'userName': username,
        'success':  not errors,
        'added':    added,
        'removed':  removed,
        'groups':   list_user_groups(username) or [],
        'errors':   errors,
    }

# -------------------------------------------------------
# Who is calling, and what may they do?
#
# A signed-in portal user is identified by the token the authorizer has already verified (it is
# only decoded here). Callers that use the API key (Postman, backend jobs) carry no user token:
# they are trusted, as before, and none of the rules below apply to them.
# -------------------------------------------------------
HELPDESK_GROUPS = {'HELPDESK', 'SUPER_ADMIN'}
ADMIN_GROUPS    = {'SUPER_ADMIN'}

# -------------------------------------------------------
# Which groups are portal groups? The groups say so themselves.
#
# Cognito groups have no "type", and organisation groups (one per broker firm) share the same
# namespace. A group is a PORTAL group — one this API may grant or remove — when its DESCRIPTION
# starts with the marker below. Create one like this:
#     aws cognito-idp create-group ... --description "[portal] Can view email delivery logs"
# Organisation groups are created by broker-firms-api with the firm's display name as their
# description, so they are never portal groups. No marker = not manageable (safe by default).
# -------------------------------------------------------
PORTAL_GROUP_MARKER        = os.environ.get('PORTAL_GROUP_MARKER', '').strip() or '[portal]'
PORTAL_GROUP_CACHE_SECONDS = 60

_portal_cache = { 'at': 0.0, 'groups': None }        # name -> hint (the text after the marker)

def is_portal_group(description):
    return isinstance(description, str) and description.strip().lower().startswith(PORTAL_GROUP_MARKER.lower())

def group_hint(description):
    return description.strip()[len(PORTAL_GROUP_MARKER):].strip(' -—:')

def portal_groups(force=False):
    """name -> hint for every portal group in the pool. Looked up in Cognito and kept for a minute,
    so a newly created group shows up on its own. If Cognito cannot be asked, the last list is used."""
    cached = _portal_cache['groups']
    if cached is not None and not force and time.time() - _portal_cache['at'] < PORTAL_GROUP_CACHE_SECONDS:
        return cached
    try:
        found, token = {}, None
        while True:
            kwargs = { 'UserPoolId': USER_POOL_ID, 'Limit': 60 }
            if token:
                kwargs['NextToken'] = token
            response = cognito.list_groups(**kwargs)
            for group in response.get('Groups', []):
                if is_portal_group(group.get('Description')):
                    found[group['GroupName']] = group_hint(group['Description'])
            token = response.get('NextToken')
            if not token:
                break
    except Exception as e:
        if cached is not None:
            print(f'[GROUPS] could not refresh the portal groups, using the last list: {e}')
            return cached
        raise
    _portal_cache.update(at=time.time(), groups=found)
    if not found:
        print(f'[GROUPS] WARNING: no group has a description starting with {PORTAL_GROUP_MARKER}')
    return found

# A user's organisation value becomes the NAME of a Cognito group they are put in (create_user and
# update_user do that). Organisation groups and portal groups share one namespace, so an
# organisation named like a portal group would hand that group over. Always protected: the groups
# this API's own rules rely on (POLICY_GROUPS) — even if nobody marked them — plus every marked group.
def portal_group_clash(organisation):
    name = organisation.strip().upper() if isinstance(organisation, str) else ''
    if not name:
        return False
    if name in {g.upper() for g in POLICY_GROUPS}:
        return True
    try:
        return name in {g.upper() for g in portal_groups()}
    except Exception as e:
        print(f'[GROUPS] could not check the portal groups ({e}); only the groups this API relies on are protected')
        return False

# What a HELPDESK user may change on someone else's record. Anything else (email, organisation, ...)
# needs SUPER_ADMIN — changing an email address and then resetting the password is an account takeover.
HELPDESK_EDITABLE_FIELDS = {'givenName', 'sn', 'phoneNumber', 'frUnindexedString1'}

# (method, path) -> (groups that may call it, may the caller NOT use it on their own account?)
ROUTE_POLICY = {
    ('GET',    '/users'):                            (HELPDESK_GROUPS,       False),
    ('GET',    '/users/{username}'):                 (HELPDESK_GROUPS,       False),
    ('GET',    '/users/{username}/email-logs'):      (HELPDESK_GROUPS,       False),
    ('GET',    '/users/{username}/groups'):          (HELPDESK_GROUPS,       False),
    ('PUT',    '/users/{username}'):                 (HELPDESK_GROUPS,       True),
    ('POST',   '/users/{username}/reset-password'):  (HELPDESK_GROUPS,       True),
    ('POST',   '/users/{username}/email-unblock'):   (HELPDESK_GROUPS,       True),
    ('PUT',    '/users/{username}/groups'):          (ADMIN_GROUPS,          True),
    ('POST',   '/users'):                            (ADMIN_GROUPS,          False),
    ('POST',   '/users/bulk'):                       (ADMIN_GROUPS,          False),
    ('DELETE', '/users/{username}'):                 (ADMIN_GROUPS,          True),
    ('GET',    '/email-logs'):                       (EMAIL_LOG_VIEW_GROUPS, False),
}

# every group this API's own rules refer to
POLICY_GROUPS = set().union(*(allowed for allowed, _ in ROUTE_POLICY.values()))

def caller_identity(event):
    """Who is calling, from the token in the Authorization header. None = no user token (API key)."""
    headers = event.get('headers') or {}
    auth    = headers.get('authorization') or headers.get('Authorization') or ''
    if not auth.lower().startswith('bearer '):
        return None
    try:
        payload_b64 = auth.split(' ', 1)[1].split('.')[1]
        payload     = json.loads(base64.urlsafe_b64decode(payload_b64 + '=' * (-len(payload_b64) % 4)))
        groups      = payload.get('cognito:groups') or []
    except Exception:
        return { 'groups': [], 'username': '', 'email': '', 'sub': '' }     # unreadable → nobody → denied
    return {
        'groups':   [groups] if isinstance(groups, str) else list(groups),
        'username': str(payload.get('cognito:username') or payload.get('username') or ''),
        'email':    str(payload.get('email') or ''),
        'sub':      str(payload.get('sub') or ''),
    }

def actor_of(identity, claimed=''):
    """Who to record as having done something. A signed-in user is named by their verified token —
    never by what the request body claims."""
    if identity is not None:
        return identity['email'] or identity['username'] or 'unknown'
    return claimed or 'unknown'

def is_self(identity, username):
    """True when the signed-in user is the account being changed."""
    mine   = { identity['username'].lower(), identity['email'].lower() } - { '' }
    if username.strip().lower() in mine:
        return True
    # the Cognito username can differ from the login name / email: compare the account itself
    user = get_user(username)
    if not user:
        return False
    attrs = user['attributes']
    if identity['sub'] and attrs.get('sub') == identity['sub']:
        return True
    return bool(attrs.get('email')) and attrs['email'].lower() in mine

def forbidden(message, code='FORBIDDEN'):
    return resp(403, { 'success': False, 'errorCode': code, 'errors': [message] })

def authorize(identity, method, path, username, body):
    """None to carry on, or a 403 response."""
    if identity is None:
        return None
    policy = ROUTE_POLICY.get((method, path))
    if policy is None:
        return None
    allowed, protect_self = policy
    groups = set(identity['groups'])

    if not (allowed & groups):
        return forbidden('You do not have access to this action')

    if protect_self and username and is_self(identity, username):
        return forbidden('You cannot change your own account. Ask another administrator.', 'SELF_CHANGE_FORBIDDEN')

    if (method, path) == ('PUT', '/users/{username}') and not (ADMIN_GROUPS & groups):
        blocked = sorted(set(body) - HELPDESK_EDITABLE_FIELDS - { 'triggeredBy' })
        if blocked:
            return forbidden(f"Changing {', '.join(blocked)} needs the SUPER_ADMIN group")

    return None

# -------------------------------------------------------
# Email delivery activity across all recipients
# -------------------------------------------------------
EMAIL_LOG_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}(T[0-9:.+\-Z]+)?$')

def _iso_bound(value, end_of_day, name):
    value = (value or '').strip()
    if not value:
        return None
    if not EMAIL_LOG_DATE.match(value):
        raise BadRequest(f'{name} must be YYYY-MM-DD or an ISO timestamp')
    if 'T' not in value:
        return value + ('T23:59:59.999Z' if end_of_day else 'T00:00:00.000Z')
    return value

def _plain(value):
    # DynamoDB returns numbers as Decimal — keep them numbers in the JSON
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value

def _email_log_item(raw):
    item    = {k: _plain(v) for k, v in raw.items() if k != 'ttl'}
    details = item.get('details')
    if isinstance(details, str):                   # the logger stores the SES event as a JSON string
        try:
            parsed = json.loads(details)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            item['details'] = parsed               # otherwise leave the raw string as it was
    return item

def _event_time(item):
    details = item.get('details')
    when    = details.get('timestamp') if isinstance(details, dict) else None
    return str(when or item.get('timestamp') or '')

def _scan_deadline(context):
    budget = 8.0
    if context is not None and hasattr(context, 'get_remaining_time_in_millis'):
        budget = max(1.0, context.get_remaining_time_in_millis() / 1000 - 1.5)
    return time.time() + budget

def _read_email_logs(email, search, ts_from, ts_to, deadline):
    # One recipient → an indexed Query. Otherwise a filtered Scan (fine for modest volumes —
    # see the note in the delivery notes about a GSI before this grows large).
    kwargs = {}
    if email:
        cond = Key('email').eq(email)
        if ts_from and ts_to: cond = cond & Key('timestamp').between(ts_from, ts_to)
        elif ts_from:         cond = cond & Key('timestamp').gte(ts_from)
        elif ts_to:           cond = cond & Key('timestamp').lte(ts_to)
        kwargs['KeyConditionExpression'] = cond
        kwargs['ScanIndexForward']       = False
        read = email_logs_table.query
    else:
        flt = None
        for cond in (
            Attr('timestamp').gte(ts_from) if ts_from else None,
            Attr('timestamp').lte(ts_to)   if ts_to   else None,
            Attr('email').contains(search) if search  else None,
        ):
            if cond is not None:
                flt = cond if flt is None else flt & cond
        if flt is not None:
            kwargs['FilterExpression'] = flt
        read = email_logs_table.scan

    items, scanned, incomplete = [], 0, False
    while True:
        response = read(**kwargs)
        page     = response.get('Items', [])
        items.extend(page)
        scanned += response.get('ScannedCount', len(page))
        last = response.get('LastEvaluatedKey')
        if not last:
            break
        if scanned >= EMAIL_LOG_MAX_SCAN or time.time() > deadline:
            incomplete = True
            break
        kwargs['ExclusiveStartKey'] = last
    return items, incomplete

def search_email_logs(params, context=None):
    statuses = [s.strip() for s in (params.get('status') or '').split(',') if s.strip()]
    for s in statuses:
        if not re.fullmatch(r'[A-Za-z]{2,30}', s):
            raise BadRequest('status must be a comma-separated list of event types, e.g. Bounce,Delivery')

    email   = (params.get('email')  or '').strip().lower()
    search  = (params.get('search') or '').strip().lower()
    ts_from = _iso_bound(params.get('from'), False, 'from')
    ts_to   = _iso_bound(params.get('to'),   True,  'to')

    try:
        limit = int(params.get('limit') or EMAIL_LOG_DEFAULT_LIMIT)
    except ValueError:
        raise BadRequest('limit must be a whole number')
    if limit < 1:
        raise BadRequest('limit must be at least 1')
    limit = min(limit, EMAIL_LOG_MAX_ITEMS)

    raw, incomplete = _read_email_logs(email, search, ts_from, ts_to, _scan_deadline(context))
    items = [_email_log_item(r) for r in raw]

    # the mix of statuses for this window — ignores the status filter, so the counts stay visible while filtering
    summary = {}
    for item in items:
        key = item.get('eventType') or 'Unknown'
        summary[key] = summary.get(key, 0) + 1

    if statuses:
        wanted = {s.lower() for s in statuses}
        items  = [i for i in items if str(i.get('eventType') or '').lower() in wanted]

    items.sort(key=_event_time, reverse=True)
    total = len(items)
    return {
        'items':      items[:limit],
        'count':      min(total, limit),
        'total':      total,
        'summary':    summary,
        'truncated':  total > limit,     # more matched than were returned — narrow the filters
        'incomplete': incomplete,        # the table was too large to read in full
    }

# -------------------------------------------------------
# Lambda handler
# -------------------------------------------------------
def lambda_handler(event, context):
    print('Event:', json.dumps(event))
    try:
        return route(event, context)
    except BadRequest as e:
        return resp(400, { 'success': False, 'errorCode': 'BAD_REQUEST', 'errors': [str(e)] })

def route(event, context=None):
    route_key        = event.get('routeKey', 'GET /users')
    method           = route_key.split(' ')[0]
    path             = route_key.split(' ')[1]
    body             = parse_body(event)
    path_parameters  = event.get('pathParameters') or {}
    query_parameters = event.get('queryStringParameters') or {}
    username         = unquote(path_parameters.get('username', ''))

    identity = caller_identity(event)
    denied   = authorize(identity, method, path, username, body)
    if denied:
        return denied

    # POST /users/bulk
    if method == 'POST' and path == '/users/bulk':
        users = body.get('users', [])
        if not users:
            return resp(400, { 'error': 'users array is required' })
        if not isinstance(users, list):
            return resp(400, { 'error': 'users must be an array' })
        if len(users) > 500:
            return resp(400, { 'error': 'maximum 500 users per bulk request' })
        if not all(isinstance(u, dict) for u in users):
            raise BadRequest('users must be an array of objects')
        result = bulk_create_users(users)
        return resp(200, result)

    # POST /users
    elif method == 'POST' and path == '/users':
        if not body.get('userName'):
            return resp(400, { 'error': 'userName is required' })
        if not body.get('mail'):
            return resp(400, { 'error': 'mail (email) is required' })
        result = create_user(body)
        return respond_with(result, 201)

    # GET /users — paginated
    elif method == 'GET' and path == '/users':
        limit            = parse_limit(query_parameters)
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
        triggered_by = actor_of(identity, body.pop('triggeredBy', ''))
        result = update_user(username, body, triggered_by)
        return respond_with(result)

    # DELETE /users/{username}
    elif method == 'DELETE' and path == '/users/{username}':
        if not DELETE_ENABLED:
            return resp(403, { 'error': 'Delete operation is disabled' })
        if not username:
            return resp(400, { 'error': 'username is required' })
        result = delete_user(username)
        return respond_with(result)

    # POST /users/{username}/reset-password
    elif method == 'POST' and path == '/users/{username}/reset-password':
        if not username:
            return resp(400, { 'error': 'username is required' })
        triggered_by = actor_of(identity, body.get('triggeredBy', ''))
        result = reset_user_password(username, triggered_by)
        return respond_with(result)

    # POST /users/{username}/email-unblock
    elif method == 'POST' and path == '/users/{username}/email-unblock':
        if not username:
            return resp(400, { 'error': 'username is required' })
        triggered_by = actor_of(identity, body.get('triggeredBy', ''))
        result = unblock_email(username, triggered_by)
        return respond_with(result)

    # GET /users/{username}/email-logs
    elif method == 'GET' and path == '/users/{username}/email-logs':
        if not username:
            return resp(400, { 'error': 'username is required' })
        logs = get_email_logs(username)
        return resp(200, { 'email': username, 'logs': logs })

    # GET /users/{username}/groups
    elif method == 'GET' and path == '/users/{username}/groups':
        if not username:
            return resp(400, { 'error': 'username is required' })
        groups = list_user_groups(username)
        if groups is None:
            return resp(404, not_found(username))
        try:
            portal = portal_groups()
        except Exception as e:
            return resp(500, { 'success': False, 'errors': [f'Could not look up the portal groups: {e}'] })
        return resp(200, { 'userName': username, 'groups': groups, 'manageable': sorted(portal), 'hints': portal })

    # PUT /users/{username}/groups — grant / remove portal groups (SUPER_ADMIN, never on yourself)
    elif method == 'PUT' and path == '/users/{username}/groups':
        if not username:
            return resp(400, { 'error': 'username is required' })
        triggered_by = actor_of(identity, body.get('triggeredBy', ''))
        result = update_user_groups(username, body, triggered_by)
        return respond_with(result)

    # GET /email-logs — delivery activity across all recipients (EMAIL_LOGS or SUPER_ADMIN)
    elif method == 'GET' and path == '/email-logs':
        return resp(200, search_email_logs(query_parameters, context))

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
