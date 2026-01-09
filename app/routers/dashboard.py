from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..schemas.dashboard import DashboardResponse
from ..controllers.dashboard_controller import DashboardController
from ..services.auth_service import verify_token, check_pcp_permission
from fastapi import HTTPException, status

security = HTTPBearer()
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def verify_admin(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Dependency para verificar se o usuário é admin
    """
    token_data = verify_token(credentials.credentials)
    
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verificar se é admin
    roles = token_data.get("roles", "")
    if not roles or "admin" not in [role.strip().lower() for role in roles.split(',')]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado. Apenas administradores podem acessar este recurso.",
        )
    
    return token_data


@router.get("/escolas", response_model=DashboardResponse)
async def get_escolas(
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Endpoint para obter lista de escolas com contagem de pedidos
    Apenas administradores podem acessar
    """
    return await DashboardController.get_escolas(db)
