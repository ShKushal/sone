import boto3
import json
import os
import time
from datetime import datetime

dynamodb   = boto3.resource('dynamodb', region_name=os.environ.get('REGION', 'ap-southeast-1'))
email_logs = dynamodb.Table(os.environ.get('TABLE_NAME', 'CognitoEmailLogs'))

def lambda_handler(event, context):
    for record in event.get('Records', []):
        try:
            message     = json.loads(record['Sns']['Message'])
            event_type  = message.get('eventType', '')
            mail        = message.get('mail', {})
            destination = mail.get('destination', [])
            subject     = mail.get('commonHeaders', {}).get('subject', '')
            timestamp   = mail.get('timestamp', datetime.utcnow().isoformat())
            message_id  = mail.get('messageId', '')

            details = {}
            if event_type == 'Delivery':
                details = message.get('delivery', {})
            elif event_type == 'Bounce':
                details = message.get('bounce', {})
            elif event_type == 'Complaint':
                details = message.get('complaint', {})

            success = event_type in ['Send', 'Delivery']

            for email in destination:
                email_logs.put_item(
                    Item={
                        'email':      email.lower(),
                        'timestamp':  timestamp,
                        'eventType':  event_type,
                        'subject':    subject,
                        'messageId':  message_id,
                        'success':    success,
                        'details':    json.dumps(details),
                        'ttl':        int(time.time()) + (90 * 24 * 60 * 60)
                    }
                )
                print(f"[EMAIL LOG] {event_type} → {email} → {subject}")

        except Exception as e:
            print(f"[EMAIL LOG] Error: {e}")

    return { 'statusCode': 200 }