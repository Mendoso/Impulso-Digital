"""Importação aditiva: nunca atualiza aulas existentes ou publica cursos."""
import hashlib
import json
import sqlite3
from pathlib import Path

import click
from content_authoring import MODULES, UNITS, student_text

PACKAGE = Path(__file__).parent/'content/competencias-digitais/v1.json'


def review_digest(con, cid):
    rows=[]
    for table,order in [('courses','id'),('lessons','position,id'),('questions','id')]:
        # Published state is changed separately; retain all pedagogical fields.
        fields='title,category,description,price,pass_score' if table=='courses' else '*'
        key='id' if table=='courses' else 'course_id'
        rows.append([tuple(r) for r in con.execute(f'SELECT {fields} FROM {table} WHERE {key}=? ORDER BY {order}',(cid,))])
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()


def load_package(path):
    raw = Path(path).read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError('Pacote excede 2 MB.')
    p = json.loads(raw)
    if p.get('schema_version') != 1 or p.get('package_id') != 'crisavya-cde-2026-v1' or p.get('review_status') != 'pending_pedagogical_review':
        raise ValueError('Versão, identidade ou estado editorial inválido.')
    if p.get('modules') != MODULES or p.get('course_title') != 'Competências Digitais para o Emprego':
        raise ValueError('Estrutura do curso inválida.')
    lessons = p.get('lessons', [])
    if len(lessons) != 25:
        raise ValueError('São necessárias 25 aulas.')
    for i,l in enumerate(lessons,1):
        if l.get('id') != f'CDE-{i:02}' or l.get('position') != i or l.get('module') != MODULES[(i-1)//5] or l.get('title') != UNITS[i-1][0]:
            raise ValueError('Identidade, ordem ou título de aula inválido.')
        for field in ('theory','example','consolidation','expected_answer','evidence','success_criteria'):
            if not isinstance(l.get(field),str) or len(l[field].strip()) < 40:
                raise ValueError('Secção incompleta: '+field)
        for field in ('objectives','guided_activity'):
            if not isinstance(l.get(field),list) or len(l[field]) < 2 or not all(isinstance(x,str) and x.strip() for x in l[field]):
                raise ValueError('Lista incompleta: '+field)
        rubric=l.get('rubric',[])
        if len(rubric)!=4 or not all(isinstance(r.get('criterion'),str) and type(r.get('points')) is int and r['points']>0 for r in rubric) or sum(r['points'] for r in rubric)!=10:
            raise ValueError('Rubrica inválida.')
        if l.get('assets') != dict(text='produzido_pendente_revisao',video='nao_gravado',pdf='nao_produzido',video_url='',material_url=''):
            raise ValueError('Estado de materiais inválido; não declarar multimédia inexistente.')
        scenes=l.get('video',{}).get('scenes',[])
        if len(scenes)!=6 or not all(all(isinstance(s.get(k),str) and s[k].strip() for k in ('time','visual','narration')) for s in scenes):
            raise ValueError('Roteiro incompleto.')
    return p,hashlib.sha256(raw).hexdigest()


QUESTIONS = [
 ('Para converter um currículo editável em PDF, qual é o procedimento correto?', ['Mudar apenas a extensão','Exportar para PDF e verificar todas as páginas','Criar um atalho','Apagar o original'],1),
 ('Uma pasta sincronizada perdeu um ficheiro após eliminação local. Qual a melhor prevenção?', ['Assumir que sincronização é backup','Dar acesso público','Manter backup separado e testar recuperação','Guardar só um atalho'],2),
 ('Qual descrição de competência é verificável num CV de iniciante?', ['Sou o melhor em tudo','Tenho experiência inventada','Garanto resultados em qualquer área','Criei no curso um orçamento com fórmulas e gráfico'],3),
 ('Antes de enviar um PDF, deve:', ['Abrir e verificar páginas, contactos e versão','Confiar apenas no nome','Eliminar o editável','Enviar sem rever'],0),
 ('Receitas de 8.500 MZN e despesas de 9.200 MZN dão que saldo simples?', ['700 MZN','17.700 MZN','−700 MZN','9.200 MZN'],2),
 ('Ao copiar =B2*C2 de D2 para D3, espera-se:', ['=B2*C2 sempre','Apenas texto','Apagar a fórmula','=B3*C3'],3),
 ('Uma candidatura de treino deve ser enviada:', ['A qualquer empresa sem revisão','Só ao destinatário de teste autorizado','A todos os contactos','Para um endereço suspeito'],1),
 ('Para apresentar um portefólio numa reunião online, a opção mais prudente é:', ['Partilhar todas as janelas privadas','Publicar a ligação da reunião','Fechar notificações e partilhar só a janela necessária','Gravar sem informar'],2),
 ('Uma chamada pede o PIN M-Pesa e um código SMS. Deve:', ['Recusar e verificar por canal oficial independente','Revelar porque sabe o seu nome','Enviar o código ao formador','Publicar o PIN'],0),
 ('Um pedido de aprovação de entrada que não iniciou deve ser:', ['Aprovado por rotina','Partilhado publicamente','Ignorado enquanto fornece a palavra-passe','Recusado e investigado pelo serviço legítimo'],3)
]


def import_draft(con, package, digest):
    """Caller owns transaction (BEGIN IMMEDIATE); no UPDATE of content rows."""
    old=con.execute('SELECT digest,course_id FROM content_imports WHERE package_id=?',(package['package_id'],)).fetchone()
    if old:
        if old[0] != digest:
            raise ValueError('Identidade já importada com outro SHA256. Prepare nova versão; não sobrescrever.')
        return old[1], False
    cid=con.execute('INSERT INTO courses(title,category,description,price,published) VALUES(?,?,?,?,0)',
        (package['course_title']+' [Revisão pedagógica v1]','Carreira & tecnologia','Rascunho editorial. Revisão pedagógica obrigatória. Não disponível para venda.',750)).lastrowid
    for l in package['lessons']:
        con.execute('INSERT INTO lessons(course_id,module,title,content,position) VALUES(?,?,?,?,?)',
                    (cid,l['module'],l['title'],student_text(l),l['position']))
    for prompt,options,answer in QUESTIONS:
        con.execute('INSERT INTO questions(course_id,prompt,options,answer) VALUES(?,?,?,?)',(cid,prompt,json.dumps(options,ensure_ascii=False),answer))
    con.execute('INSERT INTO content_imports(package_id,digest,course_id) VALUES(?,?,?)',(package['package_id'],digest,cid))
    return cid,True


def register_commands(app, db):
    @app.cli.command('import-content')
    @click.option('--package',type=click.Path(exists=True,dir_okay=False),default=str(PACKAGE))
    @click.option('--apply',is_flag=True,help='Criar nova cópia em rascunho; sem esta opção apenas validar.')
    @click.option('--backup',type=click.Path(dir_okay=False),help='Novo ficheiro SQLite para backup consistente obrigatório.')
    def import_content(package,apply,backup):
        try:
            payload,digest=load_package(package)
            click.echo(f'25 aulas / 5 módulos validados. SHA256: {digest}')
            if not apply:
                click.echo('SIMULAÇÃO: nenhuma aula ou curso alterado. --apply exige --backup.');return
            if not backup:
                raise ValueError('--backup é obrigatório com --apply.')
            # Exclusive creation refuses overwrite, symlinks and the operational DB itself.
            with open(backup,'xb'):
                pass
            with sqlite3.connect(backup) as snapshot:
                db().backup(snapshot)
            con=db()
            con.execute('BEGIN IMMEDIATE')
            try:
                cid,created=import_draft(con,payload,digest)
                con.commit()
            except Exception:
                con.rollback();raise
            click.echo(f'Curso {cid}: '+('rascunho criado; publicação bloqueada até revisão.' if created else 'pacote já importado; dados preservados.'))
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
            raise click.ClickException(str(error)) from error

    @app.cli.command('review-content')
    @click.option('--course-id',type=int,required=True)
    def review_content(course_id):
        if not db().execute('SELECT 1 FROM content_imports WHERE course_id=?',(course_id,)).fetchone():
            raise click.ClickException('Curso não importado.')
        click.echo('SHA256 do estado atual (rever aulas/questões em /admin): '+review_digest(db(),course_id))

    @app.cli.command('approve-content')
    @click.option('--course-id',type=int,required=True)
    @click.option('--sha256',required=True,help='SHA256 do estado atual emitido por review-content e efetivamente revisto.')
    @click.option('--reviewer',required=True,help='Nome do responsável pela revisão concluída.')
    @click.option('--confirm-reviewed',is_flag=True,required=True,help='Declarar que a revisão pedagógica e técnica foi concluída.')
    def approve_content(course_id,sha256,reviewer,confirm_reviewed):
        if not reviewer.strip() or not confirm_reviewed:
            raise click.ClickException('Identifique o revisor e confirme a revisão efetiva.')
        with db():
            db().execute('BEGIN IMMEDIATE')
            if review_digest(db(),course_id) != sha256:
                raise click.ClickException('Conteúdo alterado desde a revisão. Rever o estado atual.')
            result=db().execute("UPDATE content_imports SET review_status='approved',reviewer=?,reviewed_digest=?,reviewed_at=CURRENT_TIMESTAMP WHERE course_id=? AND review_status='pending'",(reviewer.strip(),sha256,course_id))
            if result.rowcount!=1:
                raise click.ClickException('Curso, SHA256 ou estado incompatível. Nada aprovado.')
        click.echo('Revisão registada. O curso continua em rascunho; publicação é uma ação administrativa separada.')
