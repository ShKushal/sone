import boto3
import json
import os
import re
import time
from datetime import datetime

# -------------------------------------------------------
# Config
# -------------------------------------------------------
USER_POOL_ID   = os.environ.get('USER_POOL_ID',   'ap-southeast-1_vi0pVitMh')
TABLE_NAME     = os.environ.get('TABLE_NAME',     'BrokerFirms')
REGION         = os.environ.get('REGION',         'ap-southeast-1')
DELETE_ENABLED = os.environ.get('DELETE_ENABLED', 'false').lower() == 'true'

# errorCode → HTTP status. Anything not listed here is a 500.
ERROR_STATUS = {
    'BROKER_FIRM_EXISTS':    409,
    'BROKER_FIRM_NOT_FOUND': 404,
    'BAD_REQUEST':           400,
}

# Broker firm payload fields — a caller must send each of these as a string
FIRM_STRING_FIELDS = ('uen', 'name', 'displayName', 'description', 'address', 'country')

# Update bodies become DynamoDB expression placeholders (#key / :key),
# so every key must be a plain identifier
FIELD_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_]+$')

# -------------------------------------------------------
# Request validation helpers
# -------------------------------------------------------
# Raised for a malformed request; lambda_handler turns it into a 400
class BadRequest(Exception):
    pass

def bad_request(errors, **extra):
    return { **extra, 'success': False, 'errorCode': 'BAD_REQUEST', 'errors': errors }

def not_found(uen, message):
    return {
        'uen':       uen,
        'success':   False,
        'errorCode': 'BROKER_FIRM_NOT_FOUND',
        'errors':    [message]
    }

def validate_string_fields(payload):
    return [
        f'{field} must be a string'
        for field in FIRM_STRING_FIELDS
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

def respond_with(result, success_status=200):
    if result['success']:
        return resp(success_status, result)
    return resp(ERROR_STATUS.get(result.get('errorCode'), 500), result)

cognito  = boto3.client('cognito-idp', region_name=REGION)
dynamodb = boto3.resource('dynamodb', region_name=REGION)
table    = dynamodb.Table(TABLE_NAME)

# -------------------------------------------------------
# Create single broker firm
# -------------------------------------------------------
def create_broker_firm(firm):
    errors = []

    # a wrong-typed field would crash on .strip() / be rejected by Cognito — reject it up front
    problems = validate_string_fields(firm)
    if problems:
        return bad_request(problems)

    uen = firm.get('uen', '').strip()

    try:
        cognito.create_group(
            UserPoolId=USER_POOL_ID,
            GroupName=uen,
            Description=firm.get('displayName', '')
        )
        print(f"Cognito group created: {uen}")
    except cognito.exceptions.GroupExistsException:
        print(f"Cognito group exists: {uen} — skipping")
        return {
            'uen':       uen,
            'success':   False,
            'errorCode': 'BROKER_FIRM_EXISTS',
            'errors':    ['Broker firm already exists']
        }
    except cognito.exceptions.InvalidParameterException as e:
        # e.g. a UEN with whitespace — stop here so no orphan DynamoDB record is written
        message = e.response['Error']['Message']
        print(f"Invalid parameter for {uen}: {message}")
        return bad_request([message], uen=uen)
    except Exception as e:
        errors.append(f"Cognito error: {str(e)}")

    try:
        table.put_item(
            Item={
                'UEN':         uen,
                'name':        firm.get('name', ''),
                'displayName': firm.get('displayName', ''),
                'description': firm.get('description', ''),
                'address':     firm.get('address', ''),
                'country':     firm.get('country', ''),
                'createdAt':   datetime.utcnow().isoformat(),
                'updatedAt':   datetime.utcnow().isoformat()
            }
        )
        print(f"DynamoDB record created: {uen}")
    except Exception as e:
        errors.append(f"DynamoDB error: {str(e)}")

    return {
        'uen':     uen,
        'success': len(errors) == 0,
        'errors':  errors
    }

# -------------------------------------------------------
# Bulk create broker firms
# -------------------------------------------------------
def bulk_create_broker_firms(firms):
    success = []
    failed  = []
    skipped = []
    total   = len(firms)

    print(f"Bulk create started: {total} broker firms")

    for i, firm in enumerate(firms):
        if i > 0 and i % 8 == 0:
            print(f"Rate limit pause after {i}/{total} firms")
            time.sleep(1)

        raw_uen = firm.get('uen', '')
        uen     = raw_uen.strip() if isinstance(raw_uen, str) else ''

        problems = validate_string_fields(firm)
        if problems:
            failed.append({ 'uen': uen or 'unknown', 'errors': problems })
            continue

        if not uen:
            failed.append({ 'uen': 'unknown', 'errors': ['uen is required'] })
            continue
        if not firm.get('name'):
            failed.append({ 'uen': uen, 'errors': ['name is required'] })
            continue
        if not firm.get('displayName'):
            failed.append({ 'uen': uen, 'errors': ['displayName is required'] })
            continue

        max_retries = 3
        result      = None

        for attempt in range(max_retries):
            result = create_broker_firm(firm)
            if (not result['success'] and
                    result['errors'] and
                    'Rate limit' in str(result['errors'])):
                wait = 2 ** attempt
                print(f"Retrying {uen} in {wait}s")
                time.sleep(wait)
            else:
                break

        if result['success']:
            success.append(uen)
        elif result.get('errorCode') == 'BROKER_FIRM_EXISTS':
            skipped.append(uen)
        else:
            failed.append({ 'uen': uen, 'errors': result['errors'] })

    print(f"Bulk complete: {len(success)} created, {len(failed)} failed, {len(skipped)} skipped")

    return {
        'total':   total,
        'created': len(success),
        'failed':  len(failed),
        'skipped': len(skipped),
        'results': { 'success': success, 'failed': failed, 'skipped': skipped }
    }

# -------------------------------------------------------
# Get one
# -------------------------------------------------------
def get_broker_firm(uen):
    response = table.get_item(Key={ 'UEN': uen })
    return response.get('Item')

# -------------------------------------------------------
# List all
# -------------------------------------------------------
def list_broker_firms():
    response = table.scan()
    return response.get('Items', [])

# -------------------------------------------------------
# Update
# -------------------------------------------------------
def update_broker_firm(uen, updates):
    errors = []

    updates.pop('uen',       None)
    updates.pop('UEN',       None)
    updates.pop('createdAt', None)

    problems  = validate_string_fields(updates)
    problems += [
        f"'{key}' is not a valid field name"
        for key in updates
        if not FIELD_NAME_PATTERN.match(key)
    ]
    if problems:
        return bad_request(problems, uen=uen)

    updates['updatedAt'] = datetime.utcnow().isoformat()

    try:
        update_expr  = 'SET ' + ', '.join(f'#{k} = :{k}' for k in updates)
        expr_names   = { f'#{k}': k for k in updates }
        expr_values  = { f':{k}': v for k, v in updates.items() }

        table.update_item(
            Key={ 'UEN': uen },
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ConditionExpression='attribute_exists(UEN)'
        )
        print(f"DynamoDB updated: {uen}")
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return not_found(uen, f"Broker firm not found: {uen}")
    except Exception as e:
        errors.append(f"DynamoDB error: {str(e)}")

    if 'displayName' in updates and not errors:
        try:
            cognito.update_group(
                UserPoolId=USER_POOL_ID,
                GroupName=uen,
                Description=updates['displayName']
            )
            print(f"Cognito group updated: {uen}")
        except Exception as e:
            errors.append(f"Cognito error: {str(e)}")

    return {
        'uen':     uen,
        'success': len(errors) == 0,
        'errors':  errors
    }

# -------------------------------------------------------
# Delete
# -------------------------------------------------------
def delete_broker_firm(uen):
    errors  = []
    missing = False

    try:
        cognito.delete_group(
            UserPoolId=USER_POOL_ID,
            GroupName=uen
        )
        print(f"Cognito group deleted: {uen}")
    except cognito.exceptions.ResourceNotFoundException:
        print(f"Cognito group not found: {uen} — skipping")
    except Exception as e:
        errors.append(f"Cognito error: {str(e)}")

    try:
        table.delete_item(
            Key={ 'UEN': uen },
            ConditionExpression='attribute_exists(UEN)'
        )
        print(f"DynamoDB record deleted: {uen}")
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        missing = True
        errors.append(f"Broker firm not found in DynamoDB: {uen}")
    except Exception as e:
        errors.append(f"DynamoDB error: {str(e)}")

    result = {
        'uen':     uen,
        'success': len(errors) == 0,
        'errors':  errors
    }
    # only a 404 when "not found" is the sole problem
    if missing and len(errors) == 1:
        result['errorCode'] = 'BROKER_FIRM_NOT_FOUND'
    return result

# -------------------------------------------------------
# Lambda handler
# -------------------------------------------------------
def lambda_handler(event, context):
    print('Event:', json.dumps(event))
    try:
        return route(event)
    except BadRequest as e:
        return resp(400, { 'success': False, 'errorCode': 'BAD_REQUEST', 'errors': [str(e)] })

def route(event):
    route_key       = event.get('routeKey', 'GET /broker-firms')
    method          = route_key.split(' ')[0]
    path            = route_key.split(' ')[1]
    body            = parse_body(event)
    path_parameters = event.get('pathParameters') or {}
    uen             = path_parameters.get('brokerId')

    # POST /broker-firms/bulk
    if method == 'POST' and path == '/broker-firms/bulk':
        firms = body.get('firms', [])
        if not firms:
            return resp(400, { 'error': 'firms array is required' })
        if not isinstance(firms, list):
            return resp(400, { 'error': 'firms must be an array' })
        if len(firms) > 500:
            return resp(400, { 'error': 'maximum 500 firms per bulk request' })
        if not all(isinstance(f, dict) for f in firms):
            raise BadRequest('firms must be an array of objects')
        result = bulk_create_broker_firms(firms)
        return resp(200, result)

    # POST /broker-firms
    elif method == 'POST' and path == '/broker-firms':
        if not body.get('uen'):
            return resp(400, { 'error': 'uen is required' })
        if not body.get('name'):
            return resp(400, { 'error': 'name is required' })
        if not body.get('displayName'):
            return resp(400, { 'error': 'displayName is required' })
        result = create_broker_firm(body)
        return respond_with(result, 201)

    # GET /broker-firms
    elif method == 'GET' and path == '/broker-firms':
        firms = list_broker_firms()
        return resp(200, firms)

    # GET /broker-firms/{brokerId}
    elif method == 'GET' and path == '/broker-firms/{brokerId}':
        if not uen:
            return resp(400, { 'error': 'uen is required' })
        firm = get_broker_firm(uen)
        return resp(200 if firm else 404, firm or { 'error': 'Broker firm not found' })

    # PUT /broker-firms/{brokerId}
    elif method == 'PUT' and path == '/broker-firms/{brokerId}':
        if not uen:
            return resp(400, { 'error': 'uen is required' })
        if not body:
            return resp(400, { 'error': 'No fields to update' })
        result = update_broker_firm(uen, body)
        return respond_with(result)

    # DELETE /broker-firms/{brokerId}
    elif method == 'DELETE' and path == '/broker-firms/{brokerId}':
        if not DELETE_ENABLED:
            return resp(403, { 'error': 'Delete operation is disabled' })
        if not uen:
            return resp(400, { 'error': 'uen is required' })
        result = delete_broker_firm(uen)
        return respond_with(result)

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
