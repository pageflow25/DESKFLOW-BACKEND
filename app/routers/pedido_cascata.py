from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..schemas.pedido_cascata import PedidoCascataResponse
from ..controllers.cascata_controller import CascataController
from ..services.auth_service import verify_token
from fastapi import HTTPException, status

security = HTTPBearer()
router = APIRouter(prefix="/api/pedidos", tags=["pedidos_cascata"])


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


@router.get("/escola/{escola_id}/cascata", response_model=PedidoCascataResponse)
async def get_pedidos_escola_cascata(
    escola_id: int,
    tipo_formulario: str = Query(default='MEMOREX', description="Tipo de formulário a filtrar"),
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Endpoint para obter pedidos da escola em estrutura hierárquica
    Apenas administradores podem acessar
    
    Args:
        escola_id: ID da escola
        tipo_formulario: Tipo de formulário (padrão: 'MEMOREX')
    """
    return await CascataController.get_pedidos_escola(db, escola_id, tipo_formulario)
