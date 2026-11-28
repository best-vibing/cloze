"""가린 이미지 주소의 서명(데이터 계약 2절 `GET /images/{key}`, 신뢰성 설계 2절).

주소는 `/images/{key}?exp=<유닉스 초>&sig=<HMAC-SHA256 hex>`이다. 서명 대상은 `{exp}:{key}`라
key나 exp 하나만 바꿔도 서명이 맞지 않는다. exp는 숫자뿐이라 첫 `:`에서 둘이 갈린다.
표준 라이브러리만 쓴다. 서명 키·현재 시각은 부르는 쪽이 넘긴다.
"""

import hashlib
import hmac
from urllib.parse import quote, urlencode

PREFIX = "/images/"
# 유닉스 초는 서기 33658년까지 11자리다. 이보다 긴 exp는 숫자로 바꾸지 않고 거절한다.
# 아주 긴 숫자 문자열은 int()가 ValueError(4300자리 상한)를 내 500이 된다.
MAX_EXP_DIGITS = 12


def _mac(key: str, exp: int, secret: bytes) -> str:
    return hmac.new(secret, f"{exp}:{key}".encode(), hashlib.sha256).hexdigest()


def parse_exp(text: str) -> int | None:
    """쿼리의 exp를 정수로 읽는다. ASCII 숫자 12자리 이내가 아니면 None.

    str.isdecimal()은 다른 문자 체계의 숫자(예: 아라비아 숫자)도 받으므로 쓰지 않는다.
    """
    if not (text.isascii() and text.isdigit() and len(text) <= MAX_EXP_DIGITS):
        return None
    return int(text)


def sign(key: str, secret: bytes, now: float, ttl_s: int) -> str:
    """key를 내보내는 주소. 만료 시각은 now + ttl_s(초 단위로 내림).

    ttl_s는 설정 `image_url_ttl_s`(cloze/config.py)에서 받는다. 기본값을 여기 두지 않는다.
    """
    exp = int(now) + ttl_s
    query = urlencode({"exp": exp, "sig": _mac(key, exp, secret)})
    return f"{PREFIX}{quote(key, safe='/')}?{query}"


def verify(key: str, exp: int, sig: str, secret: bytes, now: float) -> bool:
    """서명이 맞고 아직 만료 전이면 True. 만료 시각과 같은 초부터는 거절한다."""
    if now >= exp:
        return False
    # 비ASCII 글자가 섞인 sig도 500이 아니라 거절되게 바이트로 비교한다(auth.unsign과 같은 이유).
    return hmac.compare_digest(sig.encode(), _mac(key, exp, secret).encode())
