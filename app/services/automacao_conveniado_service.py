import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import String, func
from sqlalchemy.orm import Session

from ..config.database import SessionLocal
from ..config.logging_config import get_logger
from ..config.settings import get_settings
from ..controllers.orcamento_controller import OrcamentoController
from ..models.distribuicao_material import DistribuicaoMaterial
from ..models.escola import Escola
from ..models.especificacao_form import EspecificacaoForm
from ..models.unidade_escolar import UnidadeEscolar
from ..schemas.orcamento import FluxoOrcamentoRequest

logger = get_logger(__name__)
settings = get_settings()

STATUS_ID_PENDENTE_PROCESSAMENTO = 1
TIPO_ESCOLA_CONVENIADO = "conveniado"


class AutomacaoConveniadoService:
    """Automação de lançamento de pedidos para escolas conveniadas."""

    _lock = asyncio.Lock()
    _executando: bool = False

    @staticmethod
    def _parse_data_saida(valor: Optional[str]) -> Optional[date]:
        if not valor:
            return None

        bruto = str(valor).strip()
        if not bruto:
            return None

        try:
            return date.fromisoformat(bruto[:10])
        except ValueError:
            logger.warning(f"Data de saída inválida ignorada: {valor}")
            return None

    @staticmethod
    def _horarios_configurados() -> List[Tuple[int, int]]:
        horarios: List[Tuple[int, int]] = []

        for item in settings.CONVENIADO_AUTOMACAO_HORARIOS.split(","):
            valor = item.strip()
            if not valor:
                continue

            try:
                hora_txt, minuto_txt = valor.split(":", maxsplit=1)
                hora = int(hora_txt)
                minuto = int(minuto_txt)

                if not (0 <= hora <= 23 and 0 <= minuto <= 59):
                    raise ValueError("hora/minuto fora da faixa")

                horarios.append((hora, minuto))
            except Exception:
                logger.warning(f"Horário inválido em CONVENIADO_AUTOMACAO_HORARIOS: {valor}")

        if not horarios:
            horarios = [(15, 30), (15, 58)]

        return horarios

    @classmethod
    def obter_horarios_scheduler(cls) -> List[Tuple[int, int]]:
        """Retorna os horários (hora, minuto) para o agendador."""
        return cls._horarios_configurados()

    @staticmethod
    def _obter_escolas_conveniadas(db: Session) -> List[int]:
        rows = (
            db.query(Escola.id)
            .join(UnidadeEscolar, UnidadeEscolar.escola_id == Escola.id)
            .join(DistribuicaoMaterial, DistribuicaoMaterial.unidade_escolar_id == UnidadeEscolar.id)
            .filter(
                func.lower(Escola.tipo_escola.cast(String)) == TIPO_ESCOLA_CONVENIADO,
                DistribuicaoMaterial.status_id == STATUS_ID_PENDENTE_PROCESSAMENTO,
                DistribuicaoMaterial.quantidade > 0,
            )
            .distinct()
            .all()
        )

        return [row.id for row in rows]

    @staticmethod
    def _obter_filtros_escola(db: Session, escola_id: int) -> Dict[str, Any]:
        rows = (
            db.query(
                EspecificacaoForm.id_produto,
                DistribuicaoMaterial.data_saida,
                DistribuicaoMaterial.formulario_id,
                UnidadeEscolar.divisao_logistica,
                UnidadeEscolar.dias_uteis,
            )
            .join(DistribuicaoMaterial, DistribuicaoMaterial.especificacao_form_id == EspecificacaoForm.id)
            .join(UnidadeEscolar, UnidadeEscolar.id == DistribuicaoMaterial.unidade_escolar_id)
            .filter(
                UnidadeEscolar.escola_id == escola_id,
                DistribuicaoMaterial.status_id == STATUS_ID_PENDENTE_PROCESSAMENTO,
                DistribuicaoMaterial.quantidade > 0,
            )
            .all()
        )

        ids_produtos = sorted({row.id_produto for row in rows if row.id_produto is not None})
        ids_formularios = sorted({row.formulario_id for row in rows if row.formulario_id is not None})
        divisoes_logistica = sorted(
            {
                str(row.divisao_logistica).strip()
                for row in rows
                if row.divisao_logistica is not None and str(row.divisao_logistica).strip()
            }
        )
        dias_uteis_filtro = sorted({row.dias_uteis for row in rows if row.dias_uteis is not None})
        datas_saida = sorted(
            {
                data_convertida
                for row in rows
                for data_convertida in [AutomacaoConveniadoService._parse_data_saida(row.data_saida)]
                if data_convertida is not None
            }
        )

        return {
            "ids_produtos": ids_produtos,
            "ids_formularios": ids_formularios,
            "divisoes_logistica": divisoes_logistica,
            "dias_uteis_filtro": dias_uteis_filtro,
            "datas_saida": datas_saida,
        }

    @staticmethod
    def _montar_data_entrega(now_tz: datetime) -> str:
        offset = max(settings.CONVENIADO_DATA_ENTREGA_OFFSET_DIAS, 0)
        entrega = (now_tz + timedelta(days=offset)).replace(
            hour=12,
            minute=0,
            second=0,
            microsecond=0,
        )
        return entrega.isoformat(timespec="milliseconds")

    @classmethod
    async def executar_lote_conveniados(cls) -> Dict[str, Any]:
        """Executa o fluxo completo para todas as escolas conveniadas elegíveis."""
        if not settings.CONVENIADO_AUTOMACAO_ATIVA:
            logger.info("Automação de conveniados desativada por configuração")
            return {
                "executado": False,
                "motivo": "desativado",
                "escolas_processadas": 0,
                "erros": [],
                "resultados": [],
            }

        async with cls._lock:
            if cls._executando:
                logger.warning("Automação de conveniados já está em execução. Ignorando disparo concorrente.")
                return {
                    "executado": False,
                    "motivo": "concorrente",
                    "escolas_processadas": 0,
                    "erros": ["Execução concorrente bloqueada"],
                    "resultados": [],
                }

            cls._executando = True

            try:
                tz = ZoneInfo(settings.CONVENIADO_AUTOMACAO_TIMEZONE)
                now_tz = datetime.now(tz)

                with SessionLocal() as db:
                    escolas_ids = cls._obter_escolas_conveniadas(db)

                if not escolas_ids:
                    logger.info("Nenhuma escola conveniada com status_id=1 encontrada para automação")
                    return {
                        "executado": True,
                        "escolas_processadas": 0,
                        "erros": [],
                        "resultados": [],
                    }

                resultados: List[Dict[str, Any]] = []
                erros: List[str] = []

                logger.info(
                    f"Iniciando automação de conveniados para {len(escolas_ids)} escola(s): {escolas_ids}"
                )

                for escola_id in escolas_ids:
                    with SessionLocal() as db_escola:
                        try:
                            filtros = cls._obter_filtros_escola(db_escola, escola_id)
                            ids_produtos = filtros["ids_produtos"]
                            ids_formularios = filtros["ids_formularios"]
                            divisoes_logistica = filtros["divisoes_logistica"]
                            dias_uteis_filtro = filtros["dias_uteis_filtro"]
                            datas_saida = filtros["datas_saida"]

                            logger.info(
                                "Automação escola %s - filtros: status_ids=%s, ids_produtos=%s, "
                                "datas_saida=%s, divisoes_logistica=%s, dias_uteis_filtro=%s, ids_formularios=%s",
                                escola_id,
                                [STATUS_ID_PENDENTE_PROCESSAMENTO],
                                ids_produtos,
                                [d.isoformat() for d in datas_saida],
                                divisoes_logistica,
                                dias_uteis_filtro,
                                ids_formularios,
                            )

                            if not ids_produtos:
                                logger.info(f"Escola {escola_id} sem produtos elegíveis. Pulando.")
                                continue

                            if not datas_saida:
                                logger.info(
                                    f"Escola {escola_id} sem data_saida válida em distribuicao_materiais. Pulando."
                                )
                                continue

                            for data_saida in datas_saida:
                                logger.info(
                                    "Automação escola %s - processando data de saída %s em lote dedicado",
                                    escola_id,
                                    data_saida.isoformat(),
                                )

                                request = FluxoOrcamentoRequest(
                                    tipo_fluxo="com_distribuicao_sem_faturamento",
                                    escola_id=escola_id,
                                    ids_produtos=ids_produtos,
                                    datas_saida=[data_saida],
                                    divisoes_logistica=divisoes_logistica or None,
                                    dias_uteis_filtro=dias_uteis_filtro or None,
                                    aprovar_automaticamente=True,
                                    data_entrega=None,
                                    usar_data_saida_distribuicao=True,
                                    baixar_arquivos=True,
                                    gerar_op=True,
                                    ids_formularios=ids_formularios or None,
                                    status_ids=[STATUS_ID_PENDENTE_PROCESSAMENTO],
                                    grupo_lote_id=None,
                                    modo_agrupamento="unidade",
                                )

                                resultado = await OrcamentoController.processar_orcamento_com_distribuicao(
                                    db=db_escola,
                                    request=request,
                                )

                                resultado_dict = resultado.model_dump()
                                resultado_dict["escola_id"] = escola_id
                                resultado_dict["data_saida_lote"] = data_saida.isoformat()
                                resultados.append(resultado_dict)

                                if resultado.erros:
                                    erros.extend(
                                        [
                                            f"Escola {escola_id} | Data {data_saida.isoformat()}: {erro}"
                                            for erro in resultado.erros
                                        ]
                                    )

                                logger.info(
                                    f"Automação escola {escola_id} (data {data_saida.isoformat()}): "
                                    f"total={resultado.total}, enviados={resultado.enviados}, "
                                    f"aprovados={resultado.aprovados}, downloads={resultado.downloads}, "
                                    f"erros={len(resultado.erros)}"
                                )

                        except Exception as exc:
                            erro = f"Erro ao processar escola {escola_id}: {str(exc)}"
                            logger.error(erro, exc_info=True)
                            erros.append(erro)

                return {
                    "executado": True,
                    "escolas_processadas": len({r.get("escola_id") for r in resultados}),
                    "erros": erros,
                    "resultados": resultados,
                }

            finally:
                cls._executando = False


async def executar_automacao_conveniados_agendada() -> None:
    """Entry point para execução agendada."""
    resultado = await AutomacaoConveniadoService.executar_lote_conveniados()
    logger.info(
        "Automação conveniados finalizada — "
        f"executado={resultado.get('executado')}, "
        f"escolas_processadas={resultado.get('escolas_processadas')}, "
        f"erros={len(resultado.get('erros', []))}"
    )
