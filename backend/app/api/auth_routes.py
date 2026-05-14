from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import create_access_token, decode_access_token_subject, verify_credentials
from app.auth.deps import get_current_user_id

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(body: LoginBody) -> dict[str, str]:
    uid = verify_credentials(body.username, body.password)
    if not uid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    token = create_access_token(user_id=uid)
    return {"access_token": token, "token_type": "bearer", "user_id": uid}


@router.get("/me")
def auth_me(user_id: Annotated[str, Depends(get_current_user_id)]) -> dict[str, str]:
    return {"user_id": user_id}
