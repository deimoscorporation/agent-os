from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from core import crypto


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("O e-mail é obrigatório.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra["is_staff"] = True
        extra["is_superuser"] = True
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Conta identificada por e-mail (sempre em minúsculas)."""

    email = models.EmailField("e-mail", unique=True)
    full_name = models.CharField("nome completo", max_length=150)
    is_staff = models.BooleanField("membro da equipe", default=False)
    is_active = models.BooleanField("ativo", default=True)
    date_joined = models.DateTimeField("criado em", default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    def __str__(self):
        return self.email

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email

    @property
    def initials(self):
        parts = self.full_name.split()
        if not parts:
            return self.email[:1].upper()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


class OpenAICredential(models.Model):
    """Chave de API da OpenAI do próprio usuário, guardada criptografada."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="openai_credential")
    ciphertext = models.TextField()
    hint = models.CharField(max_length=4, blank=True)
    # Modelos de conversa a que a chave tem acesso, lidos da OpenAI ao validar.
    available_models = models.JSONField(default=list, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "chave da OpenAI"
        verbose_name_plural = "chaves da OpenAI"

    def __str__(self):
        return f"Chave da OpenAI de {self.user}"

    def reveal(self):
        """Só para chamar a API em nome do usuário — nunca exponha em views."""
        return crypto.decrypt(self.ciphertext)

    @classmethod
    def store(cls, user, api_key, models=None):
        credential, _ = cls.objects.update_or_create(
            user=user,
            defaults={
                "ciphertext": crypto.encrypt(api_key),
                "hint": crypto.hint(api_key),
                "available_models": models or [],
                "validated_at": timezone.now() if models is not None else None,
            },
        )
        return credential
