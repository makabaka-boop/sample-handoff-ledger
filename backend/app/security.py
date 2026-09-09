import hashlib
import hmac
import secrets


def code_digest(code: str, signing_key: str) -> str:
    return hmac.new(signing_key.encode(), code.encode(), hashlib.sha256).hexdigest()


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"
