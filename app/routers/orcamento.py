from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..schemas.orcamento import (
    ProcessamentoResultado,
    FluxoOrcamentoRequest
)
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


@router.post("/processar", response_model=ProcessamentoResultado)
async def processar_orcamento(
    request: FluxoOrcamentoRequest,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Processa orçamento de acordo com o fluxo selecionado
    
    Tipos de fluxo disponíveis:
    - com_distribuicao_sem_faturamento: FASE 01 (Orçamento) + FASE 02 (Aprovação com OP, sem faturamento)
    """
    try:
        logger.info(f"Usuário {user_data.get('username')} iniciando processamento - Fluxo: {request.tipo_fluxo}")
        
        if request.tipo_fluxo == "com_distribuicao_sem_faturamento":
            # Processar com distribuição sem faturamento
            return await OrcamentoController.processar_orcamento_com_distribuicao(
                db=db, 
                request=request
            )
        
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tipo de fluxo '{request.tipo_fluxo}' não implementado ainda"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no processamento de orçamento: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.post("/aprovar/{id_orcamento}")
async def aprovar_orcamento_manual(
    id_orcamento: int,
    data_entrega: str = None,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Aprova um orçamento específico manualmente
    """
    try:
        logger.info(f"Aprovação manual do orçamento {id_orcamento}")
        
        if not data_entrega:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Data de entrega é obrigatória"
            )
        
        # Buscar orçamento na tabela orcamento_api
        from ..models.orcamento_api import OrcamentoAPI
        orcamento_api = db.query(OrcamentoAPI).filter(
            OrcamentoAPI.id_orcamento == id_orcamento
        ).first()
        
        if not orcamento_api:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Orçamento não encontrado"
            )
        
        # Verificar se já foi aprovado
        from ..models.aprovacao_api import AprovacaoAPI
        aprovacao_existente = db.query(AprovacaoAPI).filter(
            AprovacaoAPI.distribuicao_material_id == orcamento_api.distribuicao_material_id
        ).first()
        
        if aprovacao_existente:
            return {
                "message": "Orçamento já foi aprovado anteriormente",
                "id_orcamento": id_orcamento,
                "id_ops": aprovacao_existente.id_ops
            }
        
        # Aprovar orçamento
        from ..services.orcamento_api_service import OrcamentoAPIService
        api_service = OrcamentoAPIService()
        resposta_aprovacao = await api_service.aprovar_orcamento(
            id_orcamento=id_orcamento,
            itens=orcamento_api.itens,
            data_entrega=data_entrega
        )
        
        # Salvar aprovação
        from ..services.orcamento_service import OrcamentoService
        id_ops = resposta_aprovacao.get('data', {}).get('id_ops')
        pedidos = api_service.extrair_pedidos_aprovacao(resposta_aprovacao)
        
        aprovacao_api = OrcamentoService.salvar_aprovacao_api(
            db=db,
            distribuicao_id=orcamento_api.distribuicao_material_id,
            id_orcamento=id_orcamento,
            id_ops=id_ops,
            pedidos=pedidos,
            resposta_completa=resposta_aprovacao
        )
        
        # Atualizar status
        OrcamentoService.atualizar_status_distribuicao(
            db=db,
            distribuicao_id=orcamento_api.distribuicao_material_id,
            novo_status="orcamento_aprovado",
            mensagem=f"Orçamento aprovado manualmente - OPs: {id_ops}",
            sucesso=True
        )
        
        return {
            "message": "Orçamento aprovado com sucesso",
            "id_orcamento": id_orcamento,
            "id_ops": id_ops,
            "pedidos_count": len(pedidos)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na aprovação manual: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.get("/status/{escola_id}")
async def consultar_status_orcamentos(
    escola_id: int,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Consulta status dos orçamentos de uma escola
    """
    try:
        # Buscar distribuições da escola
        from sqlalchemy import text
        query = """
            SELECT 
                dm.id,
                dm.unidade_escolar_id,
                ue.nome as unidade_nome,
                ef.nome_item,
                ef.quantidade,
                sdf.codigo as status_codigo,
                sdf.descricao as status_descricao,
                oa.id_orcamento,
                aa.id_ops,
                dm.status_distribuicao
            FROM distribuicao_materiais dm
            JOIN unidades_escolares ue ON ue.id = dm.unidade_escolar_id
            JOIN especificacoes_form ef ON ef.id = dm.especificacao_form_id
            LEFT JOIN status_deskflow_pedido sdf ON sdf.id = dm.status_id
            LEFT JOIN orcamento_api oa ON oa.distribuicao_material_id = dm.id
            LEFT JOIN aprovacao_api aa ON aa.distribuicao_material_id = dm.id
            WHERE ue.escola_id = :escola_id
            ORDER BY dm.id
        """
        
        result = db.execute(text(query), {"escola_id": escola_id})
        distribuicoes = []
        
        for row in result:
            distribuicoes.append({
                "distribuicao_id": row.id,
                "unidade_escolar_id": row.unidade_escolar_id,
                "unidade_nome": row.unidade_nome,
                "item_nome": row.nome_item,
                "quantidade": row.quantidade,
                "status_codigo": row.status_codigo,
                "status_descricao": row.status_descricao,
                "status_distribuicao": row.status_distribuicao,
                "id_orcamento": row.id_orcamento,
                "id_ops": row.id_ops,
                "tem_orcamento": row.id_orcamento is not None,
                "foi_aprovado": row.id_ops is not None
            })
        
        return {
            "escola_id": escola_id,
            "total_distribuicoes": len(distribuicoes),
            "distribuicoes": distribuicoes
        }
        
    except Exception as e:
        logger.error(f"Erro ao consultar status: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.get("/arquivos/listar")
async def listar_orcamentos(user_data: dict = Depends(verify_admin)):
    """
    Lista arquivos de orçamento gerados
    """
    try:
        arquivos = ArquivoOrcamentoService.listar_arquivos()
        return {"arquivos": arquivos}
    except Exception as e:
        logger.error(f"Erro ao listar arquivos: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.get("/arquivos/download/{nome_arquivo}")
async def download_orcamento(
    nome_arquivo: str,
    user_data: dict = Depends(verify_admin)
):
    """
    Download de arquivo de orçamento
    """
    try:
        caminho_arquivo = ArquivoOrcamentoService.obter_caminho_arquivo(nome_arquivo)
        if not caminho_arquivo.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Arquivo não encontrado"
            )
        
        return FileResponse(
            path=str(caminho_arquivo),
            filename=nome_arquivo,
            media_type="application/json"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao fazer download: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.delete("/arquivos/deletar/{nome_arquivo}")
async def deletar_orcamento(
    nome_arquivo: str,
    user_data: dict = Depends(verify_admin)
):
    """
    Delete arquivo de orçamento
    """
    try:
        sucesso = ArquivoOrcamentoService.deletar_arquivo(nome_arquivo)
        if sucesso:
            return {"message": "Arquivo deletado com sucesso"}
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Arquivo não encontrado"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao deletar arquivo: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.post("/arquivos/limpar-antigos")
async def limpar_arquivos_antigos(
    dias: int = 1,
    user_data: dict = Depends(verify_admin)
):
    """
    Limpa arquivos antigos de orçamento
    """
    try:
        arquivos_removidos = ArquivoOrcamentoService.limpar_arquivos_antigos(dias)
        return {
            "message": f"Limpeza concluída. {len(arquivos_removidos)} arquivos removidos.",
            "arquivos_removidos": arquivos_removidos
        }
        
    except Exception as e:
        logger.error(f"Erro ao limpar arquivos antigos: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )