# Conteúdos CDE v1 — integração e revisão

## O que foi produzido

Preservam-se exatamente os cinco módulos e os 25 títulos/posições de `seed-demo`. O pacote propõe 20 horas: vinte aulas de 45 minutos e cinco práticas de 60 minutos. O tempo inclui prática, não apenas leitura. Os nomes, vagas e montantes usados nos exemplos são fictícios e adaptados a Moçambique.

| Material | Estado |
| --- | --- |
| 25 aulas: objetivos, explicação, exemplo, atividade, consolidação e rubrica | Produzido; revisão pedagógica pendente |
| Guia do formador: respostas esperadas | Produzido; não inserido no texto do aluno |
| 25 roteiros: locução integral, imagem e tempos propostos | Produzido; ensaio e ajuste de duração pendentes |
| 10 questões finais, duas por módulo, com respostas | Produzido; revisão pendente |
| Gravações, legendas finais e publicação de vídeos | Não realizadas |
| PDFs diagramados e publicados | Não produzidos |

`content_authoring.py` é a fonte editorial. `python content_authoring.py` gera `content/competencias-digitais/v1.json` (importação) e `CADERNO.md` (revisão humana com roteiros e guia). Não executar o gerador para sobrescrever edições diretas ao JSON: editar a fonte e gerar novamente. O teste exige correspondência entre fonte e pacote. O caderno é documentação versionada do repositório, não um PDF ou vídeo pronto.

## Importação segura

Não há importação no arranque, em `seed-demo`, por URL nem por pedido do aluno. O comando só é acessível a quem já administra o servidor. Os dados não são tratados como HTML nem executados. Não há downloads automáticos ou URLs multimédia inventados.

1. Executar primeiro numa cópia de teste da base e rever o pacote:

```bash
python -m flask --app app import-content
```

Sem `--apply`, valida e apresenta o SHA256 do pacote; não altera aulas/cursos. O arranque da aplicação prepara tabelas `IF NOT EXISTS` conforme o comportamento existente.

2. Aplicar explicitamente com um caminho **novo** de backup num diretório privado do operador:

```bash
python -m flask --app app import-content --apply --backup /caminho/privado/antes-cde-v1.sqlite3
```

O backup usa a API SQLite, recusa sobrescrever ficheiros e deve ser protegido como a base original. O diretório pai deve existir e ter acesso restrito. Testar restauração numa instância isolada. O backup pode conter dados pessoais e nunca deve entrar no Git.

A transação `BEGIN IMMEDIATE` cria um **novo curso não publicado**, 25 aulas e dez questões. Nenhum `UPDATE` é aplicado às aulas existentes, mesmo demonstrativas. Preservam-se alunos, encomendas, matrículas, progresso, certificados, personalizações e multimédia. Não se transferem matrículas para o novo curso. O preço de referência de 750 MZN é mantido no rascunho; não constitui decisão de lançamento.

O mesmo ID de pacote e SHA não duplica o curso. O mesmo ID com outro SHA é recusado. Uma versão posterior requer ID e política de validação novos num PR revisto; não contornar a proteção para substituir dados. Falhas revertem toda a criação do curso. Um backup produzido antes de uma falha continua disponível.

## Revisão e publicação separadas

O administrador consulta as aulas e questões em `/admin`; o guia de respostas e roteiros está no caderno. O revisor deve verificar:

- adequação ao nível inicial, português, exemplos locais e acessibilidade;
- execução real dos exercícios no software disponível, incluindo variantes de fórmulas;
- correção dos cálculos, respostas e rubricas; cobertura dos cinco módulos;
- privacidade, ausência de promessas de emprego e uso exclusivo de dados fictícios;
- direitos dos materiais futuros, legendas, legibilidade, consumo de dados e alternativa offline;
- coerência entre texto, futura demonstração gravada e avaliação.

Depois de concluir a revisão do estado **atual na base**, obter a impressão digital:

```bash
python -m flask --app app review-content --course-id 2
```

Usar o ID efetivamente devolvido pela importação, não assumir `2`. Registar apenas uma revisão realmente realizada:

```bash
python -m flask --app app approve-content --course-id 2 --sha256 SHA_DO_ESTADO_REVISTO --reviewer 'Nome do revisor responsável' --confirm-reviewed
```

Esta declaração é um registo do operador, não uma assinatura digital nem verificação automática da qualidade pedagógica. O SHA do estado na base é diferente do SHA do pacote. Se houver alterações entre revisão e aprovação/publicação, a impressão digital deixa de corresponder. A aprovação não publica: é necessária a ação administrativa Publicar, após rever condições comerciais e a identificação do curso. Edições de aulas e adição de aulas/questões pela administração retiram a publicação e exigem nova revisão. Cursos antigos sem registo de importação mantêm o fluxo existente.

O curso importado fica inacessível aos alunos enquanto a revisão está pendente. Para abandonar uma importação, deixar o curso em rascunho; não eliminar dados de produção. Restaurar backup requer manutenção planeada e pode perder transações posteriores: não restaurar automaticamente.

## Avaliação proposta e limites atuais

Cada aula tem uma atividade e consolidação avaliáveis por um formador: execução 4, correção/justificação 3, clareza 2 e privacidade 1 ponto; 7/10 propostos com feedback e reapresentação. A evidência e a resposta esperada são específicas de cada aula. O portefólio final reúne índice, CV, orçamento, candidatura de treino e plano de 30 dias.

As dez questões finais usam o mecanismo existente da plataforma, com aprovação a 70%. A aplicação **ainda não recolhe nem classifica automaticamente entregas práticas**: as rubricas são orientação para acompanhamento humano e não bloqueiam a emissão do certificado. Marcar uma aula como concluída continua a ser uma declaração do aluno. Se a política de certificação exigir aprovação das práticas, implementar submissão e validação do formador antes do lançamento sob essa política. Nenhuma destas aprovações foi presumida neste pacote.

## Produção audiovisual futura

Ensaiar os roteiros de sete minutos e ajustar tempos à demonstração; não acelerar locução para caber no relógio. Gravar em dispositivo e software representativos do público. Ampliar controlos, usar contraste, legendas em português e descrição verbal das ações. Ocultar contas, notificações, códigos e dados de terceiros. Não gravar credenciais reais. Publicar apenas depois de revisão e autorização, testando em telemóvel e ligação limitada. Acrescentar URLs HTTPS válidos e suportados pelo formulário existente; isso exige nova revisão do curso importado. O JSON v1 deixa ambas as URLs vazias de propósito.

## Validação técnica

```bash
python -m unittest discover -s tests -v
```

Os testes verificam estrutura e correspondência editorial, rejeição de pacotes incompletos, simulação, preservação de aulas personalizadas, backup, idempotência, rollback integral, bloqueio de publicação, aprovação separada e invalidação após edição. Executar a suite existente também verifica os fluxos anteriores de conta, compra, avaliação e certificado. Não prova eficácia pedagógica nem substitui pilotagem com formandos.
