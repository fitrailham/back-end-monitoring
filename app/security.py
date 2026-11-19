"""Password and opaque-token primitives; no plaintext credentials are persisted."""
import hashlib
import secrets


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    # OWASP scrypt profile: N=2**17, r=8, p=1 (128 MiB).
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                            n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024).hex()
    return f"scrypt$131072$8$1${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "131072", "8", "1"):
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024).hex()
        return secrets.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False
