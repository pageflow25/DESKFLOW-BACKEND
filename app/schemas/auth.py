from pydantic import BaseModel
from typing import Optional

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    user_name: str
    roles: str

class TokenData(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    roles: Optional[str] = None
