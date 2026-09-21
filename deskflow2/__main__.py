"""Linha de comando do DESKFLOW2.0.

    python -m deskflow2 worker                      # serviço: todos os ciclos em loop
    python -m deskflow2 verificar                   # testa banco e login no ERP
    python -m deskflow2 dry-run --orcamento 123     # mostra o corpo que iria ao ERP (não muda nada)
    python -m deskflow2 despachar --orcamento 123   # despacha um orçamento pendente agora
    python -m deskflow2 ciclo orcamentos            # roda um ciclo uma vez (orcamentos|aprovacoes|downloads|reconciliacao)
"""

import argparse
import json
import logging
import sys

from .config import get_settings
from .logging_config import configurar_logging

logger = logging.getLogger("deskflow2")


def _dry_run(app, orcamento_id: int) -> int:
    from .repositorios.fila import carregar_orcamento
    from .servicos.payload import PayloadIncompleto, montar_payload_orcamento

    with app.engine.connect() as conn:
        orcamento = carregar_orcamento(conn, orcamento_id)
        if not orcamento:
            print(f"Orçamento #{orcamento_id} não encontrado", file=sys.stderr)
            return 1
        try:
            corpo = montar_payload_orcamento(conn, orcamento, app.settings.ERP_IDENTIFIER)
        except PayloadIncompleto as exc:
            print(f"Payload incompleto: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(corpo, ensure_ascii=False, indent=2, default=str))
    return 0


def _verificar(app) -> int:
    from sqlalchemy import text

    from .repositorios.status import carregar_catalogo

    with app.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        carregar_catalogo(conn)
    print("Banco: ok (catálogo de status do PCP encontrado)")
    app.erp.garantir_login()
    print("ERP: login ok")
    print(f"Modo de envio: {app.settings.PCP_MODO_ENVIO}")
    print(f"Download: {app.settings.DOWNLOAD_BASE_PATH or '(desligado: DOWNLOAD_BASE_PATH vazio)'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="deskflow2", description="Consumidor da fila do PCP (PageFlow -> Wingraph)")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("worker", help="roda todos os ciclos em loop")
    sub.add_parser("verificar", help="testa banco e login no ERP")
    dry = sub.add_parser("dry-run", help="mostra o corpo do orçamento sem enviar")
    dry.add_argument("--orcamento", type=int, required=True)
    desp = sub.add_parser("despachar", help="despacha um orçamento pendente agora")
    desp.add_argument("--orcamento", type=int, required=True)
    ciclo = sub.add_parser("ciclo", help="roda um ciclo uma vez")
    ciclo.add_argument("nome", choices=["orcamentos", "aprovacoes", "downloads", "reconciliacao"])
    args = parser.parse_args(argv)

    settings = get_settings()
    configurar_logging(settings.LOG_DIR, settings.LOG_LEVEL)

    from .app import montar_aplicacao

    app = montar_aplicacao(settings)
    try:
        if args.comando == "worker":
            if not settings.PCP_ENVIO_ATIVO:
                logger.warning("PCP_ENVIO_ATIVO=false: worker não iniciado")
                return 0
            from .jobs import criar_agendador

            logger.info(
                "DESKFLOW2.0 iniciado: modo %s, ciclo a cada %ss, download %s",
                settings.PCP_MODO_ENVIO,
                settings.PCP_ENVIO_INTERVALO_SEGUNDOS,
                settings.DOWNLOAD_BASE_PATH or "desligado",
            )
            try:
                criar_agendador(app).start()
            except (KeyboardInterrupt, SystemExit):
                logger.info("DESKFLOW2.0 encerrado")
            return 0
        if args.comando == "verificar":
            return _verificar(app)
        if args.comando == "dry-run":
            return _dry_run(app, args.orcamento)
        if args.comando == "despachar":
            app.erp.garantir_login()
            print(app.orcamentos.despachar(args.orcamento))
            return 0
        if args.comando == "ciclo":
            servico = getattr(app, args.nome)
            print(f"{args.nome}: {servico.executar_ciclo()} registro(s)")
            return 0
    finally:
        app.fechar()
    return 1


if __name__ == "__main__":
    sys.exit(main())
