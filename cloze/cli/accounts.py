"""계정 관리 CLI(ADR-007: 가입 API 없이 팀이 발급한다).

uv run python -m cloze.cli.accounts create-user <login_id> --display-name <이름>
uv run python -m cloze.cli.accounts set-password <login_id>

비밀번호는 셸 기록에 남지 않도록 인자가 아니라 입력으로 받는다.
delete-user는 문서에 삭제 표시를 해야 해서 documents 테이블(#14) 뒤에 더한다.
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError

from cloze.config import load_settings
from cloze.infra.db import make_engine, make_sessionmaker
from cloze.infra.db.users import create_user, set_password


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    password = _ask_password()
    # Windows 기본 이벤트 루프(Proactor)는 psycopg 비동기 모드를 지원하지 않는다.
    ok = asyncio.run(_run(args, password), loop_factory=asyncio.SelectorEventLoop)
    sys.exit(0 if ok else 1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cloze.cli.accounts")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="계정을 만든다")
    create.add_argument("login_id")
    create.add_argument("--display-name", required=True)
    reset = commands.add_parser("set-password", help="비밀번호를 바꾸고 기존 세션을 끊는다")
    reset.add_argument("login_id")
    return parser


def _ask_password() -> str:
    password = getpass.getpass("비밀번호: ")
    if not password:
        sys.exit("비밀번호가 비었습니다.")
    if getpass.getpass("한 번 더: ") != password:
        sys.exit("두 비밀번호가 다릅니다.")
    return password


async def _run(args: argparse.Namespace, password: str) -> bool:
    engine = make_engine(load_settings())
    try:
        async with make_sessionmaker(engine)() as session:
            if args.command == "create-user":
                try:
                    await create_user(session, args.login_id, password, args.display_name)
                except IntegrityError:
                    print(f"이미 있는 아이디: {args.login_id}")
                    return False
            elif not await set_password(session, args.login_id, password):
                print(f"없는 아이디: {args.login_id}")
                return False
            await session.commit()
            print("완료")
            return True
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
