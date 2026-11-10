# Portal do Colaborador

Portal interno de autoatendimento para colaboradores: **holerites em PDF**, **registros diários de ponto** e **espelhos de ponto em PDF**, com importação em lote pelo RH e trilha de auditoria de acessos.

**Acesse o portal em produção:** <https://pedropaixa.pythonanywhere.com/>
(acesso restrito; o usuário e a senha são fornecidos pelo RH)

![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)

---

## Sumário

- [Funcionalidades](#funcionalidades)
- [Stack](#stack)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Início rápido (desenvolvimento)](#início-rápido-desenvolvimento)
- [Fluxo do RH](#fluxo-do-rh)
- [Importação em lote](#importação-em-lote)
- [Segurança e LGPD](#segurança-e-lgpd)
- [Configuração (variáveis de ambiente)](#configuração-variáveis-de-ambiente)
- [Deploy com Docker](#deploy-com-docker)
- [Testes](#testes)
- [Pontos de atenção](#pontos-de-atenção)

## Funcionalidades

**Para o colaborador**
- Login com usuário e senha, troca obrigatória da senha temporária no primeiro acesso e recuperação de senha por e-mail.
- Consulta e download dos próprios holerites (mensal, adiantamento, 13º e férias), organizados por ano.
- Consulta dos registros de ponto diários: batidas, horas trabalhadas, extras, faltas e saldo do banco de horas.
- Consulta dos espelhos de ponto mensais em PDF.

**Para o RH (`/admin/`)**
- Cadastro de usuários e colaboradores (matrícula, CPF, cargo, setor).
- Importação em lote de **holerites** (PDFs ou ZIP), com identificação automática do colaborador pelo CPF.
- Importação em lote de **ponto** por planilha (CSV/XLSX), com modelo para download e prévia antes de confirmar.
- Importação em lote de **espelhos de ponto** em PDF.
- Logs de acesso somente leitura: logins, falhas, holerites vistos e baixados.

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12, Django 5.2 |
| Banco de dados | PostgreSQL 16 (SQLite em desenvolvimento) |
| Servidor de aplicação | Gunicorn + WhiteNoise |
| HTTPS / proxy | Caddy (certificado automático) |
| Senhas | Argon2id |
| Leitura de arquivos | pypdf (PDFs), openpyxl (planilhas) |
| Infra | Docker e Docker Compose |

## Estrutura do repositório

```
.
├── config/                 # settings, urls e wsgi do projeto Django
├── portal/                 # aplicação principal
│   ├── models.py           # Colaborador, Holerite, RegistroPonto, EspelhoPonto, LogAcesso
│   ├── views.py            # login, home, holerites e importação de holerites
│   ├── views_ponto.py      # importação de ponto por planilha
│   ├── views_espelho.py    # espelhos de ponto (consulta e importação)
│   ├── importacao.py       # leitura de PDFs/ZIP e identificação por CPF
│   ├── importacao_ponto.py # leitura de CSV/XLSX de ponto
│   ├── middleware.py       # troca de senha obrigatória
│   ├── admin.py            # configuração do admin
│   ├── migrations/
│   └── tests*.py           # testes automatizados
├── templates/              # templates HTML (inclui overrides do admin)
├── Dockerfile
├── docker-compose.yml      # Postgres + web (gunicorn) + Caddy
├── Caddyfile
├── .env.example
└── requirements.txt
```

## Início rápido (desenvolvimento)

```bash
git clone <URL_DO_SEU_REPOSITORIO>
cd portal-colaborador

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Acesse <http://127.0.0.1:8000/>. Em desenvolvimento, os e-mails de recuperação de senha aparecem no terminal e o banco é um SQLite local.

## Fluxo do RH

1. **Usuários → Adicionar:** crie o login e preencha o bloco *Colaborador* (matrícula, CPF, cargo). O colaborador será obrigado a trocar a senha temporária no primeiro acesso.
2. **Holerites:** envie um a um pelo admin ou em lote por **Importar holerites**.
3. **Ponto:** importe a planilha em **Importar ponto (planilha)** e os PDFs em **Importar espelhos (PDF)**.
4. **Logs de acesso:** acompanhe logins, falhas e downloads de documentos.

## Importação em lote

| Importação | Formato aceito | Identificação do colaborador | Observações |
|---|---|---|---|
| Holerites | PDFs ou ZIP | CPF no nome do arquivo ou dentro do PDF | Escolha mês e tipo; até 1000 PDFs de 5 MB cada. Reenviar o mesmo mês/tipo substitui o PDF. |
| Ponto (registros diários) | CSV ou XLSX (até 5 MB) | Coluna CPF ou Matrícula | Cabeçalho reconhecido por nome, sem diferenciar maiúsculas ou acentos. Colunas obrigatórias: CPF (ou Matrícula) e Data. Há um modelo para download e uma prévia antes de gravar. |
| Espelhos de ponto | PDFs ou ZIP | CPF no nome do arquivo ou dentro do PDF | Um espelho por colaborador e mês. |

## Segurança e LGPD

- Senhas com Argon2id, mínimo de 10 caracteres e validação contra senhas comuns.
- Troca de senha obrigatória no primeiro acesso; link de recuperação válido por 30 minutos.
- Bloqueio de 15 minutos após 5 falhas de login (por usuário + IP).
- Sessão expira após 30 minutos de inatividade; cookies `Secure` em produção.
- **Isolamento entre colaboradores:** toda consulta parte do colaborador logado. Pedir o documento de outra pessoa devolve 404, igual a um documento inexistente, e a tentativa é gravada como `acesso_negado`.
- **RH:** só abre PDFs quem tem a permissão correspondente (superusuários têm todas). Cada abertura fica registrada.
- **Arquivos:** os PDFs ficam fora de qualquer pasta pública, com nome aleatório, e são entregues apenas pela view após conferir o dono.
- **Upload:** só aceita PDF de verdade (confere a assinatura `%PDF-`), com limite de 5 MB.
- **Resposta HTTP:** `Cache-Control: private, no-store` e `nosniff`.
- Log de acessos a documentos para fins de LGPD.

## Configuração (variáveis de ambiente)

Copie `.env.example` para `.env` e preencha. **Nunca envie o `.env` ao Git.**

| Variável | Descrição |
|---|---|
| `DEBUG` | `0` em produção |
| `HTTPS` | `1` para forçar cookies seguros |
| `SECRET_KEY` | Chave longa e aleatória (`python -c "import secrets; print(secrets.token_urlsafe(50))"`) |
| `DOMINIO` | Domínio atendido pelo Caddy |
| `ALLOWED_HOSTS` | Hosts permitidos |
| `CSRF_TRUSTED_ORIGINS` | Ex.: `https://portal.suaempresa.com.br` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Conexão com o PostgreSQL (sem `DB_NAME`, usa SQLite) |
| `PRIVATE_MEDIA_ROOT` | Pasta privada dos PDFs |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | E-mail para recuperação de senha |

## Deploy com Docker

Requer Docker. Em um servidor, aponte o DNS do domínio para o IP dele e libere as portas 80 e 443.

```bash
cp .env.example .env        # edite o .env (Windows: copy .env.example .env)
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

O portal fica em `https://SEU_DOMINIO/`; o Caddy obtém e renova o certificado sozinho, e as migrações rodam na subida do contêiner. Para testar no seu PC sem domínio, use o bloco "Teste local" do `.env.example` e acesse `http://localhost/`.

```bash
docker compose logs -f web                                   # ver erros
docker compose up -d --build                                 # aplicar atualização do código
docker compose exec db pg_dump -U portal portal > backup.sql # backup do banco
```

Os PDFs ficam no volume `privatemedia`, que também precisa de backup. Não use `docker compose down -v`, pois apaga todos os volumes.

## Testes

```bash
python manage.py test
```

## Pontos de atenção

- O bloqueio de login usa o cache do Django. Com vários workers do Gunicorn, configure Redis como cache; atrás de proxy, `REMOTE_ADDR` é o IP do proxy (considere o `django-axes`).
- As telas de recuperação de senha usam os templates padrão do admin do Django.
- Faça backup do banco **e** da pasta `PRIVATE_MEDIA_ROOT`.

---

Uso interno. Os dados de colaboradores são pessoais e protegidos pela LGPD.
