"""Verificação de clientes no ERP: o cliente existe? o ERP já reflete o que
mandamos?

É a regra de negócio que resolve um resultado INCERTO de `cliente.criar` e
`cliente.atualizar` (e da planilha, que delega aos dois). Estava espalhada nos
controllers — `consultar_por_documento` e `id_cliente_de` em
`handlers/comum.py`, `divergencias` em `handlers/clientes/atualizar.py` — e
veio para cá para que a regra de clientes possa ser lida num lugar só.
"""

from typing import Any, Optional

from ...integracoes.erp import ErroErp, clientes_do_envelope, envelope_json, mensagem_de_erro, sucesso_erp
from ...utils.conversao import inteiro_positivo, so_digitos


def consultar_por_documento(erp, documento: str) -> Optional[dict]:
    """Busca UM cliente pelo documento. É o verificador de `cliente.criar` e
    `cliente.atualizar`.

    Devolve o registro, `None` quando o ERP respondeu que não existe, e levanta
    `ErroErp` quando a própria consulta não pôde ser concluída — nesse caso
    quem chama mantém o item `incerto`, porque uma consulta que falhou não é
    prova de ausência (é exatamente o falso negativo silencioso do
    `verificarClienteExistente` legado, que engole o erro e devolve
    `existe: false`).
    """
    resposta = erp.listar_clientes(cpfcnpj=documento)
    dados = envelope_json(resposta)
    if resposta.status_code >= 400 or not sucesso_erp(dados):
        raise ErroErp(
            f"consulta por documento não concluída (HTTP {resposta.status_code}): "
            f"{mensagem_de_erro(dados, resposta)}"
        )
    for registro in clientes_do_envelope(dados):
        if so_digitos(registro.get("cnpj")) == documento or so_digitos(registro.get("cpf")) == documento:
            return registro
    # Sem filtro batendo: o ERP respondeu a busca e não devolveu o documento.
    return None


def id_cliente_de(registro: Any) -> Optional[int]:
    if not isinstance(registro, dict):
        return None
    return inteiro_positivo(registro.get("id_cliente"))


# Não entram na comparação: `id_cliente` é a chave, e contato/endereço são
# listas aninhadas cuja identidade é por id — compará-las daria divergência
# constante por ordenação e por campo que o ERP normaliza.
CAMPOS_IGNORADOS = frozenset({"id_cliente", "contato", "endereco", "identifier"})


def _normalizar(valor: Any) -> str:
    if isinstance(valor, bool):
        return "1" if valor else "0"
    if isinstance(valor, (int, float)):
        return str(int(valor)) if float(valor).is_integer() else str(float(valor))
    texto = str(valor if valor is not None else "").strip()
    if texto.lower() in ("true", "false"):
        return "1" if texto.lower() == "true" else "0"
    return texto


def _iguais(enviado: Any, atual: Any) -> bool:
    if _normalizar(enviado) == _normalizar(atual):
        return True
    # cnpj/cpf/cep/telefone vão formatados de um lado e crus do outro.
    digitos_enviado, digitos_atual = so_digitos(enviado), so_digitos(atual)
    return bool(digitos_enviado) and digitos_enviado == digitos_atual


def divergencias(enviado: dict, registro: dict) -> list:
    """Campos escalares enviados que o ERP não reflete.

    Só compara o que foi enviado com valor e o que o ERP devolveu: campo ausente
    da resposta não é divergência, porque o GET do ERP não devolve tudo o que o
    PATCH aceita. Falso positivo aqui custa uma retentativa segura; falso
    negativo fecharia uma edição que não aconteceu — por isso a assimetria é
    deliberada.
    """
    fora = []
    for chave, valor in (enviado or {}).items():
        if chave in CAMPOS_IGNORADOS or isinstance(valor, (list, dict)):
            continue
        if _normalizar(valor) == "":
            continue
        if chave not in registro:
            continue
        if not _iguais(valor, registro.get(chave)):
            fora.append(chave)
    return fora
