from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..schemas.auth import LoginRequest, LoginResponse
from ..controllers.auth_controller import AuthController

security = HTTPBearer()
router = APIRouter(prefix="/api/auth", tags=["authentication"])

@router.post("/login", response_model=LoginResponse)
async def login(login_data: LoginRequest, db: Session = Depends(get_db)):
    """
    Endpoint de autenticação
    Verifica credenciais e retorna JWT se usuário for gerente ou admin
    """
    return await AuthController.login(login_data, db)

@router.post("/validate")
async def validate_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Endpoint para validar token JWT
    """
    return await AuthController.validate_token(credentials)
