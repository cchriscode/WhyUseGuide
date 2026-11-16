"""요청마다 쓰는 의존성: DB 세션, 현재 사용자(Bearer 토큰)."""

import hashlib
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from whyuseguide_api.db.models import AuthToken, User
from whyuseguide_api.errors import ApiError
from whyuseguide_api.timed import OtpPlanner

TOKEN_TTL = timedelta(days=30)  # PRD §10 보안: 토큰 만료 30일


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


def get_planner(request: Request) -> OtpPlanner | None:
    """OTP 시간표 구간 질의 객체(main.lifespan이 만든다). None이면 엔진 근사만 쓴다."""
    planner: OtpPlanner | None = request.app.state.planner
    return planner


Planner = Annotated[OtpPlanner | None, Depends(get_planner)]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_token(session: AsyncSession, user: User) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + TOKEN_TTL
    session.add(AuthToken(user_id=user.id, token_hash=hash_token(token), expires_at=expires))
    return token, expires


async def current_user(session: Session, authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthorized", "Bearer 토큰이 필요해요")
    token = authorization.split(" ", 1)[1].strip()
    row = (
        await session.execute(
            select(User)
            .join(AuthToken, AuthToken.user_id == User.id)
            .where(AuthToken.token_hash == hash_token(token), AuthToken.expires_at > datetime.now(UTC))
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(401, "unauthorized", "토큰이 없거나 만료됐어요")
    return row


CurrentUser = Annotated[User, Depends(current_user)]
