"""Leituras do PCP no banco, para os controllers `pcp.*` da fila.

Era `repositorios/fila.py` — nome que se confundia com `fila/repositorio.py`,
que é o SQL da própria fila.

Só LEITURA. O claim e a gravação da execução acontecem em `fila_processamento`
(`fila/repositorio.py`), e todo o resultado de domínio (resposta, erro, status
final, pedidos, histórico, `downloads_bremen`) é gravado pelo PageFlow, que
projeta o resultado do item. A role `deskflow_fila` só tem GRANT nas colunas de
execução da fila: uma gravação de domínio daqui seria negada pelo banco.

O claim compare-and-set em `orcamento_api_orcamentos`/`orcamento_api_aprovacoes`,
os `listar_*_pendentes` e as gravações de `payload_enviado`/`id_requisicao` nas
tabelas de domínio eram dos ciclos antigos e foram removidos com eles.
"""

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text

ORIGEM_ESCOLA = "escola"
ORIGEM_INTEGRACAO = "integracao"


@dataclass
class OrcamentoReivindicado:
    """Um orçamento do PCP, nas DUAS origens de lote.

    `origem` (orcamento_api_lotes.origem) diz de onde vêm os itens, e as duas
    listas são EXCLUSIVAS — o CHECK `ck_orcamento_api_itens_origem` garante
    isso no banco:
      - 'escola'     -> `pedido_distribuicao_ids`, com `modo_agrupamento`;
      - 'integracao' -> `integra_pedido_produto_ids`, sem modo (a divisão é
        fixa: 1 orçamento por pedido do parceiro).

    Use `ids_origem` para não ramificar por origem em cada chamador.

    É a representação que os três SQLs de `sql/` e as validações de
    `servicos/pcp/payload.py` enxergam. O handler `pcp.orcamento.enviar` a monta a
    partir do payload do item da fila; o `dry-run` da linha de comando a lê do
    banco com `carregar_orcamento`.
    """

    id: int
    requisicao_id: int
    lote_id: int
    modo_envio: Optional[str]
    url_webhook: Optional[str]
    modo_agrupamento: Optional[str]
    cliente_id: Optional[int]
    vendedor_id: Optional[int]
    forma_pagamento: Optional[int]
    pedido_distribuicao_ids: list
    origem: str = ORIGEM_ESCOLA
    integra_pedido_produto_ids: list = field(default_factory=list)
    # Nas DUAS origens desde 2026-09-24: a data de entrega que o usuário
    # escolheu no "Enviar", já como 'DD/MM/YYYY', para entrar no `obs_producao`
    # do item (os três SQLs de orçamento). Na origem escola ela MANDA sobre a
    # `pedido_formularios.data_entrega`; vem None nos lotes de escola que já
    # estavam na fila, e aí o SQL cai de volta na data do formulário.
    # Não é a data que vai ao ERP como data do item — essa é a `data_saida`,
    # que o PageFlow manda na APROVAÇÃO.
    data_entrega: Optional[str] = None

    @property
    def ids_origem(self) -> list:
        """Ids que o `codigo_externo` dos itens tem de reproduzir."""
        if self.origem == ORIGEM_INTEGRACAO:
            return self.integra_pedido_produto_ids
        return self.pedido_distribuicao_ids


# --- Orçamentos -----------------------------------------------------------------

def carregar_orcamento(conn, orcamento_id: int) -> Optional[OrcamentoReivindicado]:
    """Dados para montar a chamada: grupo (cliente/vendedor/forma), modo do
    lote e os pedidos do orçamento. Usado pelo `dry-run`, sem claim."""
    linha = conn.execute(text("""
        SELECT o.id, o.orcamento_api_requisicao_id, o.modo_envio::text AS modo_envio, o.url_webhook,
               r.orcamento_api_lote_id AS lote_id, r.cliente_id, r.vendedor_id, r.forma_pagamento,
               p.origem::text AS origem,
               p.modo_agrupamento::text AS modo_agrupamento,
               to_char(o.data_entrega, 'DD/MM/YYYY') AS data_entrega
          FROM orcamento_api_orcamentos o
          JOIN orcamento_api_requisicoes r ON r.id = o.orcamento_api_requisicao_id
          JOIN orcamento_api_lotes p ON p.id = r.orcamento_api_lote_id
         WHERE o.id = :id
    """), {"id": orcamento_id}).mappings().first()
    if not linha:
        return None

    # Uma consulta só para as duas origens: o item tem exatamente uma das duas
    # colunas preenchida, então cada lista sai com os ids que lhe cabem.
    itens = conn.execute(text("""
        SELECT pedido_distribuicao_id, integra_pedido_produto_id
          FROM orcamento_api_itens
         WHERE orcamento_api_orcamento_id = :id
         ORDER BY pedido_distribuicao_id NULLS LAST, integra_pedido_produto_id NULLS LAST
    """), {"id": orcamento_id}).mappings().all()

    pedidos = [item["pedido_distribuicao_id"] for item in itens if item["pedido_distribuicao_id"] is not None]
    produtos = [item["integra_pedido_produto_id"] for item in itens if item["integra_pedido_produto_id"] is not None]

    return OrcamentoReivindicado(
        id=linha["id"],
        requisicao_id=linha["orcamento_api_requisicao_id"],
        lote_id=linha["lote_id"],
        modo_envio=linha["modo_envio"],
        url_webhook=linha["url_webhook"],
        modo_agrupamento=linha["modo_agrupamento"],
        cliente_id=linha["cliente_id"],
        vendedor_id=linha["vendedor_id"],
        forma_pagamento=linha["forma_pagamento"],
        pedido_distribuicao_ids=pedidos,
        origem=linha["origem"] or ORIGEM_ESCOLA,
        integra_pedido_produto_ids=produtos,
        data_entrega=linha["data_entrega"],
    )


# --- Downloads dos arquivos da OP --------------------------------------------------

def arquivos_da_aprovacao(conn, aprovacao_id: int) -> list:
    """Arquivos de cada OP da aprovação, nas DUAS origens de lote.

    - Lote de ESCOLA: OP (orcamento_api_itens_retorno.id_op) -> pedido ->
      `pedido_distribuicao_arquivos` -> `pedido_arquivos_pdf` (Vercel Blob).
      Uma linha por pedido x arquivo; `arquivo_pdf_id` identifica o arquivo.
    - Lote de INTEGRAÇÃO: OP -> produto do pedido do parceiro
      (`integra_pedido_produtos`), cujos arquivos são URLs na própria linha:
      `arquivo_pdf` e, quando existirem, `design_capa_frente`/`design_capa_verso`.
      Não há `pedido_arquivos_pdf`, então `arquivo_pdf_id` vem NULL e a
      identidade do arquivo é (produto, tipo_arquivo). `mockup_capa_frente`,
      `mockup_capa_costas` e `etiqueta_produto` NÃO são baixados: são material
      de conferência/expedição, não arquivo de produção da OP.

    As duas metades devolvem as mesmas chaves, e `pasta` é o nome do primeiro
    nível da pasta de destino: a escola, ou "integração/numero_pedido".
    """
    return [dict(linha) for linha in conn.execute(text("""
        WITH aprovacao AS (
            SELECT p.origem::text AS origem, e.nome AS escola_nome, i.nome AS integracao_nome
              FROM orcamento_api_aprovacoes a
              JOIN orcamento_api_orcamentos o ON o.id = a.orcamento_api_orcamento_id
              JOIN orcamento_api_requisicoes r ON r.id = o.orcamento_api_requisicao_id
              JOIN orcamento_api_lotes p ON p.id = r.orcamento_api_lote_id
              LEFT JOIN escola_escolas e ON e.id = p.escola_id
              LEFT JOIN integra_integracoes_externas i ON i.id = p.integracao_id
             WHERE a.id = :aprovacao_id
        )
        -- Origem escola
        SELECT ir.id_op,
               ir.pedido_distribuicao_id,
               NULL::int AS integra_pedido_produto_id,
               pda.arquivo_pdf_id,
               ap.nome AS arquivo_nome,
               COALESCE(NULLIF(ap.caminho_remoto, ''), ap.arquivo) AS url,
               ap.tipo_arquivo,
               -- Identidade do arquivo para deduplicar o download: o ID, nunca
               -- o nome. Dois PDFs diferentes podem ter o mesmo nome, e
               -- deduplicar por nome perderia um deles.
               pda.arquivo_pdf_id::text AS chave_arquivo,
               COALESCE(ac.escola_nome, 'Escola sem nome') AS pasta,
               1 AS ordem_origem
          FROM orcamento_api_itens_retorno ir
          CROSS JOIN aprovacao ac
          JOIN pedido_distribuicao_arquivos pda ON pda.distribuicao_material_id = ir.pedido_distribuicao_id
          JOIN pedido_arquivos_pdf ap ON ap.id = pda.arquivo_pdf_id
         WHERE ir.orcamento_api_aprovacao_id = :aprovacao_id
           AND ir.id_op IS NOT NULL
           AND ir.pedido_distribuicao_id IS NOT NULL

        UNION ALL

        -- Origem integração: os arquivos são URLs no próprio produto.
        SELECT ir.id_op,
               NULL::int AS pedido_distribuicao_id,
               ir.integra_pedido_produto_id,
               NULL::int AS arquivo_pdf_id,
               arq.nome AS arquivo_nome,
               arq.url,
               arq.tipo_arquivo,
               ir.integra_pedido_produto_id::text || ':' || arq.tipo_arquivo AS chave_arquivo,
               COALESCE(ac.integracao_nome, 'Integracao') || ' - ' || COALESCE(ip.numero_pedido, 'sem numero') AS pasta,
               2 AS ordem_origem
          FROM orcamento_api_itens_retorno ir
          CROSS JOIN aprovacao ac
          JOIN integra_pedido_produtos ipp ON ipp.id = ir.integra_pedido_produto_id
          JOIN integra_pedidos ip ON ip.id = ipp.pedido_id
          CROSS JOIN LATERAL (
              VALUES
                  (ipp.arquivo_pdf, 'miolo', ipp.id::text || '_miolo.pdf'),
                  (ipp.design_capa_frente, 'capa_frente', ipp.id::text || '_capa_frente.pdf'),
                  (ipp.design_capa_verso, 'capa_verso', ipp.id::text || '_capa_verso.pdf')
          ) AS arq(url, tipo_arquivo, nome)
         WHERE ir.orcamento_api_aprovacao_id = :aprovacao_id
           AND ir.id_op IS NOT NULL
           AND ir.integra_pedido_produto_id IS NOT NULL
           AND COALESCE(arq.url, '') <> ''

        ORDER BY id_op, ordem_origem, chave_arquivo, pedido_distribuicao_id, integra_pedido_produto_id
    """), {"aprovacao_id": aprovacao_id}).mappings()]


__all__ = [
    "ORIGEM_ESCOLA",
    "ORIGEM_INTEGRACAO",
    "OrcamentoReivindicado",
    "carregar_orcamento",
    "arquivos_da_aprovacao",
]
