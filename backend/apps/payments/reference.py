"""Public payment reference generator: 'SP-' + 10 Crockford-base32 chars."""

import secrets

# Crockford base32 alphabet (no I, L, O, U — avoids look-alike confusion on receipts).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_reference() -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    return f"SP-{body}"
