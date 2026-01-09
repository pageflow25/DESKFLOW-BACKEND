from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, dashboard, pedido_cascata, orcamento
from app.config.settings import get_settings
from app.config.logging_config import setup_logging, get_logger
from app.middleware import RequestLoggingMiddleware

# Inicializar logging
setup_logging()
logger = get_logger(__name__)

settings = get_settings()

def create_app() -> FastAPI:
    logger.info("Criando aplicação FastAPI...")
    app = FastAPI(
        title=settings.APP_NAME,
        debug=settings.DEBUG,
        version="1.0.0",
        description="Sistema PCP - Planejamento e Controle de Produção"
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
