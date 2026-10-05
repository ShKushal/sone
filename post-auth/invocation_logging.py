import json
import logging

logger = logging.getLogger()


def log_invocation(event, context):
    event_json = json.dumps(event, default=str)
    # print() reaches CloudWatch even if the Lambda log level filters INFO
    print(f"EVENT: {event_json}")
    logger.info("Event: %s", event_json)

    context_details = {
        "function_name": getattr(context, "function_name", None),
        "function_version": getattr(context, "function_version", None),
        "invoked_function_arn": getattr(context, "invoked_function_arn", None),
        "memory_limit_in_mb": getattr(context, "memory_limit_in_mb", None),
        "aws_request_id": getattr(context, "aws_request_id", None),
        "log_group_name": getattr(context, "log_group_name", None),
        "log_stream_name": getattr(context, "log_stream_name", None),
        "remaining_time_in_millis": (
            context.get_remaining_time_in_millis() if context else None
        ),
    }
    logger.info("Context: %s", json.dumps(context_details, default=str))
