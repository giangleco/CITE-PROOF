"""Lệnh quản trị: `python -m citeproof.cli <lệnh>`.

- `create-user --email E --name N [--admin]`: tạo tài khoản; mật khẩu nhập ẩn hai lần.
- `create-invite [--days N]`: in ra một mã mời dùng một lần.
- `seed-sample --email E`: thêm bài mẫu cho tài khoản (chỉ môi trường local).
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from citeproof.core.enums import UserRole
from citeproof.core.errors import CiteProofError
from citeproof.core.settings import get_settings
from citeproof.db.models import User
from citeproof.db.session import make_engine, make_session_factory
from citeproof.services import auth
from citeproof.services.sample import seed_sample


def _read_password() -> str:
    first = getpass.getpass("Mật khẩu: ")
    if getpass.getpass("Nhập lại mật khẩu: ") != first:
        raise CiteProofError("Hai lần nhập mật khẩu không khớp.")
    return first


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m citeproof.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    user = sub.add_parser("create-user", help="Tạo tài khoản")
    user.add_argument("--email", required=True)
    user.add_argument("--name", required=True)
    user.add_argument("--admin", action="store_true")
    invite = sub.add_parser("create-invite", help="Tạo mã mời dùng một lần")
    invite.add_argument("--days", type=int, default=14)
    sample = sub.add_parser("seed-sample", help="Thêm bài mẫu (chỉ local)")
    sample.add_argument("--email", required=True)
    return parser


async def run(argv: Sequence[str]) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            if args.command == "create-user":
                role = UserRole.ADMIN if args.admin else UserRole.USER
                user = await auth.create_user(
                    session,
                    email=args.email,
                    password=_read_password(),
                    display_name=args.name,
                    role=role,
                )
                await session.commit()
                print(f"Đã tạo tài khoản {user.email} ({role.value}).")
            elif args.command == "create-invite":
                code = await auth.create_invite(session, created_by=None, expires_in_days=args.days)
                await session.commit()
                print(f"Mã mời (dùng một lần, hết hạn sau {args.days} ngày):")
                print(code)
            elif args.command == "seed-sample":
                return await _seed_sample(session, args.email)
    except CiteProofError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


async def _seed_sample(session: AsyncSession, email: str) -> int:
    settings = get_settings()
    user = (
        await session.execute(select(User).where(User.email == auth.normalize_email(email)))
    ).scalar_one_or_none()
    if user is None:
        print(f"Lỗi: không có tài khoản {email}.", file=sys.stderr)
        return 1
    _, run_id = await seed_sample(session, settings, user_id=user.id)
    await session.commit()
    print(f"Đã thêm bài mẫu. Mở: /runs/{run_id}/report")
    return 0


def main() -> None:
    sys.exit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
