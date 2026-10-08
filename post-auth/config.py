import os


SENDER_EMAIL = os.environ["SENDER_EMAIL"]

RESET_PASSWORD_PATH = "/forgotPassword"


def get_required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} environment variable is not set")
    return value
