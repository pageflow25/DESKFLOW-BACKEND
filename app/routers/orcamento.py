from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..schemas.orcamento import (
    ProcessamentoResultado,
    FluxoOrcamentoRequest,
    OrcamentoRequest,
    OrcamentoListResponse,
    GerarOrcamentoCompleto,
    LoteDisparoListResponse,
)
from ..controllers.orcamento_controller import OrcamentoController
from ..services.auth_service import verify_token
from ..services.arquivo_orcamento_service import ArquivoOrcamentoService
from ..services.automacao_conveniado_service import AutomacaoConveniadoService
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


@router.post("/gerar", response_model=ProcessamentoResultado)
async def gerar_orcamento(
    request: GerarOrcamentoCompleto,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Gera orçamento com fluxo completo (FASE 01 + FASE 02)
    
    Executa:
    1. Geração de orçamentos via API Bremen (FASE 01)
    2. Salvamento na tabela orcamento_api
    3. Aprovação automática dos orçamentos (FASE 02) 
    4. Salvamento na tabela aprovacao_api
    5. Atualização de status e histórico
    
    Args:
        request: Dados para geração e processamento do orçamento
        
    Returns:
        ProcessamentoResultado com detalhes do processamento completo
    """
    try:
        logger.info(f"Usuário {user_data.get('username')} iniciando fluxo completo para escola {request.escola_id}")
        
        # Se data_entrega não foi fornecida, usar data padrão (hoje + 7 dias)
        data_entrega = request.data_entrega
        if not data_entrega and request.aprovar_automaticamente and not request.usar_data_saida_distribuicao:
            from datetime import datetime, timedelta
            data_futura = datetime.now() + timedelta(days=7)
            data_entrega = data_futura.strftime("%Y-%m-%dT12:00:00.000-03:00")
            logger.info(f"Data de entrega não fornecida, usando padrão: {data_entrega}")
        
        # Converter para FluxoOrcamentoRequest
        fluxo_request = FluxoOrcamentoRequest(
            tipo_fluxo=request.tipo_fluxo,
            escola_id=request.escola_id,
            ids_produtos=request.ids_produtos,
            datas_saida=request.datas_saida,
            divisoes_logistica=request.divisoes_logistica,
            dias_uteis_filtro=request.dias_uteis_filtro,
            ids_unidades=request.ids_unidades,
            ids_arquivos=request.ids_arquivos,
            ids_formularios=request.ids_formularios,
            status_ids=request.status_ids,
            nome_arquivo_filtro=request.nome_arquivo_filtro,
            grupo_lote_id=request.grupo_lote_id,
            aprovar_automaticamente=request.aprovar_automaticamente,
            data_entrega=data_entrega,
            usar_data_saida_distribuicao=request.usar_data_saida_distribuicao,
            baixar_arquivos=request.baixar_arquivos,
            gerar_op=request.gerar_op,
            modo_agrupamento=request.modo_agrupamento,
            persistir_resultado=request.persistir_resultado,
            organizar_arquivos_por_escola=request.organizar_arquivos_por_escola,
            atualizar_status_fase01=request.atualizar_status_fase01
        )
        
        # Executar fluxo completo
        return await OrcamentoController.processar_orcamento_com_distribuicao(
            db=db,
            request=fluxo_request
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no fluxo completo de orçamento: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.get("/lotes/disparos", response_model=LoteDisparoListResponse)
async def listar_lotes_disparo(
    limit: int = 10,
    offset: int = 0,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Lista lotes de disparo de pedidos para acompanhamento/auditoria.

    Retorna resumo por grupo_lote_id e detalhes por distribuição.
    Suporta paginação via limit/offset.
    """
    try:
        logger.info(
            f"Usuário {user_data.get('username')} consultando lotes de disparo (limit={limit}, offset={offset})"
        )
        return await OrcamentoController.listar_lotes_disparo(db=db, limit=limit, offset=offset)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao listar lotes de disparo: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.post("/gerar-simples", response_model=OrcamentoListResponse)
async def gerar_orcamento_simples(
    request: OrcamentoRequest,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Gera orçamento com base nos filtros fornecidos (apenas geração local, sem envio para API)
    
    Args:
        request: Dados para geração do orçamento (escola_id, ids_produtos, datas_saida, etc.)
        
    Returns:
        OrcamentoListResponse com orçamentos gerados
    """
    try:
        logger.info(f"Usuário {user_data.get('username')} gerando orçamento simples para escola {request.escola_id}")
        
        return await OrcamentoController.gerar_orcamento(db=db, request=request)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao gerar orçamento simples: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


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


@router.post("/automacao/conveniados/executar")
async def executar_automacao_conveniados(
    user_data: dict = Depends(verify_admin)
):
    """
    Executa manualmente o lote de automação das escolas conveniadas.

    Fluxo executado por escola elegível:
    1) Lançamento de pedidos (orçamento)
    2) Aprovação com geração de OP
    3) Download e organização de arquivos
    4) Atualização de status/histórico de distribuição
    """
    try:
        logger.info(
            f"Usuário {user_data.get('username')} iniciou execução manual da automação de conveniados"
        )
        return await AutomacaoConveniadoService.executar_lote_conveniados()
    except Exception as e:
        logger.error(f"Erro na automação manual de conveniados: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.post("/aprovar/{id_orcamento}")
async def aprovar_orcamento_manual(
    id_orcamento: int,
    data_entrega: str = None,
    gerar_op: bool = True,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Aprova um orçamento específico manualmente.
    
    Busca os itens da tabela orcamento_api e envia para aprovação na API Bremen.
    Salva 1 linha por OP na tabela aprovacao_api.
    """
    try:
        logger.info(f"Aprovação manual do orçamento {id_orcamento}")
        
        if not data_entrega:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Data de entrega é obrigatória"
            )
        
        # Buscar todos os registros do orçamento na tabela orcamento_api
        from ..models.orcamento_api import OrcamentoAPI
        registros_orcamento = db.query(OrcamentoAPI).filter(
            OrcamentoAPI.id_orcamento == id_orcamento
        ).order_by(OrcamentoAPI.id).all()
        
        if not registros_orcamento:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Orçamento não encontrado"
            )
        
        # Extrair distribuicoes_ids na ordem dos registros
        distribuicoes_ids = [r.distribuicao_material_id for r in registros_orcamento]
        envio_item_ids_por_distribuicao = {
            r.distribuicao_material_id: r.envio_item_id
            for r in registros_orcamento
            if getattr(r, "envio_item_id", None)
        }
        
        # Verificar se já foi aprovado
        from ..models.aprovacao_api import AprovacaoAPI
        aprovacao_existente = db.query(AprovacaoAPI).filter(
            AprovacaoAPI.id_orcamento == id_orcamento
        ).first()
        
        if aprovacao_existente:
            return {
                "message": "Orçamento já foi aprovado anteriormente",
                "id_orcamento": id_orcamento,
                "id_ops": aprovacao_existente.id_ops
            }
        
        # Aprovar orçamento via API Bremen
        from ..services.orcamento_api_service import OrcamentoAPIService
        api_service = OrcamentoAPIService()
        resposta_aprovacao = await api_service.aprovar_orcamento(
            db=db,
            id_orcamento=id_orcamento,
            data_entrega=data_entrega,
            gerar_op=gerar_op
        )
        
        # Normalizar resposta
        if isinstance(resposta_aprovacao, list):
            resposta_aprovacao = {"data": resposta_aprovacao}
        
        # Salvar aprovação (1 linha por OP)
        from ..services.orcamento_service import OrcamentoService
        registros_aprovacao = OrcamentoService.salvar_aprovacao_api(
            db=db,
            id_orcamento=id_orcamento,
            resposta_completa=resposta_aprovacao,
            distribuicoes_ids=distribuicoes_ids,
            payload_orcamento={},
            envio_item_ids_por_distribuicao=envio_item_ids_por_distribuicao,
        )
        
        # Atualizar status de cada distribuição
        for dist_id in distribuicoes_ids:
            try:
                OrcamentoService.atualizar_status_distribuicao(
                    db=db,
                    distribuicao_id=dist_id,
                    novo_status="orcamento_aprovado",
                    mensagem=f"Orçamento aprovado manualmente - {len(registros_aprovacao)} OPs",
                    sucesso=True,
                    envio_item_id=envio_item_ids_por_distribuicao.get(dist_id),
                )
            except Exception as e:
                logger.warning(f"Erro ao atualizar status da distribuição {dist_id}: {e}")
        
        return {
            "message": "Orçamento aprovado com sucesso",
            "id_orcamento": id_orcamento,
            "ops_count": len(registros_aprovacao),
            "distribuicoes_atualizadas": len(distribuicoes_ids)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na aprovação manual: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.post("/baixar-arquivos/{id_orcamento}")
async def baixar_arquivos_orcamento(
    id_orcamento: int,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    FASE 03 — Baixa e organiza os arquivos PDF de um orçamento aprovado.
    
    Para cada OP aprovada, baixa os PDFs do Vercel Blob e organiza
    em pastas nomeadas pelo número da OP.
    
    Estrutura gerada:
        C:/Bremen/OPs/
        ├── 4777/
        │   └── ATIV_23_02.PDF
        ├── 4800/
        │   ├── BLOCO_ATIVIDADES_CAPA.PDF
        │   └── BLOCO_ATIVIDADES_MIOLO.PDF
    
    Args:
        id_orcamento: ID do orçamento aprovado
    """
    try:
        logger.info(f"Usuário {user_data.get('username')} iniciando download FASE 03 para orçamento {id_orcamento}")
        
        # Verificar se o orçamento foi aprovado
        from ..models.aprovacao_api import AprovacaoAPI
        aprovacoes = db.query(AprovacaoAPI).filter(
            AprovacaoAPI.id_orcamento == id_orcamento
        ).all()
        
        if not aprovacoes:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Orçamento {id_orcamento} não possui aprovações. Execute a FASE 02 primeiro."
            )
        
        # Executar download
        from ..services.download_bremen_service import DownloadBremenService
        download_service = DownloadBremenService()
        resultado = await download_service.processar_downloads_por_orcamento(
            db=db,
            id_orcamento=id_orcamento
        )
        
        return {
            "message": f"Download concluído para orçamento {id_orcamento}",
            "id_orcamento": id_orcamento,
            "total_ops": resultado.get("total_ops", 0),
            "arquivos_baixados": resultado.get("downloads", 0),
            "erros": resultado.get("erros", []),
            "detalhes": resultado.get("detalhes", [])
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no download de arquivos: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro interno: {str(e)}"
        )


@router.get("/downloads/status/{id_orcamento}")
async def consultar_downloads_orcamento(
    id_orcamento: int,
    db: Session = Depends(get_db),
    user_data: dict = Depends(verify_admin)
):
    """
    Consulta o status dos downloads para um orçamento específico.
    
    Retorna a lista de arquivos baixados com detalhes de cada OP.
    """
    try:
        from ..services.download_bremen_service import DownloadBremenService
        downloads = DownloadBremenService.obter_downloads_por_orcamento(
            db=db,
            id_orcamento=id_orcamento
        )
        
        return {
            "id_orcamento": id_orcamento,
            "total_downloads": len(downloads),
            "downloads": downloads
        }
        
    except Exception as e:
        logger.error(f"Erro ao consultar downloads: {str(e)}")
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
            FROM pedido_distribuicoes dm
            JOIN escola_unidades ue ON ue.id = dm.unidade_escolar_id
            JOIN pedido_especificacoes ef ON ef.id = dm.especificacao_form_id
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