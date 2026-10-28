#!/usr/bin/env python3
"""
gap_tool.py — run the ForgeRock gap report straight on the Lambda (no API Gateway, so no 30-second
limit) and cut the result into batches ready for POST /users/bulk.

  python3 gap_tool.py run   [--function reports-api] [--region ap-southeast-1] [--size 25] [--out DIR]
  python3 gap_tool.py run   --url https://<api-id>.execute-api.<region>.amazonaws.com/<stage> [--api-key KEY] [--size 25]
  python3 gap_tool.py split gap.json [--size 25] [--out DIR]       # cut an existing result again

Two ways to run the report
  --function (default)  Calls the Lambda directly with your AWS login (needs boto3 and lambda:InvokeFunction).
                        There is no 30-second limit, so this is the way for thousands of users.
  --url                 Calls the API address with an API key. Needs only Python 3: no AWS login, no boto3.
                        The key is read from --api-key, or the GAP_API_KEY variable, or typed when asked
                        (the last two keep it out of your shell history). The address must be https://.
                        An HTTP API allows a call only 30 seconds, and this report takes about two minutes
                        with 9,000 users, so at that size the API gives up; the tool then says so.

What it does
  1. Invokes the function once with  compact=true  (import payload only, about 2.5 MB for 9,000 users).
     It never retries by itself: a retry would start a second two-minute run.
  2. Checks every record: userName and mail must be present, and nobody may appear twice (by
     user name or by email, ignoring case). Records that would fail the import go to problems.json.
  3. Writes gap.json, batches/batch-0001.json, ... (each {"users": [...]} — the body POST /users/bulk
     takes) and, if needed, problems.json, into a NEW folder. It never overwrites an earlier run.

Batch size: through API Gateway a call must finish in 30 seconds, and creating a user takes about
0.9 s, so use 25. Calling the users function directly, 500 is the most the bulk route accepts.

The folder holds names and email addresses of real people: keep it private and delete it when done.
Needs Python 3 and boto3, with permission to invoke the function (lambda:InvokeFunction).
"""
import argparse, datetime, getpass, json, os, socket, sys, urllib.error, urllib.parse, urllib.request

READ_TIMEOUT = 900          # seconds; the run takes about two minutes for 9,000 users
HTTP_TIMEOUT = 60           # seconds, for --url: API Gateway gives up after 30, so waiting much longer never helps
ROUTE        = 'GET /reports/forgerock/gap'
PATH         = '/reports/forgerock/gap'


class ToolError(Exception):
    pass


def make_client(region):
    import boto3
    from botocore.config import Config
    # total_max_attempts=1: never run the report twice because of a retry
    return boto3.client('lambda', region_name=region,
                        config=Config(read_timeout=READ_TIMEOUT, connect_timeout=10, retries={'total_max_attempts': 1}))


def explain_aws_error(e, function, region):
    """A plain sentence for the usual ways calling the function goes wrong, instead of a traceback."""
    name = type(e).__name__
    response = getattr(e, 'response', None)             # present on every AWS error, but empty on timeouts
    error    = (response.get('Error') or {}) if isinstance(response, dict) else {}
    code     = error.get('Code', '')
    detail   = error.get('Message') or str(e)
    if code in ('AccessDeniedException', 'AccessDenied', 'UnauthorizedOperation'):
        return f'Not allowed to run {function}: the credentials need the permission lambda:InvokeFunction on it. ({detail})'
    if code == 'ResourceNotFoundException':
        return f'No function called {function} in region {region} of this account. Check --function and --region, and that you are in the right AWS account.'
    if code in ('TooManyRequestsException', 'ServiceException', 'ThrottlingException'):
        return f'The Lambda service refused or could not run it right now ({code}). Wait a minute and try again. {detail}'
    if name == 'NoCredentialsError':
        return 'No AWS credentials found. Run this in AWS CloudShell, or configure credentials first.'
    if name in ('ReadTimeoutError',):
        return 'No answer within 15 minutes. The function may still be running: look at its CloudWatch logs before trying again.'
    if name in ('EndpointConnectionError', 'ConnectTimeoutError', 'ConnectionClosedError'):
        return f'Could not reach the Lambda service in {region}. Check the network and the region. ({detail})'
    return f'{name}: {detail or e}'


def run_report(client, function, region='ap-southeast-1'):
    event = {'routeKey': ROUTE, 'queryStringParameters': {'compact': 'true'}}
    try:
        response = client.invoke(FunctionName=function, InvocationType='RequestResponse', Payload=json.dumps(event).encode())
        raw = response['Payload'].read()
    except Exception as e:                      # AWS and network errors only reach here: say what happened, plainly
        raise ToolError(explain_aws_error(e, function, region))
    if response.get('FunctionError'):
        try:
            detail = json.loads(raw).get('errorMessage') or raw[:300]
        except Exception:
            detail = raw[:300]
        raise ToolError(f"The function failed ({response['FunctionError']}): {detail}")
    try:
        outer = json.loads(raw)
    except Exception:
        raise ToolError(f'The answer was not JSON: {raw[:200]!r}')
    if outer.get('statusCode') != 200:
        raise ToolError(f"The report answered with status {outer.get('statusCode')}: {str(outer.get('body'))[:300]}")
    return json.loads(outer['body'])


def redact(text, key):
    return str(text).replace(key, '***') if key else str(text)


def check_url(url):
    p = urllib.parse.urlparse(url.strip())
    local = p.scheme == 'http' and p.hostname in ('localhost', '127.0.0.1')      # plain http only for testing on this machine
    if p.scheme != 'https' and not local:
        raise ToolError('The address must start with https:// (an API key sent over plain http would travel unprotected).')
    if not p.hostname:
        raise ToolError('The address looks incomplete. It should look like https://<api-id>.execute-api.<region>.amazonaws.com/<stage>')
    return url.strip().rstrip('/')


def get_key(cli_key):
    key = (cli_key or os.environ.get('GAP_API_KEY') or '').strip()
    if not key and sys.stdin.isatty():
        key = getpass.getpass('API key (not shown): ').strip()
    if not key:
        raise ToolError('No API key. Set GAP_API_KEY, pass --api-key, or run this where you can type it when asked.')
    return key


def explain_http(status, body, key):
    text = body.decode('utf-8', 'replace') if body else ''
    try:
        parsed = json.loads(text)
        detail = (parsed.get('error') or parsed.get('message') or '') if isinstance(parsed, dict) else ''
    except Exception:
        detail = text[:200]
    detail = redact(detail, key)
    extra = f': {detail}' if detail else ''
    if status in (401, 403):
        return f'The API did not accept the call (HTTP {status}{extra}). Check the API key, and that the address is for the right environment.'
    if status == 404:
        return 'The API has no such route (HTTP 404). Check the address, including the stage name, and that the route GET /reports/forgerock/gap exists.'
    if status == 429:
        return 'The API is limiting requests (HTTP 429). Wait a minute and try again.'
    if status in (503, 504) or 'timed out' in detail.lower():
        return (f'The API gave up waiting (HTTP {status}). An HTTP API allows a call only 30 seconds, and this report takes about two '
                'minutes with thousands of users, so it cannot come back through the API. The function keeps running in the background. '
                'Run the tool the other way instead: python3 gap_tool.py run --function reports-api, in AWS CloudShell (no 30-second limit).')
    if status in (500, 502):
        return f'The report failed on the server (HTTP {status}{extra}). Look at the function\'s CloudWatch logs.'
    return f'Unexpected answer from the API (HTTP {status}{extra}).'


def run_report_http(base, key, timeout):
    request = urllib.request.Request(f'{base}{PATH}?compact=true', headers={'x-api-key': key, 'Accept': 'application/json'}, method='GET')
    host = urllib.parse.urlparse(base).hostname
    try:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as e:                       # the API answered, with an error status
            status, body = e.code, e.read()
    except (socket.timeout, TimeoutError):
        raise ToolError(f'No answer from {host} within {timeout:g} seconds. (An HTTP API gives up after 30; see --function for large runs.)')
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ToolError(f'No answer from {host} within {timeout:g} seconds. (An HTTP API gives up after 30; see --function for large runs.)')
        raise ToolError(redact(f'Could not reach {host}: {e.reason}', key))
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(redact(f'{type(e).__name__}: {e}', key))
    if status != 200:
        raise ToolError(explain_http(status, body, key))
    try:
        return json.loads(body)
    except Exception:
        raise ToolError(f'The API answered 200 but not with JSON: {body[:200]!r}')


def check_records(users):
    """(good, problems): problems are records the import would refuse or that repeat someone."""
    good, problems, seen = [], [], set()
    for u in users:
        name = (u.get('userName') or '').strip() if isinstance(u.get('userName'), str) else ''
        mail = (u.get('mail') or '').strip() if isinstance(u.get('mail'), str) else ''
        if not name or not mail or '@' not in mail:
            problems.append({'reason': 'missing or invalid userName / mail', 'record': u}); continue
        keys = {name.lower(), mail.lower()}
        if keys & seen:
            problems.append({'reason': 'appears more than once (same user name or email)', 'record': u}); continue
        seen |= keys
        good.append(u)
    return good, problems


def write_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))


def cut(report, size, out):
    """Write gap.json, the batches and problems.json into a new folder; return a summary."""
    if size < 1:
        raise ToolError('--size must be at least 1')
    users = (report.get('bulkPayload') or {}).get('users')
    if not isinstance(users, list):
        raise ToolError('The result has no bulkPayload.users — was it run with compact=true?')
    good, problems = check_records(users)
    os.makedirs(os.path.join(out, 'batches'), exist_ok=False)          # a new folder every time
    write_json(os.path.join(out, 'gap.json'), report)
    batches = [good[i:i + size] for i in range(0, len(good), size)]
    for n, batch in enumerate(batches, 1):
        write_json(os.path.join(out, 'batches', f'batch-{n:04d}.json'), {'users': batch})
    if problems:
        write_json(os.path.join(out, 'problems.json'), problems)
    return {'reported': report.get('count'), 'received': len(users), 'good': len(good), 'problems': len(problems), 'batches': len(batches), 'size': size}


def show(report, s, out):
    summary = report.get('summary') or {}
    print(f"\nForgeRock users: {summary.get('totalInForgeRock', '?')}   already in Cognito: {summary.get('totalInCognito', '?')}   to create: {s['reported']}")
    if s['reported'] != s['received']:
        print(f"  WARNING: the report says {s['reported']} but {s['received']} records arrived")
    orgs = sorted((summary.get('byOrganisation') or {}).items(), key=lambda kv: -kv[1])
    if orgs:
        print('  by organisation (frUnindexedString5): ' + ', '.join(f'{k} {v}' for k, v in orgs[:8]) + (f', … {len(orgs)} in all' if len(orgs) > 8 else ''))
    print(f"  ready to import: {s['good']} users in {s['batches']} batches of up to {s['size']}   ->  {os.path.join(out, 'batches')}")
    if s['problems']:
        print(f"  NOT importable: {s['problems']} records (listed with the reason in {os.path.join(out, 'problems.json')})")
    print(f"  everything is in: {out}   (contains personal data — keep private, delete when done)")


def main(argv=None):
    p = argparse.ArgumentParser(description='Run the ForgeRock gap report directly and cut it into import batches.')
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run', help='invoke the function and cut the result')
    r.add_argument('--function', default='reports-api'); r.add_argument('--region', default='ap-southeast-1')
    r.add_argument('--url', help='call this API address with an API key instead of invoking the Lambda (no AWS login needed)')
    r.add_argument('--api-key', help='the API key for --url (safer: set GAP_API_KEY, or leave this out and type it when asked)')
    r.add_argument('--timeout', type=float, default=HTTP_TIMEOUT, help='seconds to wait for --url (default 60)')
    s = sub.add_parser('split', help='cut an existing gap.json again'); s.add_argument('file')
    for x in (r, s):
        x.add_argument('--size', type=int, default=25, help='users per batch (default 25)'); x.add_argument('--out', help='new folder to write into')
    a = p.parse_args(argv)
    out = a.out or 'gap-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    try:
        if a.cmd == 'run' and a.url:
            base, key = check_url(a.url), get_key(a.api_key)
            print(f'Calling {base}{PATH} with the API key (an HTTP API allows 30 seconds) …')
            report = run_report_http(base, key, a.timeout)
        elif a.cmd == 'run':
            print(f'Running {ROUTE} on {a.function} (this takes a couple of minutes) …')
            report = run_report(make_client(a.region), a.function, a.region)
        else:
            with open(a.file, encoding='utf-8') as f:
                report = json.load(f)
        s = cut(report, a.size, out)
    except ToolError as e:
        print(f'Stopped: {e}'); return 1
    except FileExistsError:
        print(f'Stopped: the folder {out} already exists — choose another with --out'); return 1
    show(report, s, out); return 0


if __name__ == '__main__':
    sys.exit(main())
