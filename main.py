from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.routers import auth, dashboard, pedido_cascata, orcamento
from app.config.settings import get_settings
from app.config.logging_config import setup_logging, get_logger
from app.middleware import RequestLoggingMiddleware
from app.services.automacao_conveniado_service import (
    AutomacaoConveniadoService,
    executar_automacao_conveniados_agendada,
)

# Inicializar logging
setup_logging()
logger = get_logger(__name__)

settings = get_settings()
scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scheduler

    if settings.CONVENIADO_AUTOMACAO_ATIVA:
        timezone = ZoneInfo(settings.CONVENIADO_AUTOMACAO_TIMEZONE)
        scheduler = AsyncIOScheduler(timezone=timezone)

        horarios = AutomacaoConveniadoService.obter_horarios_scheduler()
        for hora, minuto in horarios:
            job_id = f"automacao_conveniado_{hora:02d}_{minuto:02d}"
            scheduler.add_job(
                executar_automacao_conveniados_agendada,
                CronTrigger(hour=hora, minute=minuto, timezone=timezone),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=600,
            )

        scheduler.start()
        horarios_fmt = ", ".join([f"{h:02d}:{m:02d}" for h, m in horarios])
        logger.info(
            f"Scheduler da automação de conveniados iniciado ({settings.CONVENIADO_AUTOMACAO_TIMEZONE}) "
            f"nos horários: {horarios_fmt}"
        )
    else:
        logger.info("Automação de conveniados desativada. Scheduler não será iniciado.")

    try:
        yield
    finally:
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler da automação de conveniados finalizado")
        scheduler = None

def create_app() -> FastAPI:
    logger.info("Criando aplicação FastAPI...")
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.DEBUG,
        version="1.0.0",
        description="Sistema PCP - Planejamento e Controle de Produção",
        lifespan=lifespan,
    )

    # Middleware de logging (adicionar antes de outros middlewares)
    app.add_middleware(RequestLoggingMiddleware)
    logger.info("Middleware de logging registrado")

    # CORS (se necessário)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Em produção, especifique os domínios permitidos
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("Middleware CORS registrado")

    # Incluir routers
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(pedido_cascata.router)
    app.include_router(orcamento.router)
    logger.info("Routers registrados: auth, dashboard, pedido_cascata, orcamento")

    # Health check endpoint
    @app.get("/healthz", tags=["Health"])
    async def health():
        logger.debug("Health check requisitado")
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "version": "1.0.0"
        }

    logger.info("Aplicação FastAPI criada com sucesso")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    # Ao executar via `python main.py` usamos o objeto `app` diretamente
    # e desativamos o reload (reload requer import string e pode encerrar
    # o processo pai em alguns ambientes). Para desenvolvimento com reload,
    # prefira usar: `uvicorn main:app --reload`.
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
