"""
Service de Pareamento de PDFs — Capa ↔ Miolo

Garante que, para cada valor de `pares` dentro de um mesmo `formulario_id`,
a capa e o miolo sejam agrupados corretamente, sem misturar com outros pares
ou outros formulários.

Regras:
  1. Agrupamento primário: formulario_id + pares
  2. Se `pares` for NULL, usa item_pedido_id como chave-fallback
  3. Cada par deve conter no máximo 1 capa e 1 miolo
  4. Nomes podem divergir (ex.: CAPA-PAR-PEDIR-NAO-I3 ↔ PAR-PEDIR-I3)
     — o pareamento é feito pela coluna `pares`, NUNCA pelo nome do arquivo
  5. Validações detectam pares incompletos, duplicados ou órfãos
"""

import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import and_

from ..config.logging_config import get_logger
from ..models.arquivo_pdf import ArquivoPdf

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ParPdf:
    """Representa um par capa + miolo agrupado pela coluna `pares`."""
    formulario_id: int
    pares: Optional[int]
    capa: Optional[ArquivoPdf] = None
    miolo: Optional[ArquivoPdf] = None

    @property
    def chave(self) -> str:
        """Chave única do par (usada como dict key)."""
        id_par = self.pares if self.pares is not None else "null"
        return f"{self.formulario_id}:{id_par}"

    @property
    def completo(self) -> bool:
        return self.capa is not None and self.miolo is not None

    @property
    def somente_miolo(self) -> bool:
        return self.capa is None and self.miolo is not None

    @property
    def somente_capa(self) -> bool:
        return self.capa is not None and self.miolo is None

    @property
    def vazio(self) -> bool:
        return self.capa is None and self.miolo is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "formulario_id": self.formulario_id,
            "pares": self.pares,
            "completo": self.completo,
            "capa": _arquivo_to_dict(self.capa) if self.capa else None,
            "miolo": _arquivo_to_dict(self.miolo) if self.miolo else None,
            "divergencia_nome": self.detectar_divergencia_nome(),
        }

    def detectar_divergencia_nome(self) -> Optional[str]:
        """
        Compara nomes da capa e do miolo e retorna mensagem se houver
        divergência significativa (além do prefixo 'CAPA-').

        Exemplos que NÃO são divergência:
            CAPA-PAR-NUMEROS-I5 ↔ PAR-NUMEROS-I5

        Exemplos que SÃO divergência:
            CAPA-PAR-PEDIR-NAO-I3 ↔ PAR-PEDIR-I3
        """
        if not self.capa or not self.miolo:
            return None

        nome_capa = _normalizar_nome(self.capa.nome)
        nome_miolo = _normalizar_nome(self.miolo.nome)

        # Remove o prefixo 'CAPA-' / 'CAPA_' do nome_capa para comparar
        nome_capa_limpo = re.sub(r'^CAPA[\-_\s]?', '', nome_capa, flags=re.IGNORECASE)

        if nome_capa_limpo != nome_miolo:
            return (
                f"Divergência de nomes no par {self.pares}: "
                f"capa='{self.capa.nome}' vs miolo='{self.miolo.nome}'"
            )
        return None


@dataclass
class ResultadoPareamento:
    """Resultado completo do pareamento de PDFs."""
    formulario_id: int
    total_arquivos: int = 0
    total_pares: int = 0
    pares_completos: int = 0
    pares_somente_miolo: int = 0
    pares_somente_capa: int = 0
    divergencias_nome: List[str] = field(default_factory=list)
    pares: List[ParPdf] = field(default_factory=list)
    erros: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "formulario_id": self.formulario_id,
            "total_arquivos": self.total_arquivos,
            "total_pares": self.total_pares,
            "pares_completos": self.pares_completos,
            "pares_somente_miolo": self.pares_somente_miolo,
            "pares_somente_capa": self.pares_somente_capa,
            "divergencias_nome": self.divergencias_nome,
            "pares": [p.to_dict() for p in self.pares],
            "erros": self.erros,
        }


# ---------------------------------------------------------------------------
# Funções auxiliares
# ---------------------------------------------------------------------------

def _normalizar_nome(nome: str) -> str:
    """Remove extensão e normaliza para comparação."""
    nome = re.sub(r'\.pdf$', '', nome, flags=re.IGNORECASE)
    nome = nome.strip().upper()
    # Remove sufixos de data/unidade (ex: -UN-2025, _UN_2025)
    nome = re.sub(r'[\-_]UN[\-_]\d{4}$', '', nome, flags=re.IGNORECASE)
    return nome


def _arquivo_to_dict(arquivo: ArquivoPdf) -> Dict[str, Any]:
    """Converte ArquivoPdf em dict resumido."""
    return {
        "id": arquivo.id,
        "nome": arquivo.nome,
        "tipo_arquivo": arquivo.tipo_arquivo,
        "pares": arquivo.pares,
        "paginas": arquivo.paginas,
        "id_componente": arquivo.id_componente,
        "item_pedido_id": arquivo.item_pedido_id,
        "caminho_remoto": arquivo.caminho_remoto,
        "formulario_id": arquivo.formulario_id,
    }


def _classificar_tipo(arquivo: ArquivoPdf) -> str:
    """
    Determina o tipo (capa/miolo) com fallback no nome do arquivo
    caso tipo_arquivo esteja vazio.
    """
    tipo = (arquivo.tipo_arquivo or "").strip().lower()
    if tipo in ("capa", "miolo"):
        return tipo

    # Fallback: inferir pelo nome do arquivo
    nome = (arquivo.nome or "").upper()
    if nome.startswith("CAPA") or "-CAPA-" in nome or "_CAPA_" in nome:
        return "capa"
    return "miolo"


# ---------------------------------------------------------------------------
# Funções principais
# ---------------------------------------------------------------------------

def agrupar_pares(
    arquivos: List[ArquivoPdf],
    formulario_id: Optional[int] = None,
) -> ResultadoPareamento:
    """
    Recebe uma lista de ArquivoPdf e agrupa em pares capa+miolo
    usando a coluna `pares` como chave de agrupamento.

    Regras de agrupamento:
    - Chave primária: (formulario_id, pares)
    - Se `pares` for None: usa item_pedido_id como fallback
    - tipo_arquivo determina se é capa ou miolo
    - Se tipo_arquivo estiver vazio, infere pelo nome do arquivo

    Args:
        arquivos: Lista de objetos ArquivoPdf
        formulario_id: ID do formulário (usado no resultado; se None, pega do 1º arquivo)

    Returns:
        ResultadoPareamento com pares agrupados, estatísticas e alertas
    """
    if not arquivos:
        return ResultadoPareamento(formulario_id=formulario_id or 0)

    if formulario_id is None:
        formulario_id = arquivos[0].formulario_id or 0

    resultado = ResultadoPareamento(
        formulario_id=formulario_id,
        total_arquivos=len(arquivos),
    )

    # Dict de pares indexado por chave
    pares_dict: Dict[str, ParPdf] = {}

    for arq in arquivos:
        form_id = arq.formulario_id or formulario_id

        # Determinar chave de agrupamento
        if arq.pares is not None:
            chave = f"{form_id}:{arq.pares}"
        else:
            # Fallback: usar item_pedido_id
            fallback = arq.item_pedido_id or arq.id
            chave = f"{form_id}:spec_{fallback}"

        # Criar par se não existe
        if chave not in pares_dict:
            pares_dict[chave] = ParPdf(
                formulario_id=form_id,
                pares=arq.pares,
            )

        par = pares_dict[chave]
        tipo = _classificar_tipo(arq)

        if tipo == "capa":
            if par.capa is not None:
                resultado.erros.append(
                    f"Par {arq.pares} (form {form_id}): capa duplicada — "
                    f"id={arq.id} ('{arq.nome}') conflita com id={par.capa.id} ('{par.capa.nome}')"
                )
            else:
                par.capa = arq
        else:  # miolo
            if par.miolo is not None:
                resultado.erros.append(
                    f"Par {arq.pares} (form {form_id}): miolo duplicado — "
                    f"id={arq.id} ('{arq.nome}') conflita com id={par.miolo.id} ('{par.miolo.nome}')"
                )
            else:
                par.miolo = arq

    # Compilar estatísticas
    resultado.pares = list(pares_dict.values())
    resultado.total_pares = len(resultado.pares)

    for par in resultado.pares:
        if par.completo:
            resultado.pares_completos += 1
        elif par.somente_miolo:
            resultado.pares_somente_miolo += 1
        elif par.somente_capa:
            resultado.pares_somente_capa += 1

        # Detectar divergências de nome
        divergencia = par.detectar_divergencia_nome()
        if divergencia:
            resultado.divergencias_nome.append(divergencia)

    logger.info(
        f"Pareamento formulário {formulario_id}: {resultado.total_arquivos} arquivos → "
        f"{resultado.total_pares} pares ({resultado.pares_completos} completos, "
        f"{resultado.pares_somente_miolo} só miolo, {resultado.pares_somente_capa} só capa, "
        f"{len(resultado.divergencias_nome)} divergências de nome)"
    )

    if resultado.erros:
        logger.warning(f"Erros no pareamento: {resultado.erros}")

    return resultado


def buscar_par_complementar(
    db: Session,
    arquivo: ArquivoPdf,
) -> Optional[ArquivoPdf]:
    """
    Dado um arquivo PDF (capa ou miolo), busca seu par complementar
    usando a coluna `pares` + `formulario_id` como chave.

    Se `pares` for NULL, busca pelo `item_pedido_id`.

    CORREÇÃO do bug original: antes usava apenas formulario_id + tipo_arquivo,
    retornando qualquer arquivo complementar do formulário (incorreto quando
    há múltiplos pares no mesmo formulário).

    Args:
        db: Sessão SQLAlchemy
        arquivo: ArquivoPdf principal (capa ou miolo)

    Returns:
        ArquivoPdf complementar ou None
    """
    tipo_principal = _classificar_tipo(arquivo)
    tipo_complementar = "capa" if tipo_principal == "miolo" else "miolo"

    if not arquivo.formulario_id:
        logger.warning(
            f"Arquivo {arquivo.id} ('{arquivo.nome}') sem formulario_id — "
            f"não é possível buscar par complementar"
        )
        return None

    # Filtro base: mesmo formulário + tipo complementar
    filtros = [
        ArquivoPdf.formulario_id == arquivo.formulario_id,
        ArquivoPdf.tipo_arquivo == tipo_complementar,
        ArquivoPdf.id != arquivo.id,
    ]

    # Filtro por pares (chave de agrupamento correta)
    if arquivo.pares is not None:
        filtros.append(ArquivoPdf.pares == arquivo.pares)
    else:
        # Fallback: mesmo item_pedido_id
        if arquivo.item_pedido_id is not None:
            filtros.append(ArquivoPdf.item_pedido_id == arquivo.item_pedido_id)

    complementar = db.query(ArquivoPdf).filter(and_(*filtros)).first()

    if complementar:
        logger.debug(
            f"Par encontrado: arquivo {arquivo.id} ({tipo_principal}) "
            f"↔ arquivo {complementar.id} ({tipo_complementar}), "
            f"pares={arquivo.pares}"
        )
    else:
        logger.debug(
            f"Sem par complementar para arquivo {arquivo.id} "
            f"(form={arquivo.formulario_id}, pares={arquivo.pares})"
        )

    return complementar


def buscar_pares_por_formulario(
    db: Session,
    formulario_id: int,
) -> ResultadoPareamento:
    """
    Busca todos os PDFs de um formulário e retorna o pareamento completo.

    Args:
        db: Sessão SQLAlchemy
        formulario_id: ID do formulário

    Returns:
        ResultadoPareamento com todos os pares do formulário
    """
    arquivos = (
        db.query(ArquivoPdf)
        .filter(ArquivoPdf.formulario_id == formulario_id)
        .order_by(ArquivoPdf.pares, ArquivoPdf.tipo_arquivo)
        .all()
    )

    logger.info(f"Formulário {formulario_id}: {len(arquivos)} arquivos encontrados")
    return agrupar_pares(arquivos, formulario_id=formulario_id)


def validar_pareamento(
    db: Session,
    formulario_id: int,
) -> Dict[str, Any]:
    """
    Valida a integridade dos pares de um formulário e retorna um relatório.
    
    Verifica:
    - Pares com capa mas sem miolo (ou vice-versa)
    - Pares duplicados (mais de uma capa ou miolo com mesmo valor de `pares`)
    - Divergências de nomes
    - Arquivos sem `pares` definido

    Args:
        db: Sessão SQLAlchemy
        formulario_id: ID do formulário

    Returns:
        Dict com relatório de validação
    """
    resultado = buscar_pares_por_formulario(db, formulario_id)

    problemas = []
    avisos = []

    for par in resultado.pares:
        if par.somente_capa:
            problemas.append(
                f"Par {par.pares}: tem capa (id={par.capa.id}, '{par.capa.nome}') "
                f"mas falta o miolo"
            )
        elif par.somente_miolo:
            # Miolo sem capa pode ser intencional (ex: prova simples)
            avisos.append(
                f"Par {par.pares}: tem miolo (id={par.miolo.id}, '{par.miolo.nome}') "
                f"sem capa associada"
            )

    for par in resultado.pares:
        if par.pares is None:
            if par.miolo:
                avisos.append(
                    f"Arquivo id={par.miolo.id} ('{par.miolo.nome}') sem valor "
                    f"na coluna 'pares' — usando item_pedido_id como fallback"
                )
            if par.capa:
                avisos.append(
                    f"Arquivo id={par.capa.id} ('{par.capa.nome}') sem valor "
                    f"na coluna 'pares' — usando item_pedido_id como fallback"
                )

    return {
        "formulario_id": formulario_id,
        "valido": len(problemas) == 0 and len(resultado.erros) == 0,
        "resumo": {
            "total_arquivos": resultado.total_arquivos,
            "total_pares": resultado.total_pares,
            "completos": resultado.pares_completos,
            "somente_miolo": resultado.pares_somente_miolo,
            "somente_capa": resultado.pares_somente_capa,
        },
        "problemas": problemas,
        "avisos": avisos,
        "divergencias_nome": resultado.divergencias_nome,
        "erros_duplicacao": resultado.erros,
        "pares": resultado.to_dict()["pares"],
    }
