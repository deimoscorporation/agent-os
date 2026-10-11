PYTHON ?= .venv/bin/python

.PHONY: help install migrate run test check superuser secrets-key

help:
	@echo "make install     cria o ambiente virtual e instala as dependências"
	@echo "make migrate     aplica as migrações do banco"
	@echo "make run         sobe o servidor de desenvolvimento em http://localhost:8000"
	@echo "make test        roda a suíte de testes"
	@echo "make check       verifica a configuração e migrações pendentes"
	@echo "make superuser   cria uma conta de equipe (acesso ao /admin/ e à fila de revisão)"
	@echo "make secrets-key gera uma chave para DEIMOS_SECRETS_KEY"

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

migrate:
	$(PYTHON) manage.py migrate

run:
	$(PYTHON) manage.py runserver

test:
	$(PYTHON) manage.py test

check:
	$(PYTHON) manage.py check
	$(PYTHON) manage.py makemigrations --check --dry-run

superuser:
	$(PYTHON) manage.py createsuperuser

secrets-key:
	@$(PYTHON) -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
