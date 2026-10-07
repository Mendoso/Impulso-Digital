# CRISAVYA Learning

Plataforma de ensino digital para Moçambique, desenvolvida a partir do documento «Quero que desenvolva uma plataforma de ensino». Implementação nova baseada nos requisitos fornecidos; não é uma exportação do site anterior.

## Funcionalidades implementadas

- Catálogo público e página de apresentação do curso.
- Contas de aluno com palavra-passe protegida por hash e sessão autenticada.
- Checkout em meticais com seleção M-Pesa, e-Mola, mKesh e Visa/Mastercard.
- **Modo demonstrativo sem cobrança:** simulação explícita de confirmação, matrícula automática e aviso na área do aluno.
- Modo real condicionado a um adaptador de gateway contratado; webhook autenticado, validação de montante, moeda e referência, e processamento idempotente.
- Área do aluno: aulas de leitura, vídeos incorporados, links de materiais, progresso e histórico de pedidos.
- Avaliação final desbloqueada após todas as aulas; mínimo de 70% para aprovação.
- Certificado com código único, página pública de verificação e impressão/guardar em PDF pelo navegador.
- Administração: criar cursos em rascunho, adicionar e editar aulas, adicionar questões, publicar/retirar cursos e consultar pedidos.
- Curso inicial de **750 MT**, com **5 módulos e 25 aulas de leitura demonstrativas**. Não existem vídeos ou PDFs definitivos pré-carregados; o administrador deve inseri-los.

## Executar localmente

Requisitos: Python 3.12+ e acesso ao PyPI. Comandos dentro do checkout existente; não é necessário criar um worktree.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/flask --app app init-db
.venv/bin/flask --app app seed-demo
.venv/bin/flask --app app run --host 0.0.0.0 --port 8000
```

Aceda pela interface disponibilizada pelo seu alojamento. O comando `seed-demo` preserva catálogos existentes. Não execute o servidor Flask de desenvolvimento na internet pública.

Crie o administrador interativamente, sem guardar a palavra-passe no histórico ou no Git:

```bash
.venv/bin/flask --app app create-admin
```

Depois entre na conta e abra `/admin`. Não há administrador nem palavra-passe padrão. Registar-se pela página pública cria apenas uma conta de aluno. A aplicação não lê `.env` automaticamente; configure variáveis no serviço de alojamento (consulte `.env.example`).

## Base de dados e GitHub

`schema.sql` contém o esquema versionado; `seed-demo` cria os dados iniciais. A base SQLite é criada em `instance/learning.sqlite3`, excluída do Git, tal como as sessões, credenciais e ambientes virtuais. **Dados reais de alunos, pagamentos e palavras-passe não devem ser publicados no GitHub.** O repositório armazena código, esquema, dependências, testes e documentação; o alojamento armazena a base operacional num volume persistente.

Faça backups pela API SQLite (`sqlite3.Connection.backup`) ou por ferramenta que suporte uma cópia consistente da base em uso. Teste a restauração antes da operação comercial. Para maior escala, migre para PostgreSQL com migrações próprias; esta versão usa SQL SQLite.

## Validar

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pip check
```

Os testes exercitam compra, bloqueio antes da confirmação, matrícula, isolamento entre alunos, conclusão das 25 aulas, reprovação/aprovação, certificado, administração, CSRF, login e webhooks válidos/inválidos/repetidos. A integração com operadoras e o pagamento de dinheiro real **não estão validados**.

## Alojamento

```bash
.venv/bin/gunicorn --workers 2 --bind 0.0.0.0:8000 --access-logfile - app:app
```

Use HTTPS no proxy, `COOKIE_SECURE=1`, `APP_SECRET_KEY` persistente e aleatório e um volume para a base. O health check `/saude` consulta a base. Não use `PAYMENT_MODE=live` antes de contratar e validar o gateway/adaptador em sandbox e fazer uma transação real controlada. Antes de venda real, também são necessários conteúdos autorizados, política de privacidade/reembolso, suporte, recuperação/verificação de contas, limitação de tentativas no proxy, backups e monitorização. Estas funcionalidades operacionais ainda não estão implementadas na aplicação.

Os vídeos YouTube/Vimeo e links públicos de PDFs não oferecem proteção contra partilha. Para conteúdos comerciais privados, adote alojamento com tokens/URLs temporárias e autorização por matrícula, em vez de links públicos.

Consulte [arquitetura e jornada](docs/ARQUITETURA.md) e [pagamentos e integração](docs/PAGAMENTOS.md).
