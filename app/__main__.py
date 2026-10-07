"""Linha de comando do DESKFLOW2.0.

    python -m deskflow2 worker                      # serviço: os ciclos da fila em loop
    python -m deskflow2 verificar                   # testa banco e login no ERP
    python -m deskflow2 dry-run --orcamento 123     # mostra o corpo que iria ao ERP (não muda nada)
"""

import argparse
import json
import logging
import sys

from .config import get_settings
from .logging_config import configurar_logging

logger = logging.getLogger("deskflow2")


def _dry_run(app, orcamento_id: int) -> int:
    """Corpo que o SQL monta para um orçamento já gravado em
    `orcamento_api_orcamentos`. Só leitura, e sem passar pela fila: serve para
    conferir o payload de um orçamento real antes de enfileirá-lo."""
    from .repositorios.fila import carregar_orcamento
    from .servicos.pcp.payload import PayloadIncompleto, montar_payload_orcamento

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
    if app.fila is None:
        print("Fila de processamento: desligada (FILA_ATIVA=false ou catálogo fila_* ausente)")
    else:
        print(
            f"Fila de processamento: worker {app.fila.worker_id}, "
            f"pool sincrono={app.fila.sincrono.capacidade} assincrono={app.fila.assincrono.capacidade}, "
            f"destino {app.settings.FILA_DESTINO}"
        )
    app.erp.garantir_login()
    print("ERP: login ok")
    print(f"Download: {app.settings.DOWNLOAD_BASE_PATH or '(desligado: DOWNLOAD_BASE_PATH vazio)'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="deskflow2", description="Consumidor da fila do PCP (PageFlow -> Wingraph)")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("worker", help="roda os ciclos da fila em loop")
    sub.add_parser("verificar", help="testa banco e login no ERP")
    dry = sub.add_parser("dry-run", help="mostra o corpo do orçamento sem enviar")
    dry.add_argument("--orcamento", type=int, required=True)
    args = parser.parse_args(argv)

    settings = get_settings()
    configurar_logging(settings.LOG_DIR, settings.LOG_LEVEL)

    from .app import montar_aplicacao

    app = montar_aplicacao(settings)
    try:
        if args.comando == "worker":
            if app.fila is None:
                logger.warning("Fila inativa (FILA_ATIVA=false ou catálogo fila_* ausente): worker não iniciado")
                return 0
            from .jobs import criar_agendador

            logger.info(
                "DESKFLOW2.0 iniciado: fila de processamento, poll sincrono %ss / assincrono %ss, download %s",
                settings.FILA_POLL_SINCRONO_SEGUNDOS,
                settings.FILA_POLL_ASSINCRONO_SEGUNDOS,
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
    finally:
        app.fechar()
    return 1


if __name__ == "__main__":
    sys.exit(main())
