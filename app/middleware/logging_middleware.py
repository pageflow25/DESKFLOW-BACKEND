"""
Middleware de logging para requisições HTTP
"""
import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.config.logging_config import get_logger

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware para logging automático de todas as requisições HTTP
    """
    
    async def dispatch(self, request: Request, call_next):
        # Registrar início da requisição
        start_time = time.time()
        
        # Log da requisição recebida
        logger.info(
            f"Requisição recebida: {request.method} {request.url.path}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown"
            }
        )
        
        # Processar requisição
        try:
            response = await call_next(request)
            
            # Calcular tempo de processamento
            process_time = time.time() - start_time
            
            # Log da resposta
            logger.info(
                f"Requisição concluída: {request.method} {request.url.path} - "
                f"Status: {response.status_code} - Tempo: {process_time:.3f}s",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "process_time": process_time
                }
            )
            
            # Adicionar header com tempo de processamento
            response.headers["X-Process-Time"] = str(process_time)
            
            return response
            
        except Exception as e:
            # Log de erro
            process_time = time.time() - start_time
            logger.error(
                f"Erro ao processar requisição: {request.method} {request.url.path} - "
                f"Erro: {str(e)} - Tempo: {process_time:.3f}s",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "error": str(e),
                    "process_time": process_time
                },
                exc_info=True
            )
            raise
