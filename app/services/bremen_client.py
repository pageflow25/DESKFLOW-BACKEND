"""
BremenClient — Cliente HTTP para a API Bremen com renovação automática de token.

Singleton que gerencia o ciclo de vida do token:
- Autentica automaticamente na primeira chamada
- Cacheia o token em memória
- Intercepta erro 401 (token expirado), renova e refaz a requisição
- Thread-safe via asyncio.Lock
"""

import httpx
import asyncio
import time
from typing import Dict, Any, Optional
from ..config.logging_config import get_logger
from ..config.settings import get_settings

logger = get_logger(__name__)
settings = get_settings()

# Margem de segurança: renovar o token 5 minutos antes da expiração
TOKEN_EXPIRY_MARGIN_SECONDS = 300
# Tempo padrão de vida do token Bremen (1 hora)
TOKEN_LIFETIME_SECONDS = 3600


class BremenClient:
    """
    Cliente HTTP singleton para a API Bremen com renovação automática de token.
    
    Uso:
        client = BremenClient()
        resposta = await client.post("/api/v1/orcamento", payload)
    """
    
    _instance: Optional["BremenClient"] = None
    _lock = asyncio.Lock()
    
    def __new__(cls) -> "BremenClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._token: Optional[str] = None
        self._token_obtained_at: float = 0
        self._auth_lock = asyncio.Lock()
        
        # Configurações de autenticação
        self._auth_url = settings.DEFAULT_URL
        self._identifier = settings.DEFAULT_IDENTIFIER
        self._user = settings.DEFAULT_USER
        self._password = settings.DEFAULT_PASSWORD
        
        # URL base da API
        self._base_url = settings.BREMEN_API_URL
        self._timeout = settings.API_TIMEOUT
        
        logger.info(
            f"BremenClient inicializado — "
            f"Auth URL: {self._auth_url}, "
            f"API Base: {self._base_url}"
        )
    
    @property
    def _token_expired(self) -> bool:
        """Verifica se o token está expirado ou próximo de expirar."""
        if not self._token:
            return True
        elapsed = time.time() - self._token_obtained_at
        return elapsed >= (TOKEN_LIFETIME_SECONDS - TOKEN_EXPIRY_MARGIN_SECONDS)
    
    async def _authenticate(self) -> str:
        """
        Obtém um novo token da API Bremen.
        
        Returns:
            str: Token no formato "Bearer <token>"
            
        Raises:
            Exception: Se a autenticação falhar
        """
        logger.info("BremenClient: Autenticando na API Bremen...")
        
        payload = {
            "identifier": self._identifier,
            "data": {
                "user": self._user,
                "password": self._password
            }
        }
        
        headers = {"Content-Type": "application/json"}
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self._auth_url,
                    json=payload,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
            
            # Extrair o token da resposta
            # A API pode retornar em data.token ou data.data.token
            token_value = None
            if isinstance(data, dict):
                if "token" in data:
                    token_value = data["token"]
                elif "data" in data and isinstance(data["data"], dict):
                    token_value = data["data"].get("token")
            
            if not token_value:
                # Se não encontrar campo específico, tentar usar o campo mais provável
                logger.warning(f"Formato de resposta de autenticação inesperado: {data}")
                raise ValueError(f"Token não encontrado na resposta de autenticação: {data}")
            
            # Garantir formato "Bearer <token>"
            if not token_value.startswith("Bearer "):
                token_value = f"Bearer {token_value}"
            
            self._token = token_value
            self._token_obtained_at = time.time()
            
            logger.info(
                "BremenClient: Token obtido com sucesso — "
                f"Expira em ~{TOKEN_LIFETIME_SECONDS // 60} minutos"
            )
            
            return self._token
            
        except httpx.HTTPStatusError as e:
            logger.error(
                f"BremenClient: Erro HTTP na autenticação: "
                f"{e.response.status_code} - {e.response.text}"
            )
            raise
        except Exception as e:
            logger.error(f"BremenClient: Erro na autenticação: {str(e)}")
            raise
    
    async def _ensure_token(self) -> str:
        """
        Garante que um token válido está disponível.
        Usa lock para evitar múltiplas autenticações simultâneas.
        
        Returns:
            str: Token válido
        """
        if not self._token_expired:
            return self._token
        
        async with self._auth_lock:
            # Double-check após adquirir o lock (outra coroutine pode ter renovado)
            if not self._token_expired:
                return self._token
            
            return await self._authenticate()
    
    async def _force_renew_token(self) -> str:
        """
        Força a renovação do token (usado quando recebe 401).
        
        Returns:
            str: Novo token
        """
        async with self._auth_lock:
            logger.warning("BremenClient: Forçando renovação do token (401 recebido)")
            self._token = None
            self._token_obtained_at = 0
            return await self._authenticate()
    
    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers HTTP padrão com o token atual."""
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": self._token or ""
        }
    
    async def post(
        self,
        path: str,
        payload: Dict[str, Any],
        timeout: Optional[int] = None
    ) -> httpx.Response:
        """
        Faz uma requisição POST para a API Bremen com renovação automática de token.
        
        Se receber 401, renova o token e refaz a requisição uma vez.
        
        Args:
            path: Caminho da API (ex: "/api/v1/orcamento")
            payload: Dados JSON a enviar
            timeout: Timeout em segundos (usa o padrão da settings se não informado)
            
        Returns:
            httpx.Response: Resposta da API
            
        Raises:
            httpx.HTTPStatusError: Se a requisição falhar após retry
        """
        url = f"{self._base_url}{path}"
        request_timeout = timeout or self._timeout
        
        # Garantir que temos um token válido
        await self._ensure_token()
        
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            # Primeira tentativa
            response = await client.post(
                url=url,
                headers=self._get_headers(),
                json=payload
            )
            
            # Se receber 401, renovar token e tentar novamente
            if response.status_code == 401:
                logger.warning(
                    f"BremenClient: 401 recebido em {path}. "
                    f"Resposta: {response.text[:200]}"
                )
                
                await self._force_renew_token()
                
                # Segunda tentativa com o token renovado
                response = await client.post(
                    url=url,
                    headers=self._get_headers(),
                    json=payload
                )
                
                if response.status_code == 401:
                    logger.error(
                        "BremenClient: 401 persistente após renovação de token. "
                        "Verifique as credenciais."
                    )
            
            return response
    
    @classmethod
    def reset(cls):
        """
        Reseta a instância singleton (útil para testes).
        """
        cls._instance = None
