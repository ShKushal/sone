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
# Who is calling? Cognito groups from the ID token.
# The authorizer has already verified that token, so it is only decoded here.
# Returns None when there is no user token (API-key callers such as Postman or
# backend services), which keep the trust they always had.
# -------------------------------------------------------
def caller_groups(event):
    headers = event.get('headers') or {}
    auth    = headers.get('authorization') or headers.get('Authorization') or ''
    if not auth.lower().startswith('bearer '):
        return None
    try:
        payload_b64 = auth.split(' ', 1)[1].split('.')[1]
        payload     = json.loads(base64.urlsafe_b64decode(payload_b64 + '=' * (-len(payload_b64) % 4)))
        groups      = payload.get('cognito:groups') or []
    except Exception:
        return []                                  # unreadable token → no groups → denied
    return [groups] if isinstance(groups, str) else list(groups)

def can_view_email_logs(event):
    groups = caller_groups(event)
    return groups is None or bool(EMAIL_LOG_VIEW_GROUPS & set(groups))

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
        triggered_by = body.pop('triggeredBy', '')
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
        triggered_by = body.get('triggeredBy', '')
        result = reset_user_password(username, triggered_by)
        return respond_with(result)

    # POST /users/{username}/email-unblock
    elif method == 'POST' and path == '/users/{username}/email-unblock':
        if not username:
            return resp(400, { 'error': 'username is required' })
        triggered_by = body.get('triggeredBy', '')
        result = unblock_email(username, triggered_by)
        return respond_with(result)

    # GET /users/{username}/email-logs
    elif method == 'GET' and path == '/users/{username}/email-logs':
        if not username:
            return resp(400, { 'error': 'username is required' })
        logs = get_email_logs(username)
        return resp(200, { 'email': username, 'logs': logs })

    # GET /email-logs — delivery activity across all recipients (EMAIL_LOGS or SUPER_ADMIN)
    elif method == 'GET' and path == '/email-logs':
        if not can_view_email_logs(event):
            return resp(403, { 'success': False, 'errorCode': 'FORBIDDEN',
                               'errors': ['You do not have access to the email logs'] })
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
