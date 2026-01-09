from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import timedelta
from typing import Dict, Any
from ..config.database import get_db
from ..config.settings import get_settings
from ..config.logging_config import get_logger
from ..schemas.auth import LoginRequest, LoginResponse
from ..services.auth_service import authenticate_user, create_access_token, verify_token, check_pcp_permission

settings = get_settings()
security = HTTPBearer()
logger = get_logger(__name__)

class AuthController:
    """Controller para operações de autenticação"""
    
    @staticmethod
    async def login(login_data: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
        """
        Endpoint de autenticação
        Verifica credenciais e retorna JWT se usuário for gerente ou admin
        """
        logger.info(f"Tentativa de login: {login_data.username}")
        try:
            user = authenticate_user(db, login_data.username, login_data.password)
            
            if not user:
                logger.warning(f"Autenticação falhou: {login_data.username}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Credenciais inválidas",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # Verificar se o usuário tem permissão para acessar o sistema PCP
            if not user.is_pcp_authorized():
                logger.warning(f"Acesso negado - Sem permissão PCP: {login_data.username} (roles: {user.roles})")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Acesso negado. Apenas gerentes e administradores podem acessar o sistema PCP.",
                )
            
            # Criar token JWT
            access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={
                    "sub": str(user.id),
                    "username": user.username,
                    "roles": user.roles
                },
                expires_delta=access_token_expires
            )
            
            logger.info(f"Login bem-sucedido: {login_data.username} | ID: {user.id} | Roles: {user.roles}")
            
            return LoginResponse(
                access_token=access_token,
                token_type="bearer",
                user_id=user.id,
                user_name=user.nome or user.username,
                roles=user.roles
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro interno na autenticação para usuário {login_data.username}: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro interno no processo de autenticação: {str(e)}"
            )

    @staticmethod
    async def validate_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
        """
        Endpoint para validar token JWT
        """
        logger.debug("Requisição de validação de token recebida")
        try:
            token_data = verify_token(credentials.credentials)
            
            if not token_data:
                logger.warning("Token inválido recebido na validação")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token inválido",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # Verificar permissões PCP
            if not check_pcp_permission(token_data.get("roles")):
                logger.warning(f"Permissão PCP negada para usuário: {token_data.get('username')} (roles: {token_data.get('roles')})")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Acesso negado ao sistema PCP",
                )
            
            logger.debug(f"Token validado com sucesso para usuário: {token_data.get('username')}")
            return {"valid": True, "user_data": token_data}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Erro interno na validação do token: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Erro interno na validação do token: {str(e)}"
            )