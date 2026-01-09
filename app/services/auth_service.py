from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from ..models.usuario import Usuario
from ..config.settings import get_settings
from ..config.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)

# Configuração de criptografia com configuração específica para compatibilidade
pwd_context = CryptContext(
    schemes=["bcrypt"], 
    deprecated="auto",
    bcrypt__default_rounds=12,
    bcrypt__min_rounds=4,
    bcrypt__max_rounds=31
)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha fornecida corresponde ao hash armazenado"""
    # bcrypt tem limite de 72 bytes para senhas
    if len(plain_password.encode('utf-8')) > 72:
        plain_password = plain_password[:72]
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Gera hash da senha"""
    # bcrypt tem limite de 72 bytes para senhas
    if len(password.encode('utf-8')) > 72:
        password = password[:72]
    return pwd_context.hash(password)

def authenticate_user(db: Session, username: str, password: str) -> Optional[Usuario]:
    """Autentica usuário com username e senha"""
    logger.debug(f"Autenticando: {username}")
    
    user = db.query(Usuario).filter(
        (Usuario.username == username) | (Usuario.email == username)
    ).first()
    
    if not user:
        logger.warning(f"Usuário não encontrado: {username}")
        return None
    
    if not verify_password(password, user.senha_hash):
        logger.warning(f"Senha incorreta: {username}")
        return None
    
    if not user.is_ativo:
        logger.warning(f"Usuário inativo: {username}")
        return None
    
    logger.info(f"Autenticado: {username} | ID: {user.id}")
    return user

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Cria token JWT"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    
    username = data.get('username', 'unknown')
    logger.debug(f"Token criado: {username} | Expira: {expire.strftime('%Y-%m-%d %H:%M:%S')}")
    return encoded_jwt

def verify_token(token: str) -> Optional[dict]:
    """Verifica e decodifica token JWT"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: int = payload.get("sub")
        username: str = payload.get("username")
        roles: str = payload.get("roles")
        
        if user_id is None:
            logger.warning("Token inválido: user_id não encontrado")
            return None
        
        logger.debug(f"Token validado: {username}")
        return {"user_id": user_id, "username": username, "roles": roles}
    except JWTError as e:
        logger.warning(f"Erro ao validar token: {str(e)}")
        return None

def check_pcp_permission(roles: str) -> bool:
    """Verifica se o usuário tem permissão para acessar o sistema PCP"""
    if not roles:
        return False
    
    user_roles = [role.strip().lower() for role in roles.split(',')]
    allowed_roles = ['gerente', 'admin']
    
    return any(role in user_roles for role in allowed_roles)
