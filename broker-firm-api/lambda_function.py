import boto3
import json
import os
import time
from datetime import datetime

# -------------------------------------------------------
# Config
# -------------------------------------------------------
USER_POOL_ID   = os.environ.get('USER_POOL_ID',   'ap-southeast-1_vi0pVitMh')
TABLE_NAME     = os.environ.get('TABLE_NAME',     'BrokerFirms')
REGION         = os.environ.get('REGION',         'ap-southeast-1')
DELETE_ENABLED = os.environ.get('DELETE_ENABLED', 'false').lower() == 'true'

cognito  = boto3.client('cognito-idp', region_name=REGION)
dynamodb = boto3.resource('dynamodb', region_name=REGION)
table    = dynamodb.Table(TABLE_NAME)

# -------------------------------------------------------
# Create single broker firm
# -------------------------------------------------------
def create_broker_firm(firm):
    errors = []

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
            'uen':     uen,
            'success': False,
            'errors':  ['Broker firm already exists']
        }
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

        uen = firm.get('uen', '').strip()

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
        elif result['errors'] and result['errors'][0] == 'Broker firm already exists':
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
        errors.append(f"Broker firm not found: {uen}")
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
    errors = []

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
        errors.append(f"Broker firm not found in DynamoDB: {uen}")
    except Exception as e:
        errors.append(f"DynamoDB error: {str(e)}")

    return {
        'uen':     uen,
        'success': len(errors) == 0,
        'errors':  errors
    }

# -------------------------------------------------------
# Lambda handler
# -------------------------------------------------------
def lambda_handler(event, context):
    print('Event:', json.dumps(event))

    route_key       = event.get('routeKey', 'GET /broker-firms')
    method          = route_key.split(' ')[0]
    path            = route_key.split(' ')[1]
    body            = json.loads(event.get('body') or '{}')
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
        return resp(201 if result['success'] else 500, result)

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
        return resp(200 if result['success'] else 500, result)

    # DELETE /broker-firms/{brokerId}
    elif method == 'DELETE' and path == '/broker-firms/{brokerId}':
        if not DELETE_ENABLED:
            return resp(403, { 'error': 'Delete operation is disabled' })
        if not uen:
            return resp(400, { 'error': 'uen is required' })
        result = delete_broker_firm(uen)
        return resp(200 if result['success'] else 500, result)

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