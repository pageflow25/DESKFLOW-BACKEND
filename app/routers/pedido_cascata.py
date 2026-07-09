from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..schemas.pedido_cascata import PedidoCascataResponse, StatusDeskflowListResponse
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
    tipo_formulario: str = Query(default=None, description="Tipo de formulário a filtrar (opcional)"),
    ids_formularios: str = Query(default=None, description="IDs de formulários separados por vírgula (ex: 1,2,3)"),
    status_ids: str = Query(default=None, description="IDs de status separados por vírgula (ex: 1,2,3). Padrão: 1"),
    nome_arquivo_filtro: str = Query(default=None, description="Trecho do nome do arquivo PDF para filtrar (opcional)"),
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Endpoint para obter pedidos da escola em estrutura hierárquica
    Apenas administradores podem acessar
    
    Args:
        escola_id: ID da escola
        tipo_formulario: Tipo de formulário (opcional)
        ids_formularios: IDs de formulários separados por vírgula (opcional)
        status_ids: IDs de status separados por vírgula (padrão: 1)
        nome_arquivo_filtro: Trecho do nome do arquivo PDF (opcional)
    """
    # Converter string de IDs para lista de inteiros
    parsed_ids = None
    if ids_formularios:
        try:
            parsed_ids = [int(x.strip()) for x in ids_formularios.split(',') if x.strip()]
        except ValueError:
            from fastapi import HTTPException as HTTPExc
            raise HTTPExc(status_code=400, detail="ids_formularios deve conter apenas números separados por vírgula")
    
    # Converter string de status_ids para lista de inteiros
    parsed_status_ids = None
    if status_ids:
        try:
            parsed_status_ids = [int(x.strip()) for x in status_ids.split(',') if x.strip()]
        except ValueError:
            from fastapi import HTTPException as HTTPExc
            raise HTTPExc(status_code=400, detail="status_ids deve conter apenas números separados por vírgula")
    
    return await CascataController.get_pedidos_escola(
        db,
        escola_id,
        tipo_formulario,
        parsed_ids,
        parsed_status_ids,
        nome_arquivo_filtro,
    )


@router.get("/status-deskflow", response_model=StatusDeskflowListResponse)
async def listar_status_deskflow(
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """Lista os status disponíveis para filtro em tela (status_deskflow_pedido)."""
    return await CascataController.listar_status_deskflow(db)
