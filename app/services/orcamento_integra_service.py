import os
import re
import shutil
import tempfile
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import bindparam, func, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..config.logging_config import get_logger
from ..config.settings import get_settings
from ..models.orcamento_processamento import OrcamentoProcessamento
from ..schemas.orcamento import (
    FluxoOrcamentoIntegraItem,
    FluxoOrcamentoIntegraRequest,
    FluxoOrcamentoIntegraResponse,
    OrcamentoResponse,
    PedidoIntegraListResponse,
    PedidoIntegraResumo,
    PedidoIntegraDashboardResumo,
)
from .download_bremen_service import DownloadBremenService
from .orcamento_api_service import OrcamentoAPIService


logger = get_logger(__name__)
settings = get_settings()
SQL_DIR = Path(__file__).resolve().parent / "sql"


class OrcamentoIntegraService:
    """Orquestra orçamento, aprovação e arquivos dos pedidos ``integra_*``."""

    STATUS_EM_ANDAMENTO = {
        "orcamento_em_envio",
        "aprovacao_em_envio",
        "arquivos_em_processamento",
    }
    STATUS_RECONCILIACAO = {
        "reconciliacao_orcamento",
        "reconciliacao_aprovacao",
    }

    @staticmethod
    def calcular_data_entrega_seis_dias_uteis(
        data_base: Optional[datetime] = None,
    ) -> str:
        timezone = ZoneInfo("America/Sao_Paulo")
        if data_base and data_base.tzinfo is None:
            data = data_base.replace(tzinfo=timezone)
        else:
            data = data_base.astimezone(timezone) if data_base else datetime.now(timezone)
        dias_adicionados = 0
        while dias_adicionados < 6:
            data += timedelta(days=1)
            if data.weekday() < 5:
                dias_adicionados += 1
        return data.replace(hour=12, minute=0, second=0, microsecond=0).isoformat(timespec="milliseconds")

    @staticmethod
    def obter_resumo_dashboard(db: Session) -> PedidoIntegraDashboardResumo:
        recebidos = db.execute(
            text(
                """
                SELECT COUNT(*)::INTEGER
                FROM integra_pedidos ip
                JOIN integra_status_pedidos isp ON isp.id = ip.status_id
                WHERE ip.integracao_id = 2
                  AND isp.ativo = TRUE
                  AND LOWER(isp.nome) = LOWER('Pedido recebido')
                  AND EXISTS (
                      SELECT 1
                      FROM integra_pedido_produtos ipp
                      WHERE ipp.pedido_id = ip.id
                  )
                """
            )
        ).scalar_one()
        db.rollback()
        return PedidoIntegraDashboardResumo(recebidos=recebidos)

    @classmethod
    def obter_data_entrega_pedido(cls, db: Session, pedido_id: int) -> str:
        criado_em = db.execute(
            text("SELECT criado_em FROM integra_pedidos WHERE id = :pedido_id"),
            {"pedido_id": pedido_id},
        ).scalar_one_or_none()
        db.rollback()
        if criado_em is None:
            raise ValueError(f"Pedido {pedido_id} não encontrado para cálculo da data de entrega")
        return cls.calcular_data_entrega_seis_dias_uteis(criado_em)

    @staticmethod
    def listar_pedidos(
        db: Session,
        limit: int = 100,
        offset: int = 0,
    ) -> PedidoIntegraListResponse:
        rows = db.execute(
            text(
                """
                WITH pedidos_resumo AS (
                    SELECT
                        ip.id AS pedido_id,
                        ip.numero_pedido,
                        ip.nome_cliente,
                        ip.criado_em,
                        isp.nome AS status_pedido,
                        proc.status AS processamento_status,
                        proc.id_orcamento,
                        proc.ops,
                        COUNT(ipp.id)::INTEGER AS total_produtos,
                        json_agg(ipp.nome ORDER BY ipp.id) AS produtos,
                        COUNT(*) FILTER (
                            WHERE ipp.catalogo_bremen_modelo_id IS NULL
                        )::INTEGER AS modelos_pendentes,
                        COUNT(*) FILTER (
                            WHERE ipp.arquivo_pdf IS NULL
                               OR ipp.design_capa_frente IS NULL
                               OR ipp.design_capa_verso IS NULL
                        )::INTEGER AS arquivos_pendentes
                    FROM integra_pedidos ip
                    JOIN integra_status_pedidos isp ON isp.id = ip.status_id
                    JOIN integra_pedido_produtos ipp ON ipp.pedido_id = ip.id
                    LEFT JOIN integra_orcamento_processamentos proc ON proc.pedido_id = ip.id
                    WHERE ip.integracao_id = 2
                      AND (
                          LOWER(isp.nome) = LOWER('Pedido recebido')
                          OR proc.pedido_id IS NOT NULL
                      )
                    GROUP BY
                        ip.id,
                        ip.numero_pedido,
                        ip.nome_cliente,
                        ip.criado_em,
                        isp.nome,
                        proc.status,
                        proc.id_orcamento,
                        proc.ops
                )
                SELECT
                    *,
                    COUNT(*) OVER()::INTEGER AS total_geral,
                    COUNT(*) FILTER (
                        WHERE LOWER(status_pedido) = LOWER('Pedido recebido')
                    ) OVER()::INTEGER AS total_recebidos
                FROM pedidos_resumo
                ORDER BY
                    CASE WHEN LOWER(status_pedido) = LOWER('Pedido recebido') THEN 0 ELSE 1 END,
                    criado_em DESC,
                    pedido_id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": limit, "offset": offset},
        ).mappings().all()
        db.rollback()

        pedidos: List[PedidoIntegraResumo] = []
        estados_retentativa_arquivos = {"op_gerada", "erro_arquivos"}
        estados_bloqueados = OrcamentoIntegraService.STATUS_EM_ANDAMENTO | OrcamentoIntegraService.STATUS_RECONCILIACAO | {"concluido"}

        for row in rows:
            status_recebido = (row["status_pedido"] or "").casefold() == "pedido recebido".casefold()
            estado = row["processamento_status"]
            pronto_dados = row["modelos_pendentes"] == 0 and row["arquivos_pendentes"] == 0
            elegivel = pronto_dados and (
                (status_recebido and estado not in estados_bloqueados)
                or estado in estados_retentativa_arquivos
            )

            motivo = None
            if row["modelos_pendentes"]:
                motivo = f"{row['modelos_pendentes']} produto(s) sem modelo Bremen"
            elif row["arquivos_pendentes"]:
                motivo = f"{row['arquivos_pendentes']} produto(s) com arquivos incompletos"
            elif estado in OrcamentoIntegraService.STATUS_EM_ANDAMENTO:
                motivo = "Processamento em andamento"
            elif estado in OrcamentoIntegraService.STATUS_RECONCILIACAO:
                motivo = "Requer reconciliação antes de novo envio"
            elif estado == "concluido":
                motivo = "Processamento concluído"
            elif not status_recebido and estado not in estados_retentativa_arquivos:
                motivo = f"Pedido está como '{row['status_pedido']}'"

            pedidos.append(
                PedidoIntegraResumo(
                    pedido_id=row["pedido_id"],
                    numero_pedido=row["numero_pedido"],
                    nome_cliente=row["nome_cliente"],
                    criado_em=row["criado_em"].isoformat() if row["criado_em"] else None,
                    status_pedido=row["status_pedido"],
                    processamento_status=estado,
                    id_orcamento=row["id_orcamento"],
                    ops=row["ops"] or [],
                    total_produtos=row["total_produtos"],
                    produtos=row["produtos"] or [],
                    modelos_pendentes=row["modelos_pendentes"],
                    arquivos_pendentes=row["arquivos_pendentes"],
                    elegivel=elegivel,
                    motivo_bloqueio=motivo,
                )
            )

        total = rows[0]["total_geral"] if rows else 0
        recebidos = rows[0]["total_recebidos"] if rows else 0
        return PedidoIntegraListResponse(
            pedidos=pedidos,
            total=total,
            recebidos=recebidos,
            limit=limit,
            offset=offset,
        )

    def __init__(
        self,
        api_service: Optional[OrcamentoAPIService] = None,
        download_service: Optional[DownloadBremenService] = None,
    ) -> None:
        self.api_service = api_service or OrcamentoAPIService()
        self.download_service = download_service or DownloadBremenService()

    @staticmethod
    def _obter_estado(db: Session, pedido_id: int) -> Optional[Dict[str, Any]]:
        registro = db.get(
            OrcamentoProcessamento,
            pedido_id,
            populate_existing=True,
        )
        return registro.to_dict() if registro else None

    @staticmethod
    def _inicializar_estado(db: Session, pedido_id: int) -> Dict[str, Any]:
        db.execute(
            pg_insert(OrcamentoProcessamento)
            .values(pedido_id=pedido_id, status="novo")
            .on_conflict_do_nothing(index_elements=[OrcamentoProcessamento.pedido_id])
        )
        db.commit()
        estado = OrcamentoIntegraService._obter_estado(db, pedido_id)
        if not estado:
            raise RuntimeError(f"Não foi possível inicializar o pedido {pedido_id}")
        return estado

    @staticmethod
    def _transicionar(
        db: Session,
        pedido_id: int,
        esperados: List[str],
        novo_status: str,
        **campos: Any,
    ) -> bool:
        valores: Dict[str, Any] = {
            "status": novo_status,
            "atualizado_em": func.now(),
        }
        for nome, valor in campos.items():
            if nome not in {
                "id_orcamento",
                "itens_orcamento",
                "resposta_orcamento",
                "resposta_aprovacao",
                "ops",
                "arquivos",
                "erro",
            }:
                raise ValueError(f"Campo de processamento não permitido: {nome}")
            valores[nome] = valor

        query = (
            update(OrcamentoProcessamento)
            .where(
                OrcamentoProcessamento.pedido_id == pedido_id,
                OrcamentoProcessamento.status.in_(esperados),
            )
            .values(**valores)
            .returning(OrcamentoProcessamento.pedido_id)
        )
        alterado = db.execute(query).first() is not None
        db.commit()
        return alterado

    @staticmethod
    def _gerar_payload(db: Session, pedido_id: int) -> OrcamentoResponse:
        sql = (SQL_DIR / "query_orcamento_integra.sql").read_text(encoding="utf-8")
        query = text(sql).bindparams(bindparam("pedido_ids", expanding=True))
        pedido_json = db.execute(query, {"pedido_ids": [pedido_id]}).scalar_one_or_none()
        db.rollback()  # A consulta é somente leitura; encerra a transação antes da chamada externa.

        if not pedido_json or not pedido_json.get("data", {}).get("itens"):
            raise ValueError(
                f"Pedido {pedido_id} não encontrado, não está como 'Pedido recebido' "
                "ou não possui modelo Bremen configurado"
            )
        return OrcamentoResponse.model_validate(pedido_json)

    @staticmethod
    def _bloquear_pedido_recebido(db: Session, pedido_id: int) -> bool:
        """Bloqueia a linha para impedir mudança concorrente de status durante a API."""
        row = db.execute(
            text(
                """
                SELECT ip.id
                FROM integra_pedidos ip
                JOIN integra_status_pedidos isp ON isp.id = ip.status_id
                WHERE ip.id = :pedido_id
                  AND LOWER(isp.nome) = LOWER('Pedido recebido')
                  AND isp.ativo = TRUE
                FOR UPDATE OF ip
                """
            ),
            {"pedido_id": pedido_id},
        ).first()
        return row is not None

    @staticmethod
    def _extrair_ops(resposta: Dict[str, Any]) -> List[int]:
        data = resposta.get("data", [])
        registros = data if isinstance(data, list) else [data]
        ops: List[int] = []
        for registro in registros:
            valor = registro.get("id_ops") if isinstance(registro, dict) else None
            if isinstance(valor, int):
                ops.append(valor)
            elif isinstance(valor, str):
                ops.extend(int(item.strip()) for item in valor.split(",") if item.strip())
        return ops

    @staticmethod
    def _extensao(url: str, padrao: str) -> str:
        extensao = os.path.splitext(urlparse(url).path)[1].lower()
        return extensao if re.fullmatch(r"\.[a-z0-9]{1,8}", extensao or "") else padrao

    @staticmethod
    async def _remover_diretorio(caminho: str, tentativas: int = 3) -> bool:
        """Remove uma pasta sem permitir que a limpeza masque o erro do fluxo."""
        for tentativa in range(1, tentativas + 1):
            try:
                shutil.rmtree(caminho)
                return True
            except FileNotFoundError:
                return True
            except OSError as exc:
                if tentativa < tentativas:
                    await asyncio.sleep(0.25 * tentativa)
                    continue
                logger.warning(
                    "Não foi possível remover o diretório temporário %s: %s",
                    caminho,
                    exc,
                )
        return False

    async def _organizar_arquivos(
        self,
        db: Session,
        pedido_id: int,
        ops: List[int],
    ) -> List[Dict[str, Any]]:
        produtos = db.execute(
            text(
                """
                SELECT id, arquivo_pdf, design_capa_frente, design_capa_verso
                FROM integra_pedido_produtos
                WHERE pedido_id = :pedido_id
                ORDER BY id
                """
            ),
            {"pedido_id": pedido_id},
        ).mappings().all()
        db.rollback()

        if len(produtos) != len(ops):
            raise ValueError(
                f"Pedido {pedido_id}: {len(produtos)} produtos para {len(ops)} OPs; "
                "não é possível vincular os arquivos com segurança"
            )
        if len(set(ops)) != len(ops):
            raise ValueError(f"Pedido {pedido_id}: a API retornou OPs duplicadas")

        base = os.path.abspath(getattr(settings, "DOWNLOAD_BASE_PATH", "C:/Bremen/OPs"))
        os.makedirs(base, exist_ok=True)
        temporaria = tempfile.mkdtemp(prefix=f".integra-{pedido_id}-", dir=base)
        publicadas: List[str] = []
        resultado: List[Dict[str, Any]] = []

        try:
            for produto, op in zip(produtos, ops):
                fontes = {
                    "arquivo_pdf": produto["arquivo_pdf"],
                    "design_capa_frente": produto["design_capa_frente"],
                    "design_capa_verso": produto["design_capa_verso"],
                }
                ausentes = [nome for nome, url in fontes.items() if not url]
                if ausentes:
                    raise ValueError(
                        f"Produto {produto['id']} sem arquivos obrigatórios: {', '.join(ausentes)}"
                    )

                pasta_final = os.path.abspath(os.path.join(base, str(op)))
                if os.path.commonpath([base, pasta_final]) != base:
                    raise ValueError(f"Caminho inválido para a OP {op}")

                nomes = {
                    "arquivo_pdf": f"arquivo_pdf{self._extensao(fontes['arquivo_pdf'], '.pdf')}",
                    "design_capa_frente": f"design_capa_frente{self._extensao(fontes['design_capa_frente'], '.png')}",
                    "design_capa_verso": f"design_capa_verso{self._extensao(fontes['design_capa_verso'], '.png')}",
                }

                if os.path.isdir(pasta_final) and all(
                    os.path.isfile(os.path.join(pasta_final, nome))
                    and os.path.getsize(os.path.join(pasta_final, nome)) > 0
                    for nome in nomes.values()
                ):
                    arquivos = [os.path.join(pasta_final, nome) for nome in nomes.values()]
                    resultado.append({"pedido_produto_id": produto["id"], "id_op": op, "arquivos": arquivos})
                    continue
                if os.path.exists(pasta_final):
                    raise FileExistsError(
                        f"A pasta da OP {op} já existe, mas não contém o conjunto esperado de arquivos"
                    )

                pasta_stage = os.path.join(temporaria, str(op))
                os.makedirs(pasta_stage, exist_ok=False)
                arquivos_stage: List[str] = []
                for tipo, url in fontes.items():
                    destino = os.path.join(pasta_stage, nomes[tipo])
                    tamanho = await self.download_service.baixar_arquivo(url, destino)
                    if tamanho <= 0:
                        raise ValueError(f"Arquivo {tipo} do produto {produto['id']} está vazio")
                    arquivos_stage.append(destino)

                os.replace(pasta_stage, pasta_final)
                publicadas.append(pasta_final)
                resultado.append(
                    {
                        "pedido_produto_id": produto["id"],
                        "id_op": op,
                        "arquivos": [os.path.join(pasta_final, os.path.basename(item)) for item in arquivos_stage],
                    }
                )
            return resultado
        except Exception:
            for pasta in reversed(publicadas):
                if os.path.commonpath([base, pasta]) == base and os.path.isdir(pasta):
                    await self._remover_diretorio(pasta)
            raise
        finally:
            if os.path.isdir(temporaria):
                await self._remover_diretorio(temporaria)

    @staticmethod
    def _resultado_estado(estado: Dict[str, Any], mensagem: Optional[str] = None) -> FluxoOrcamentoIntegraItem:
        return FluxoOrcamentoIntegraItem(
            pedido_id=estado["pedido_id"],
            status=estado["status"],
            id_orcamento=estado.get("id_orcamento"),
            ops=estado.get("ops") or [],
            arquivos=estado.get("arquivos") or [],
            mensagem=mensagem or estado.get("erro"),
        )

    async def _processar_pedido(
        self,
        db: Session,
        pedido_id: int,
        data_entrega: Optional[str],
    ) -> FluxoOrcamentoIntegraItem:
        estado = self._inicializar_estado(db, pedido_id)
        resposta_orcamento: Optional[Dict[str, Any]] = None
        resposta_aprovacao: Optional[Dict[str, Any]] = None
        if estado["status"] == "concluido":
            return self._resultado_estado(estado, "Pedido já processado; nenhuma chamada foi repetida")
        if estado["status"] in self.STATUS_EM_ANDAMENTO | self.STATUS_RECONCILIACAO:
            return self._resultado_estado(
                estado,
                "Processamento já iniciado ou com resultado externo indeterminado; requer reconciliação",
            )

        try:
            if estado["status"] in {"novo", "erro_validacao"}:
                payload = self._gerar_payload(db, pedido_id)
                if not self._transicionar(
                    db, pedido_id, ["novo", "erro_validacao"], "orcamento_em_envio", erro=None
                ):
                    return self._resultado_estado(self._obter_estado(db, pedido_id), "Pedido capturado por outro processo")

                if not self._bloquear_pedido_recebido(db, pedido_id):
                    db.rollback()
                    self._transicionar(
                        db,
                        pedido_id,
                        ["orcamento_em_envio"],
                        "erro_validacao",
                        erro="Pedido não está mais como 'Pedido recebido'",
                    )
                    raise ValueError("Pedido não está mais como 'Pedido recebido'")

                envio = await self.api_service.enviar_orcamento(payload)
                resposta_orcamento = envio["resposta_api"]
                id_orcamento = resposta_orcamento.get("data", {}).get("id_orcamento")
                itens = self.api_service.extrair_itens_orcamento(resposta_orcamento)
                if not id_orcamento or len(itens) != len(payload.data.itens):
                    raise ValueError("Resposta do orçamento sem ID ou com quantidade de itens divergente")
                self._transicionar(
                    db,
                    pedido_id,
                    ["orcamento_em_envio"],
                    "orcamento_gerado",
                    id_orcamento=id_orcamento,
                    itens_orcamento=itens,
                    resposta_orcamento=resposta_orcamento,
                    erro=None,
                )
                estado = self._obter_estado(db, pedido_id)

            if estado["status"] == "orcamento_gerado":
                if not self._transicionar(
                    db, pedido_id, ["orcamento_gerado"], "aprovacao_em_envio", erro=None
                ):
                    return self._resultado_estado(self._obter_estado(db, pedido_id), "Pedido capturado por outro processo")
                if not self._bloquear_pedido_recebido(db, pedido_id):
                    db.rollback()
                    self._transicionar(
                        db,
                        pedido_id,
                        ["aprovacao_em_envio"],
                        "erro_validacao",
                        erro="Pedido não está mais como 'Pedido recebido'",
                    )
                    raise ValueError("Pedido não está mais como 'Pedido recebido'")
                resposta_aprovacao = await self.api_service.aprovar_orcamento_com_itens(
                    id_orcamento=estado["id_orcamento"],
                    itens_resposta=estado["itens_orcamento"] or [],
                    data_entrega=data_entrega,
                    gerar_op=True,
                )
                ops = self._extrair_ops(resposta_aprovacao)
                if len(ops) != len(estado["itens_orcamento"] or []):
                    raise ValueError("A aprovação não retornou exatamente uma OP para cada produto")

                atualizacao = db.execute(
                    text(
                        """
                        UPDATE integra_pedidos
                        SET status_id = (
                                SELECT id FROM integra_status_pedidos
                                WHERE LOWER(nome) = LOWER('Em produção') AND ativo = TRUE
                                ORDER BY id LIMIT 1
                            ),
                            atualizado_em = CURRENT_TIMESTAMP
                        WHERE id = :pedido_id
                          AND status_id = (
                                SELECT id FROM integra_status_pedidos
                                WHERE LOWER(nome) = LOWER('Pedido recebido') AND ativo = TRUE
                                ORDER BY id LIMIT 1
                            )
                        """
                    ),
                    {"pedido_id": pedido_id},
                )
                if atualizacao.rowcount != 1:
                    db.rollback()
                    raise ValueError("O pedido deixou de estar como 'Pedido recebido' durante o processamento")
                processamento_atualizado = db.execute(
                    update(OrcamentoProcessamento)
                    .where(
                        OrcamentoProcessamento.pedido_id == pedido_id,
                        OrcamentoProcessamento.status == "aprovacao_em_envio",
                    )
                    .values(
                        status="op_gerada",
                        resposta_aprovacao=resposta_aprovacao,
                        ops=ops,
                        erro=None,
                        atualizado_em=func.now(),
                    )
                )
                if processamento_atualizado.rowcount != 1:
                    db.rollback()
                    raise RuntimeError("Estado do processamento alterado concorrentemente")
                db.commit()
                estado = self._obter_estado(db, pedido_id)

            if estado["status"] in {"op_gerada", "erro_arquivos"}:
                if not self._transicionar(
                    db,
                    pedido_id,
                    ["op_gerada", "erro_arquivos"],
                    "arquivos_em_processamento",
                    erro=None,
                ):
                    return self._resultado_estado(self._obter_estado(db, pedido_id), "Pedido capturado por outro processo")
                arquivos = await self._organizar_arquivos(db, pedido_id, estado["ops"] or [])
                self._transicionar(
                    db,
                    pedido_id,
                    ["arquivos_em_processamento"],
                    "concluido",
                    arquivos=arquivos,
                    erro=None,
                )

            return self._resultado_estado(self._obter_estado(db, pedido_id))
        except Exception as exc:
            db.rollback()
            atual = self._obter_estado(db, pedido_id)
            status_atual = atual["status"] if atual else ""
            destino = {
                "orcamento_em_envio": "reconciliacao_orcamento",
                "aprovacao_em_envio": "reconciliacao_aprovacao",
                "arquivos_em_processamento": "erro_arquivos",
            }.get(status_atual, "erro_validacao")
            if atual:
                dados_reconciliacao: Dict[str, Any] = {"erro": str(exc)[:2000]}
                if status_atual == "orcamento_em_envio" and resposta_orcamento is not None:
                    dados_reconciliacao["resposta_orcamento"] = resposta_orcamento
                    dados_reconciliacao["id_orcamento"] = resposta_orcamento.get("data", {}).get("id_orcamento")
                    dados_reconciliacao["itens_orcamento"] = self.api_service.extrair_itens_orcamento(
                        resposta_orcamento
                    )
                if status_atual == "aprovacao_em_envio" and resposta_aprovacao is not None:
                    dados_reconciliacao["resposta_aprovacao"] = resposta_aprovacao
                    try:
                        dados_reconciliacao["ops"] = self._extrair_ops(resposta_aprovacao)
                    except (TypeError, ValueError):
                        pass
                self._transicionar(
                    db,
                    pedido_id,
                    [status_atual],
                    destino,
                    **dados_reconciliacao,
                )
            logger.exception("Falha no fluxo do pedido integrado %s", pedido_id)
            return self._resultado_estado(self._obter_estado(db, pedido_id), str(exc))

    async def processar(
        self,
        db: Session,
        request: FluxoOrcamentoIntegraRequest,
    ) -> FluxoOrcamentoIntegraResponse:
        resultados: List[FluxoOrcamentoIntegraItem] = []
        for pedido_id in dict.fromkeys(request.pedido_ids):
            try:
                data_entrega = self.obter_data_entrega_pedido(db, pedido_id)
                resultados.append(await self._processar_pedido(db, pedido_id, data_entrega))
            except Exception as exc:
                db.rollback()
                logger.exception("Falha ao inicializar o fluxo do pedido integrado %s", pedido_id)
                resultados.append(
                    FluxoOrcamentoIntegraItem(
                        pedido_id=pedido_id,
                        status="erro_validacao",
                        mensagem=str(exc),
                    )
                )

        concluidos = sum(item.status == "concluido" and not item.mensagem for item in resultados)
        ignorados = sum(item.status == "concluido" and bool(item.mensagem) for item in resultados)
        erros = len(resultados) - concluidos - ignorados
        return FluxoOrcamentoIntegraResponse(
            total=len(resultados),
            concluidos=concluidos,
            ignorados=ignorados,
            erros=erros,
            resultados=resultados,
        )
