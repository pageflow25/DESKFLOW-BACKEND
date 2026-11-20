"""Infraestrutura da aplicação: configuração, banco, log, montagem e agendador.

- `config.py`         — `Settings` (pydantic-settings, lê o `.env`);
- `database.py`       — engine SQLAlchemy do Postgres do PageFlow;
- `logging_config.py` — log em console e arquivo com rotação diária;
- `app.py`            — monta as peças (`montar_aplicacao`);
- `agendador.py`      — os ciclos da fila no APScheduler.

Nada aqui conhece regra de domínio.
"""
