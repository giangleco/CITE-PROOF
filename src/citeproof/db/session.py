"""Engine và session factory async."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str, *, connect_timeout: int | None = None) -> AsyncEngine:
    # hide_parameters: thông điệp lỗi SQLAlchemy không kèm giá trị tham số (có thể là
    # nội dung tài liệu).
    connect_args: dict[str, int] = {}
    if connect_timeout is not None:
        connect_args["connect_timeout"] = connect_timeout
    return create_async_engine(
        url, pool_pre_ping=True, hide_parameters=True, connect_args=connect_args
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
