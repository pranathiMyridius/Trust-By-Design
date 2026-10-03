from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.security import create_access_token, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()

    if not user or not user.is_active or not verify_password(
        payload.password, user.hashed_password
    ):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    token = create_access_token(subject=str(user.id))

    return TokenResponse(access_token=token, user=user)


@router.get("/me", response_model=UserResponse)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user
