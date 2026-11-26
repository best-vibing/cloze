"""비밀번호 해시. 표준 라이브러리 scrypt와 무작위 솔트(데이터 계약 4절 users).

저장 형식은 `scrypt$N$r$p$솔트$해시`다. 비용 값을 함께 적어 두므로 나중에 값을 올려도
옛 해시를 그대로 검증할 수 있다.
"""

import hashlib
import hmac
import secrets

# OWASP 권장값(N=2^17, r=8, p=1). 메모리 128 * r * N = 128MiB를 쓴다.
N, R, P = 2**17, 8, 1
_MAXMEM = 2**28
_SALT_BYTES = 16


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=_MAXMEM)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, N, R, P)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    scheme, n, r, p, salt, digest = stored.split("$")
    if scheme != "scrypt":
        raise ValueError(f"모르는 해시 방식: {scheme}")
    actual = _scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p))
    return hmac.compare_digest(actual, bytes.fromhex(digest))
