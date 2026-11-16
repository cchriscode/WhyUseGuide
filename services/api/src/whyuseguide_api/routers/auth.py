from datetime import datetime

from fastapi import APIRouter

from whyuseguide_api.db.models import User
from whyuseguide_api.deps import Session, issue_token
from whyuseguide_api.schemas import ApiModel

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class TokenOut(ApiModel):
    token: str
    expires_at: datetime
    user_id: str
    is_guest: bool


@router.post("/guest", response_model=TokenOut, response_model_by_alias=True)
async def guest(session: Session) -> TokenOut:
    """게스트 토큰 발급(F-30). 로그인 전에도 일정을 만들 수 있다."""
    user = User(is_guest=True, auth_provider="guest")
    session.add(user)
    await session.flush()
    token, expires = await issue_token(session, user)
    await session.commit()
    return TokenOut(token=token, expires_at=expires, user_id=str(user.id), is_guest=True)
