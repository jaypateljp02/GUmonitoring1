"""
JWT verification for Production API.
Decodes tokens using shared JWT_SECRET — no DB query needed,
user info is extracted directly from the token payload.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel
from api.config import JWT_SECRET, JWT_ALGORITHM

security = HTTPBearer()


class TokenUser(BaseModel):
    """Lightweight user object extracted from JWT — no DB query."""
    id: str
    role: str
    name: str


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> TokenUser:
    """Extract user from JWT token. Used on every protected route."""
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        role = payload.get("role")
        name = payload.get("name")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return TokenUser(id=user_id, role=role, name=name)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


def require_admin(current_user: TokenUser = Depends(get_current_user)) -> TokenUser:
    """Only allow admin users."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
