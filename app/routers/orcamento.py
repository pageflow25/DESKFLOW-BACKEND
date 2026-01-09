from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..schemas.orcamento import OrcamentoRequest, OrcamentoListResponse
from ..controllers.orcamento_controller import OrcamentoController
from ..services.auth_service import verify_token
from ..services.arquivo_orcamento_service import ArquivoOrcamentoService
from ..config.logging_config import get_logger

logger = get_logger(__name__)
security = HTTPBearer()
router = APIRouter(prefix="/api/orcamento", tags=["orcamento"])


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


@router.post("/gerar", response_model=OrcamentoListResponse)
async def gerar_orcamento(
    request: OrcamentoRequest,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Endpoint para gerar orçamento baseado nos filtros fornecidos
    Apenas administradores podem acessar
    """
    return await OrcamentoController.gerar_orcamento(db, request)


@router.get("/arquivos/listar")
async def listar_orcamentos(user_data: dict = Depends(verify_admin)):
    """
    Lista todos os arquivos de orçamento disponíveis
    Apenas administradores podem acessar
    """
    logger.info("Listando arquivos de orçamento")
    orcamentos = ArquivoOrcamentoService.listar_orcamentos()
    return JSONResponse({
        "total": len(orcamentos),
        "orcamentos": orcamentos
    })


@router.get("/arquivos/download/{nome_arquivo}")
async def download_orcamento(
    nome_arquivo: str,
    user_data: dict = Depends(verify_admin)
):
    """
    Faz download de um arquivo de orçamento
    Apenas administradores podem acessar
    """
    logger.info(f"Download solicitado: {nome_arquivo}")
    
    # Validar nome do arquivo (segurança)
    if not nome_arquivo.startswith("orcamento_") or not nome_arquivo.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nome de arquivo inválido"
        )
    
    # Verificar se arquivo existe
    if not ArquivoOrcamentoService.arquivo_existe(nome_arquivo):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo não encontrado"
        )
    
    caminho = ArquivoOrcamentoService.obter_caminho_completo(nome_arquivo)
    return FileResponse(
        path=caminho,
        filename=nome_arquivo,
        media_type="application/json"
    )


@router.delete("/arquivos/deletar/{nome_arquivo}")
async def deletar_orcamento(
    nome_arquivo: str,
    user_data: dict = Depends(verify_admin)
):
    """
    Deleta um arquivo de orçamento
    Apenas administradores podem acessar
    """
    logger.info(f"Deletando arquivo: {nome_arquivo}")
    
    # Validar nome do arquivo (segurança)
    if not nome_arquivo.startswith("orcamento_") or not nome_arquivo.endswith(".json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nome de arquivo inválido"
        )
    
    if not ArquivoOrcamentoService.deletar_arquivo(nome_arquivo):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo não encontrado"
        )
    
    return JSONResponse({
        "mensagem": f"Arquivo {nome_arquivo} deletado com sucesso"
    })


@router.post("/arquivos/limpar-antigos")
async def limpar_arquivos_antigos(
    dias: int = 1,
    user_data: dict = Depends(verify_admin)
):
    """
    Deleta arquivos de orçamento mais antigos que X dias
    Apenas administradores podem acessar
    """
    logger.info(f"Limpando arquivos mais antigos que {dias} dia(s)")
    
    deletados = ArquivoOrcamentoService.limpar_arquivos_antigos(dias=dias)
    return JSONResponse({
        "mensagem": f"{deletados} arquivo(s) antigo(s) deletado(s)"
    })
