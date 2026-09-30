"""Regras de payload dos cinco tipos de cliente.

Juntas, mostram uma simetria que estava escondida quando cada regra vivia no
`preparar()` do seu handler: `criar` e `atualizar` exigem CNPJ ou CPF **nao para
enviar ao ERP**, e sim porque sem documento nao ha como VERIFICAR um resultado
incerto — os dois sao nao idempotentes e o documento e a unica chave natural que
`cliente.consultar` sabe consultar. A mensagem de recusa diz isso, e diz igual
nos dois.
"""

from typing import Any, Optional, Tuple

from .comum import PayloadInvalido, inteiro_positivo, so_digitos


def documento_do_payload(payload: dict) -> str:
    """CNPJ ou CPF, na ordem em que o PageFlow os escolhe. O produtor já manda
    `documento` pronto; os outros dois caminhos existem para o item montado à
    mão pela tela de reprocesso."""
    payload = payload or {}
    cliente = payload.get("cliente") or {}
    for bruto in (payload.get("documento"), cliente.get("cnpj"), cliente.get("cpf"),
                  payload.get("cnpj"), payload.get("cpf"), payload.get("cpfcnpj")):
        documento = so_digitos(bruto)
        if documento:
            return documento
    return ""


def pagina_do_payload(payload: Optional[dict]) -> Optional[int]:
    payload = payload or {}
    return inteiro_positivo(payload.get("pagina", payload.get("page")))


def montar_consulta(payload: dict) -> dict:
    """Traduz o payload do item nos parâmetros do GET.

    Os quatro do contrato do ERP: `id`, `cpfcnpj`, `email` e `page`. O
    documento vem normalizado (só dígitos), que é a forma com que o PageFlow o
    grava na chave de bloqueio.
    """
    payload = payload or {}
    consulta: dict = {}

    identificador = payload.get("id_cliente", payload.get("id"))
    if identificador not in (None, ""):
        try:
            consulta["id_cliente"] = int(identificador)
        except (TypeError, ValueError):
            pass

    documento = documento_do_payload(payload)
    if documento:
        consulta["cpfcnpj"] = documento

    email = (payload.get("email") or "").strip() if isinstance(payload.get("email"), str) else None
    if email:
        consulta["email"] = email

    pagina = payload.get("page", payload.get("pagina"))
    if pagina not in (None, ""):
        try:
            consulta["page"] = int(pagina)
        except (TypeError, ValueError):
            pass

    return consulta


def _cadastro_de(payload: dict) -> Any:
    cliente = (payload or {}).get("cliente")
    return cliente if isinstance(cliente, dict) and cliente else None


def validar_criar(payload: Optional[dict]) -> Tuple[Optional[dict], Optional[PayloadInvalido]]:
    """O cadastro a enviar em `POST /api/v1/cliente`."""
    payload = payload or {}

    cliente = _cadastro_de(payload)
    if cliente is None:
        return None, PayloadInvalido(
            "cliente.criar exige `payload.cliente` com o cadastro a enviar.")

    if not documento_do_payload(payload):
        return None, PayloadInvalido(
            "cliente.criar exige CNPJ ou CPF: sem documento não há como verificar um resultado incerto.")

    return cliente, None


def validar_atualizar(payload: Optional[dict]) -> Tuple[Optional[dict], Optional[PayloadInvalido]]:
    """O corpo do `PATCH`, já com o `id_cliente` resolvido.

    O id pode vir na raiz do payload ou dentro de `cliente`; a raiz ganha, que é
    a ordem que o produtor usa.
    """
    payload = payload or {}

    cliente = _cadastro_de(payload)
    if cliente is None:
        return None, PayloadInvalido(
            "cliente.atualizar exige `payload.cliente` com o cadastro completo.")

    id_cliente = inteiro_positivo(payload.get("id_cliente", cliente.get("id_cliente")))
    if id_cliente is None:
        return None, PayloadInvalido("cliente.atualizar exige id_cliente numérico no payload.")

    if not documento_do_payload(payload):
        return None, PayloadInvalido(
            "cliente.atualizar exige CNPJ ou CPF: sem documento não há como verificar um resultado incerto.")

    return {**cliente, "id_cliente": id_cliente}, None


def validar_consultar(payload: Optional[dict]) -> Tuple[Optional[dict], Optional[PayloadInvalido]]:
    """Os parâmetros do GET. Consulta sem critério nenhum listaria a base
    inteira, então é recusada."""
    consulta = montar_consulta(payload)
    if not consulta:
        return None, PayloadInvalido(
            "cliente.consultar exige ao menos um critério (id_cliente, documento, email ou page).")
    return consulta, None


def validar_sincronizar_pagina(
    payload: Optional[dict],
) -> Tuple[Optional[int], Optional[PayloadInvalido]]:
    """O número da página. Um item é UMA página, nunca a varredura inteira."""
    pagina = pagina_do_payload(payload)
    if pagina is None:
        return None, PayloadInvalido(
            "cliente.sincronizar_pagina exige `pagina` inteira e maior que zero.")
    return pagina, None
