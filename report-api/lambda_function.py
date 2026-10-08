import boto3
import csv
import io
import json
import os
from datetime import datetime, timezone

from forgerock_idm import list_all_users, list_all_organisations
from forgerock_service_auth import get_service_access_token

# -------------------------------------------------------
# Config
# -------------------------------------------------------
USER_POOL_ID = os.environ.get('USER_POOL_ID', 'ap-southeast-1_vi0pVitMh')
TABLE_NAME   = os.environ.get('TABLE_NAME',   'BrokerFirms')
REGION       = os.environ.get('REGION',       'ap-southeast-1')

cognito  = boto3.client('cognito-idp', region_name=REGION)
dynamodb = boto3.resource('dynamodb', region_name=REGION)
table    = dynamodb.Table(TABLE_NAME)

# -------------------------------------------------------
# ForgeRock helpers
# -------------------------------------------------------
def extract_org(fr_user):
    org = fr_user.get('organisation', '')
    if isinstance(org, dict):
        broker_id = org.get('brokerId', '')
        if broker_id:
            return broker_id
        ref = org.get('_ref', '')
        return ref.split('/')[-1] if ref else 'unassigned'
    return org or 'unassigned'


def extract_org_details(fr_user):
    org = fr_user.get('organisation', {})
    if not isinstance(org, dict):
        return {
            'uen':         org or 'unassigned',
            'name':        '',
            'displayName': '',
            'country':     ''
        }
    return {
        'uen':         org.get('brokerId', ''),
        'name':        org.get('name', ''),
        'displayName': org.get('displayName', ''),
        'description': org.get('description', ''),
        'address':     org.get('address', '') or '',
        'country':     org.get('country', '')
    }

def clean(value, default=''):
    """A ForgeRock value as plain text. A missing or null value must never turn into the word "None"."""
    if value is None:
        return default
    text = str(value).strip()
    return default if text in ('', 'None', 'null') else text

# -------------------------------------------------------
# Get all Cognito users
# -------------------------------------------------------
def get_cognito_users():
    users  = {}
    kwargs = { 'UserPoolId': USER_POOL_ID }

    while True:
        response = cognito.list_users(**kwargs)
        for user in response.get('Users', []):
            attrs    = {
                a['Name']: a['Value']
                for a in user.get('Attributes', [])
            }
            username        = user['Username'].lower()
            users[username] = {
                'userName':           user['Username'],
                'status':             user['UserStatus'],
                'enabled':            user['Enabled'],
                'createdAt':          user['UserCreateDate'].isoformat(),
                'lastModified':       user['UserLastModifiedDate'].isoformat(),
                'email':              attrs.get('email', ''),
                'givenName':          attrs.get('given_name', ''),
                'familyName':         attrs.get('family_name', ''),
                'organisation':       attrs.get('custom:organisation', ''),
                'lastLogin':          attrs.get('custom:lastLogin', ''),
                'migrationType':      attrs.get('custom:migrationType', ''),
                'migrated':           attrs.get('custom:migrationType', '') != '',
                'active':             attrs.get('custom:frUnindexedString1', '').upper() == 'TRUE',
                'frUnindexedString2': attrs.get('custom:frUnindexedString2', ''),
                'frUnindexedString3': attrs.get('custom:frUnindexedString3', ''),
                'frUnindexedString5': attrs.get('custom:frUnindexedString5', '')
            }
        if 'PaginationToken' not in response:
            break
        kwargs['PaginationToken'] = response['PaginationToken']

    return users

# -------------------------------------------------------
# Apply filters
# -------------------------------------------------------
def apply_filters(users, params):
    # multi-org support — comma separated
    organisation_raw = params.get('organisation', '').strip()
    organisations    = [
        o.strip() for o in organisation_raw.split(',')
        if o.strip()
    ] if organisation_raw else []

    # multi-status support — comma separated
    status_raw = params.get('status', '').strip().upper()
    statuses   = [
        s.strip() for s in status_raw.split(',')
        if s.strip()
    ] if status_raw else []

    enabled         = params.get('enabled', '').strip().lower()
    from_date       = params.get('fromDate', '').strip()
    to_date         = params.get('toDate', '').strip()
    never_logged    = params.get('neverLoggedIn', '').strip().lower()
    search          = params.get('search', '').strip().lower()
    migrated        = params.get('migrated', '').strip().lower()
    migration_type  = params.get('migrationType', '').strip()
    last_login_from = params.get('lastLoginFrom', '').strip()
    last_login_to   = params.get('lastLoginTo', '').strip()

    filtered = []

    for user in users:

        # multi-org filter
        if organisations and user['organisation'] not in organisations:
            continue

        # multi-status filter
        if statuses:
            # handle ACTIVE/INACTIVE separately
            if 'ACTIVE' in statuses and not user['enabled']:
                continue
            if 'INACTIVE' in statuses and user['enabled']:
                continue
            # handle Cognito status values
            cognito_statuses = [
                s for s in statuses
                if s in ['CONFIRMED', 'FORCE_CHANGE_PASSWORD',
                          'UNCONFIRMED', 'RESET_REQUIRED']
            ]
            if cognito_statuses and user['status'] not in cognito_statuses:
                continue

        if enabled == 'true' and not user['enabled']:
            continue
        if enabled == 'false' and user['enabled']:
            continue
        if never_logged == 'true' and user['lastLogin']:
            continue
        if from_date:
            try:
                if user['createdAt'][:10] < from_date:
                    continue
            except Exception:
                pass
        if to_date:
            try:
                if user['createdAt'][:10] > to_date:
                    continue
            except Exception:
                pass
        if last_login_from:
            try:
                if not user['lastLogin'] or user['lastLogin'][:10] < last_login_from:
                    continue
            except Exception:
                pass
        if last_login_to:
            try:
                if not user['lastLogin'] or user['lastLogin'][:10] > last_login_to:
                    continue
            except Exception:
                pass
        if search:
            searchable = (
                user['userName'].lower() +
                user['email'].lower() +
                user['givenName'].lower() +
                user['familyName'].lower()
            )
            if search not in searchable:
                continue
        if migrated == 'true' and not user['migrated']:
            continue
        if migrated == 'false' and user['migrated']:
            continue
        if migration_type and user['migrationType'] != migration_type:
            continue

        filtered.append(user)

    return filtered

# -------------------------------------------------------
# Build summary
# -------------------------------------------------------
def build_summary(users):
    total        = len(users)
    enabled      = sum(1 for u in users if u['enabled'])
    never_logged = sum(1 for u in users if not u['lastLogin'])
    confirmed    = sum(1 for u in users if u['status'] == 'CONFIRMED')
    force_change = sum(1 for u in users if u['status'] == 'FORCE_CHANGE_PASSWORD')
    migrated     = sum(1 for u in users if u['migrated'])
    by_org       = {}
    by_migration = {}

    for user in users:
        org                 = user['organisation'] or 'unassigned'
        by_org[org]         = by_org.get(org, 0) + 1
        mtype               = user['migrationType'] or 'pending'
        by_migration[mtype] = by_migration.get(mtype, 0) + 1

    return {
        'total':               total,
        'enabled':             enabled,
        'disabled':            total - enabled,
        'neverLoggedIn':       never_logged,
        'confirmed':           confirmed,
        'forceChangePassword': force_change,
        'migrated':            migrated,
        'pendingMigration':    total - migrated,
        'byOrganisation':      by_org,
        'byMigrationType':     by_migration
    }

# -------------------------------------------------------
# Convert to CSV
# -------------------------------------------------------
def to_csv(rows, report_type='users'):
    output = io.StringIO()

    if report_type == 'organisations':
        fieldnames = [
            'uen', 'name', 'displayName', 'description',
            'address', 'country', 'memberCount',
            'createdAt', 'updatedAt'
        ]
    elif report_type == 'forgerock_orgs':
        fieldnames = [
            'uen', 'name', 'displayName',
            'description', 'country', 'address'
        ]
    elif report_type == 'migration_gap':
        fieldnames = [
            'userName', 'email', 'givenName', 'familyName',
            'organisation', 'orgDisplayName', 'accountStatus', 'country'
        ]
    elif report_type == 'migration':
        fieldnames = [
            'userName', 'email', 'givenName', 'familyName',
            'organisation', 'status', 'enabled', 'active',
            'migrationType', 'migrated', 'lastLogin',
            'createdAt', 'lastModified'
        ]
    else:
        fieldnames = [
            'userName', 'email', 'givenName', 'familyName',
            'organisation', 'status', 'enabled', 'active',
            'migrationType', 'migrated', 'lastLogin',
            'createdAt', 'lastModified',
            'frUnindexedString2', 'frUnindexedString3',
            'frUnindexedString5'
        ]

    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        extrasaction='ignore',
        lineterminator='\n'
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()

# -------------------------------------------------------
# PERMANENT report handlers
# -------------------------------------------------------
def handle_users_report(params, fmt):
    try:
        all_users = list(get_cognito_users().values())
        filtered  = apply_filters(all_users, params)
        summary   = build_summary(filtered)

        if fmt == 'csv':
            return resp_csv(
                to_csv(filtered, 'users'),
                f'users-report-{today()}.csv'
            )
        return resp(200, {
            'generatedAt': now(),
            'filters':     params,
            'summary':     summary,
            'count':       len(filtered),
            'users':       filtered
        })
    except Exception as e:
        print(f"Users report error: {e}")
        return resp(500, { 'error': str(e) })


def handle_summary_report(params, fmt):
    try:
        all_users = list(get_cognito_users().values())
        filtered  = apply_filters(all_users, params)
        summary   = build_summary(filtered)

        if fmt == 'csv':
            rows   = [{ 'metric': k, 'value': v }
                      for k, v in summary.items()
                      if not isinstance(v, dict)]
            output = io.StringIO()
            writer = csv.DictWriter(
                output,
                fieldnames=['metric', 'value'],
                lineterminator='\n'
            )
            writer.writeheader()
            writer.writerows(rows)
            return resp_csv(output.getvalue(), f'summary-{today()}.csv')

        return resp(200, {
            'generatedAt': now(),
            'filters':     params,
            'summary':     summary
        })
    except Exception as e:
        print(f"Summary report error: {e}")
        return resp(500, { 'error': str(e) })


def handle_organisations_report(params, fmt):
    """
    Full org details from DynamoDB (UEN as PK)
    + member count from Cognito
    Supports multi-uen and multi-country filters
    """
    try:
        response      = table.scan()
        all_firms     = response.get('Items', [])
        cognito_users = list(get_cognito_users().values())

        # member count per org
        member_counts = {}
        for user in cognito_users:
            org                = user['organisation'] or 'unassigned'
            member_counts[org] = member_counts.get(org, 0) + 1

        # multi-uen filter — comma separated
        uen_raw  = params.get('uen', '').strip()
        uens     = [
            u.strip() for u in uen_raw.split(',')
            if u.strip()
        ] if uen_raw else []

        # multi-country filter — comma separated
        country_raw = params.get('country', '').strip()
        countries   = [
            c.strip() for c in country_raw.split(',')
            if c.strip()
        ] if country_raw else []

        search = params.get('search', '').strip().lower()

        org_list = []
        for firm in all_firms:
            uen = firm.get('UEN', '')

            if uens and uen not in uens:
                continue
            if countries and firm.get('country', '') not in countries:
                continue
            if search:
                searchable = (
                    uen.lower() +
                    firm.get('name', '').lower() +
                    firm.get('displayName', '').lower()
                )
                if search not in searchable:
                    continue

            org_list.append({
                'uen':         uen,
                'name':        firm.get('name', ''),
                'displayName': firm.get('displayName', ''),
                'description': firm.get('description', ''),
                'address':     firm.get('address', ''),
                'country':     firm.get('country', ''),
                'memberCount': member_counts.get(uen, 0),
                'createdAt':   firm.get('createdAt', ''),
                'updatedAt':   firm.get('updatedAt', '')
            })

        org_list.sort(key=lambda x: x['memberCount'], reverse=True)

        by_country = {}
        for org in org_list:
            c             = org['country'] or 'unknown'
            by_country[c] = by_country.get(c, 0) + 1

        summary = {
            'totalOrganisations': len(org_list),
            'totalMembers':       sum(o['memberCount'] for o in org_list),
            'byCountry':          by_country
        }

        if fmt == 'csv':
            return resp_csv(
                to_csv(org_list, 'organisations'),
                f'organisations-report-{today()}.csv'
            )

        return resp(200, {
            'generatedAt':   now(),
            'filters':       params,
            'summary':       summary,
            'count':         len(org_list),
            'organisations': org_list
        })

    except Exception as e:
        print(f"Organisations report error: {e}")
        return resp(500, { 'error': str(e) })


def handle_migration_report(params, fmt):
    try:
        all_users = list(get_cognito_users().values())
        filtered  = apply_filters(all_users, params)
        summary   = build_summary(filtered)

        if fmt == 'csv':
            return resp_csv(
                to_csv(filtered, 'migration'),
                f'migration-report-{today()}.csv'
            )
        return resp(200, {
            'generatedAt': now(),
            'filters':     params,
            'summary': {
                'total':            summary['total'],
                'migrated':         summary['migrated'],
                'pendingMigration': summary['pendingMigration'],
                'byMigrationType':  summary['byMigrationType'],
                'byOrganisation':   summary['byOrganisation']
            },
            'count': len(filtered),
            'users': filtered
        })
    except Exception as e:
        print(f"Migration report error: {e}")
        return resp(500, { 'error': str(e) })


def handle_migration_summary(params, fmt):
    try:
        all_users = list(get_cognito_users().values())
        filtered  = apply_filters(all_users, params)
        summary   = build_summary(filtered)
        org_data  = {}

        for user in filtered:
            org = user['organisation'] or 'unassigned'
            if org not in org_data:
                org_data[org] = {
                    'organisation':     org,
                    'total':            0,
                    'migrated':         0,
                    'pendingMigration': 0,
                    'migratedPct':      0.0
                }
            org_data[org]['total'] += 1
            if user['migrated']:
                org_data[org]['migrated'] += 1
            else:
                org_data[org]['pendingMigration'] += 1

        for org in org_data.values():
            if org['total'] > 0:
                org['migratedPct'] = round(
                    org['migrated'] / org['total'] * 100, 1
                )

        org_list    = list(org_data.values())
        overall_pct = round(
            summary['migrated'] / summary['total'] * 100, 1
        ) if summary['total'] > 0 else 0.0

        if fmt == 'csv':
            output = io.StringIO()
            writer = csv.DictWriter(
                output,
                fieldnames=[
                    'organisation', 'total', 'migrated',
                    'pendingMigration', 'migratedPct'
                ],
                lineterminator='\n'
            )
            writer.writeheader()
            writer.writerows(org_list)
            return resp_csv(output.getvalue(), f'migration-summary-{today()}.csv')

        return resp(200, {
            'generatedAt': now(),
            'filters':     params,
            'overall': {
                'total':            summary['total'],
                'migrated':         summary['migrated'],
                'pendingMigration': summary['pendingMigration'],
                'migratedPct':      overall_pct,
                'byMigrationType':  summary['byMigrationType']
            },
            'byOrganisation': org_list
        })
    except Exception as e:
        print(f"Migration summary error: {e}")
        return resp(500, { 'error': str(e) })


# -------------------------------------------------------
# TEMPORARY handlers — delete after migration cutover
# -------------------------------------------------------
def handle_forgerock_organisations(params, fmt):
    """
    TEMPORARY — delete route after migration cutover.
    Queries ForgeRock managed/organisation directly.
    Compares with DynamoDB BrokerFirms (UEN as PK).
    bulkPayload ready to POST to /broker-firms/bulk.
    """
    try:
        token = get_service_access_token()
        if not token:
            return resp(500, { 'error': 'Failed to get ForgeRock service token' })

        fr_orgs_raw = list_all_organisations(
            fields='name,displayName,description,brokerId,country,address'
        )
        print(f'ForgeRock total orgs: {len(fr_orgs_raw)}')

        fr_orgs = []
        for org in fr_orgs_raw:
            uen = (org.get('brokerId') or '').strip()
            if not uen:
                print(f"Skipping org without brokerId: {org.get('name')}")
                continue
            fr_orgs.append({
                'uen':         uen,
                'name':        org.get('name') or '',
                'displayName': org.get('displayName') or '',
                'description': org.get('description') or '',
                'address':     org.get('address') or '',
                'country':     org.get('country') or ''
            })

        existing_firms = table.scan().get('Items', [])
        existing_uens  = { f.get('UEN', '') for f in existing_firms }

        missing_orgs       = [o for o in fr_orgs if o['uen'] not in existing_uens]
        already_in_cognito = [o for o in fr_orgs if o['uen'] in existing_uens]

        summary = {
            'totalInForgeRock': len(fr_orgs),
            'alreadyInCognito': len(already_in_cognito),
            'missingInCognito': len(missing_orgs)
        }

        bulk_payload = { 'firms': missing_orgs }

        if fmt == 'csv':
            return resp_csv(
                to_csv(fr_orgs, 'forgerock_orgs'),
                f'forgerock-orgs-{today()}.csv'
            )

        return resp(200, {
            'generatedAt':      now(),
            'summary':          summary,
            'allOrgs':          fr_orgs,
            'alreadyInCognito': already_in_cognito,
            'missingOrgs':      missing_orgs,
            'bulkPayload':      bulk_payload
        })

    except Exception as e:
        print(f"ForgeRock orgs error: {e}")
        return resp(500, { 'error': str(e) })


def handle_forgerock_gap(params, fmt):
    """
    TEMPORARY — delete route after migration cutover.
    Users in ForgeRock but NOT in Cognito.
    bulkPayload ready to POST to /users/bulk.
    """
    try:
        token = get_service_access_token()
        if not token:
            return resp(500, { 'error': 'Failed to get ForgeRock service token' })

        fr_users = list_all_users(
            fields='userName,mail,givenName,sn,organisation,accountStatus,country,frUnindexedString1,frUnindexedString2,frUnindexedString5'
        )
        print(f'ForgeRock total users: {len(fr_users)}')

        cognito_users  = get_cognito_users()
        # also match on the email ADDRESS stored in Cognito, not only on the username — otherwise a
        # user who exists under a different username would be created a second time
        cognito_emails = { u['email'].lower() for u in cognito_users.values() if u.get('email') }

        # firms that exist in BrokerFirms — only used to warn about users who point at a firm that does not exist yet
        try:
            known_firms = { f.get('UEN', '') for f in table.scan().get('Items', []) }
        except Exception as e:
            print(f'Could not read BrokerFirms ({e}) — unknownFirms not checked')
            known_firms = None

        missing    = []
        by_org     = {}
        org_source = { 'frUnindexedString5': 0, 'forgerockOrganisation': 0, 'unassigned': 0 }
        conflicts  = []

        for fr_user in fr_users:
            username   = (fr_user.get('userName') or '').lower()
            email      = (fr_user.get('mail') or '').lower()
            in_cognito = (
                username in cognito_users or
                email    in cognito_users or
                (email and email in cognito_emails)
            )

            if not in_cognito:
                org_details = extract_org_details(fr_user)
                fr_org      = org_details['uen'] if org_details['uen'] not in ('', 'unassigned') else ''
                fr5         = clean(fr_user.get('frUnindexedString5'))

                # frUnindexedString5 is the firm field. The ForgeRock organisation object is only the fallback.
                uen         = fr5 or fr_org or 'unassigned'
                source      = 'frUnindexedString5' if fr5 else ('forgerockOrganisation' if fr_org else 'unassigned')
                org_source[source] += 1
                if fr5 and fr_org and fr5 != fr_org:
                    conflicts.append({
                        'userName':              clean(fr_user.get('userName')),
                        'frUnindexedString5':    fr5,
                        'forgerockOrganisation': fr_org
                    })

                missing.append({
                    'userName':           clean(fr_user.get('userName')),
                    'email':              clean(fr_user.get('mail')),
                    'givenName':          clean(fr_user.get('givenName')),
                    'familyName':         clean(fr_user.get('sn')),
                    'organisation':       uen,
                    'orgDisplayName':     org_details['displayName'],
                    'accountStatus':      fr_user.get('accountStatus', ''),
                    'country':            fr_user.get('country', ''),
                    'frUnindexedString1': clean(fr_user.get('frUnindexedString1'), 'TRUE'),
                    'frUnindexedString2': clean(fr_user.get('frUnindexedString2')),
                    'frUnindexedString5': fr5
                })
                by_org[uen] = by_org.get(uen, 0) + 1

        # users whose firm is not in BrokerFirms: they would be created WITHOUT a group
        unknown_firms = None
        if known_firms is not None:
            unknown_firms = {}
            for u in missing:
                if u['organisation'] != 'unassigned' and u['organisation'] not in known_firms:
                    unknown_firms[u['organisation']] = unknown_firms.get(u['organisation'], 0) + 1

        summary = {
            'totalInForgeRock':   len(fr_users),
            'totalInCognito':     len(cognito_users),
            'notInCognito':       len(missing),
            'byOrganisation':     by_org,
            'organisationSource': org_source,
            'firmConflicts':      { 'count': len(conflicts), 'examples': conflicts[:20] },
            'unknownFirms':       unknown_firms
        }

        bulk_payload = {
            'users': [
                {
                    'userName':           u['userName'],
                    'mail':               u['email'],
                    'givenName':          u['givenName'],
                    'sn':                 u['familyName'],
                    'organisation':       u['organisation'],
                    'frUnindexedString1': u['frUnindexedString1'],
                    'frIndexedString2':   u['frUnindexedString2'],
                    'frUnindexedString5': u['frUnindexedString5'],
                    'migrationType':      'BULK'
                }
                for u in missing
            ]
        }

        if fmt == 'csv':
            return resp_csv(
                to_csv(missing, 'migration_gap'),
                f'forgerock-gap-{today()}.csv'
            )

        return resp(200, {
            'generatedAt': now(),
            'filters':     params,
            'summary':     summary,
            'count':       len(missing),
            'users':       missing,
            'bulkPayload': bulk_payload
        })

    except Exception as e:
        print(f"ForgeRock gap error: {e}")
        return resp(500, { 'error': str(e) })


# -------------------------------------------------------
# Lambda handler
# -------------------------------------------------------
def lambda_handler(event, context):
    print('Event:', json.dumps(event))

    route_key = event.get('routeKey', 'GET /reports/users')
    method    = route_key.split(' ')[0]
    path      = route_key.split(' ')[1]
    params    = event.get('queryStringParameters') or {}
    fmt       = params.get('format', 'json').lower()

    if method == 'GET' and path == '/reports/users':
        return handle_users_report(params, fmt)

    elif method == 'GET' and path == '/reports/users/summary':
        return handle_summary_report(params, fmt)

    elif method == 'GET' and path == '/reports/inactive':
        params['neverLoggedIn'] = 'true'
        return handle_users_report(params, fmt)

    elif method == 'GET' and path == '/reports/organisations':
        return handle_organisations_report(params, fmt)

    elif method == 'GET' and path == '/reports/migration':
        return handle_migration_report(params, fmt)

    elif method == 'GET' and path == '/reports/migration/pending':
        params['migrated'] = 'false'
        return handle_migration_report(params, fmt)

    elif method == 'GET' and path == '/reports/migration/complete':
        params['migrated'] = 'true'
        return handle_migration_report(params, fmt)

    elif method == 'GET' and path == '/reports/migration/summary':
        return handle_migration_summary(params, fmt)

    elif method == 'GET' and path == '/reports/forgerock/organisations':
        return handle_forgerock_organisations(params, fmt)

    elif method == 'GET' and path == '/reports/forgerock/gap':
        return handle_forgerock_gap(params, fmt)

    return resp(400, { 'error': 'Invalid report type' })


# -------------------------------------------------------
# Response helpers
# -------------------------------------------------------
def now():
    return datetime.now(timezone.utc).isoformat()

def today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')

def resp(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type':                'application/json',
            'Access-Control-Allow-Origin': '*'
        },
        'body': json.dumps(body, default=str)
    }

def resp_csv(csv_data, filename):
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type':                'text/csv',
            'Content-Disposition':         f'attachment; filename="{filename}"',
            'Access-Control-Allow-Origin': '*'
        },
        'body':            csv_data,
        'isBase64Encoded': False
    }
