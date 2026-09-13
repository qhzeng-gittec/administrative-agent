"""Content identities shared by task history and internal evolution."""
import hashlib
import json


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
