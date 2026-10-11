"""Criptografia de segredos em repouso (cofre dos workspaces e chaves de API dos usuários)."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key = getattr(settings, "SECRETS_ENCRYPTION_KEY", "")
    if not key:
        # Desenvolvimento: deriva da SECRET_KEY. Em produção defina DEIMOS_SECRETS_KEY.
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Não foi possível decifrar o segredo: a chave de criptografia mudou.") from exc


def hint(value: str) -> str:
    """Últimos caracteres, só para o dono reconhecer o segredo. Vazio em valores curtos."""
    return value[-4:] if len(value) >= 12 else ""
