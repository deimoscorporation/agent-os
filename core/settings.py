"""
Configurações do Deimos Agent OS.

Tudo o que muda entre ambientes vem de variáveis de ambiente — veja `.env.example`.
Um arquivo `.env` na raiz do projeto é carregado automaticamente (sem sobrescrever
variáveis já definidas no ambiente).
"""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _flag(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


_load_dotenv(BASE_DIR / ".env")

# Desenvolvimento por padrão. Em produção: DJANGO_DEBUG=false.
DEBUG = _flag("DJANGO_DEBUG", True)

# Chave pública de desenvolvimento: serve só para rodar localmente.
_DEV_SECRET_KEY = 'django-insecure-^@&=n54zhnn8e^0glll8*0nh%p#jts%wg_^))!ofwc&shon8)!'
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Defina DJANGO_SECRET_KEY para rodar com DJANGO_DEBUG=false.")
    SECRET_KEY = _DEV_SECRET_KEY

# Chave (Fernet) que criptografa o cofre de segredos e as chaves de API dos usuários.
# Gere com: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Sem ela, a chave é derivada da SECRET_KEY (apenas desenvolvimento).
# Trocar esta chave torna ilegíveis os segredos já salvos.
SECRETS_ENCRYPTION_KEY = os.environ.get("DEIMOS_SECRETS_KEY", "")
if not SECRETS_ENCRYPTION_KEY and not DEBUG:
    raise ImproperlyConfigured("Defina DEIMOS_SECRETS_KEY para rodar com DJANGO_DEBUG=false.")

ALLOWED_HOSTS = _list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = _list("DJANGO_CSRF_TRUSTED_ORIGINS")

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # Atrás de um proxy que termina o TLS (nginx, Caddy, load balancer).
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = _flag("DJANGO_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0"))


# OpenAI — cada usuário cadastra a própria chave em "Minha conta".
# OPENAI_BASE_URL aceita qualquer servidor compatível com a API da OpenAI.
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
# Modelos oferecidos enquanto a chave do usuário ainda não foi validada; o primeiro é o padrão.
OPENAI_MODELS = _list("OPENAI_MODELS", "gpt-5-mini,gpt-5,gpt-4.1,gpt-4o-mini")
# Opcional: chave da instância, usada por quem não cadastrou a sua. Deixe vazio para exigir chave própria.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Os testes nunca usam chave real nem dependem do .env de quem roda.
if "test" in sys.argv:
    OPENAI_API_KEY = ""
    OPENAI_BASE_URL = "https://api.openai.test/v1"
    OPENAI_MODELS = ["gpt-5-mini", "gpt-5", "gpt-4.1", "gpt-4o-mini"]


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'accounts',
    'workspaces',
    'knowledge',
    'chat',
    'marketplace',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Todas as páginas exigem login, exceto as marcadas com @login_not_required.
    'django.contrib.auth.middleware.LoginRequiredMiddleware',
    'workspaces.middleware.WorkspaceMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

AUTH_USER_MODEL = 'accounts.User'
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'chat:index'

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'workspaces.context_processors.workspace',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'


DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get("DEIMOS_DATABASE_PATH") or BASE_DIR / 'agent_os.sqlite3',
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


LANGUAGE_CODE = 'pt-br'
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", 'America/Sao_Paulo')
USE_I18N = True
USE_TZ = True


STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
# Destino do `collectstatic` em produção.
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Arquivos enviados (anexos do chat e base de conhecimento). Não há MEDIA_URL de
# propósito: nada é servido direto do disco — os downloads passam por views que
# conferem quem está pedindo.
MEDIA_ROOT = Path(os.environ.get("DEIMOS_MEDIA_ROOT") or BASE_DIR / 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
