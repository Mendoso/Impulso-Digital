import functools
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import click
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).parent
METHODS = {'mpesa': 'M-Pesa', 'emola': 'e-Mola', 'mkesh': 'mKesh', 'card': 'Visa / Mastercard'}


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(exist_ok=True)
    key = os.environ.get('APP_SECRET_KEY')
    if not key:
        key_file = Path(app.instance_path) / 'session.key'
        if not key_file.exists():
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(secrets.token_hex(32))
        key = key_file.read_text()
    app.config.update(SECRET_KEY=key, DATABASE=os.environ.get('DATABASE_PATH', str(Path(app.instance_path) / 'learning.sqlite3')), PAYMENT_MODE=os.environ.get('PAYMENT_MODE', 'demo'), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1', MAX_CONTENT_LENGTH=1024*1024)
    if config:
        app.config.update(config)
    if app.config['PAYMENT_MODE'] not in ('demo', 'live'):
        raise RuntimeError('PAYMENT_MODE deve ser demo ou live')

    # Fresh checkouts do not include the operational database. Prepare its
    # schema before serving requests, including HEAD probes from the host.
    database_path = Path(app.config['DATABASE']).expanduser()
    app.config['DATABASE'] = str(database_path)
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=30)
        try:
            connection.executescript((ROOT / 'schema.sql').read_text())
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as error:
        raise RuntimeError(
            'Não foi possível preparar a base de dados. Verifique DATABASE_PATH '
            'e as permissões de escrita do diretório configurado.'
        ) from error

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'])
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
            g.db.execute('PRAGMA busy_timeout=5000')
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        if 'db' in g:
            g.db.close()

    def query(sql, args=(), one=False):
        rows = db().execute(sql, args).fetchall()
        return (rows[0] if rows else None) if one else rows

    def csrf():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return session['csrf']

    @app.before_request
    def before():
        g.user = query('SELECT * FROM users WHERE id=?', (session['uid'],), True) if session.get('uid') else None
        if request.method == 'POST' and request.endpoint != 'webhook':
            if not hmac.compare_digest(session.get('csrf', ''), request.form.get('csrf', '')) or not session.get('csrf'):
                abort(400, 'Sessão expirada. Recarregue a página.')

    @app.context_processor
    def context():
        return dict(csrf=csrf, methods=METHODS, payment_mode=app.config['PAYMENT_MODE'])

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-src https://www.youtube-nocookie.com https://player.vimeo.com; media-src 'self' https:; base-uri 'self'; form-action 'self'; frame-ancestors 'self'"
        if g.get('user'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    def login_required(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.user:
                if request.endpoint == 'checkout':
                    session['after_login'] = request.path
                return redirect(url_for('login'))
            return fn(*args, **kwargs)
        return wrapped

    def admin_required(fn):
        @login_required
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if g.user['role'] != 'admin':
                abort(403)
            return fn(*args, **kwargs)
        return wrapped

    def course_or_404(cid, public=False):
        course = query('SELECT * FROM courses WHERE id=?', (cid,), True)
        if not course or (public and not course['published']):
            abort(404)
        return course

    def require_enrollment(cid):
        if not query('SELECT 1 FROM enrollments WHERE user_id=? AND course_id=?', (g.user['id'], cid), True):
            abort(403, 'É necessária uma matrícula confirmada para aceder ao curso.')

    def complete_payment(order, event_id=None):
        # A single transaction makes event replay and concurrent callbacks safe.
        with db():
            if event_id:
                result = db().execute('INSERT OR IGNORE INTO payment_events(id,order_id) VALUES(?,?)', (event_id, order['id']))
                if not result.rowcount:
                    return
            db().execute("UPDATE orders SET status='paid' WHERE id=?", (order['id'],))
            db().execute('INSERT OR IGNORE INTO enrollments(user_id,course_id,order_id) VALUES(?,?,?)', (order['user_id'], order['course_id'], order['id']))
            db().execute('INSERT OR IGNORE INTO notifications(user_id,order_id,message) VALUES(?,?,?)', (order['user_id'], order['id'], 'Pagamento confirmado. O seu curso já está disponível.'))

    @app.route('/')
    def home():
        return render_template('home.html', courses=query('SELECT c.*, (SELECT COUNT(*) FROM lessons WHERE course_id=c.id) AS lesson_count FROM courses c WHERE published=1'))

    @app.route('/cursos/<int:cid>')
    def course(cid):
        return render_template('course.html', course=course_or_404(cid, True), lessons=query('SELECT id,module,title FROM lessons WHERE course_id=? ORDER BY position,id', (cid,)))

    @app.route('/registar', methods=['GET','POST'])
    def register():
        if request.method == 'POST':
            name = request.form.get('name','').strip()
            email = request.form.get('email','').strip().lower()
            password = request.form.get('password','')
            phone = request.form.get('phone','').strip()
            if not name or len(name)>120 or '@' not in email or len(email)>254 or len(password)<10 or len(phone)>30:
                flash('Informe nome, email válido e uma palavra-passe com pelo menos 10 caracteres.', 'error')
            else:
                try:
                    with db():
                        result = db().execute('INSERT INTO users(name,email,phone,password_hash) VALUES(?,?,?,?)', (name,email,phone,generate_password_hash(password)))
                    destination = session.get('after_login', url_for('dashboard'))
                    session.clear()
                    session['uid'] = result.lastrowid
                    return redirect(destination)
                except sqlite3.IntegrityError:
                    flash('Este email já está registado. Entre na sua conta.', 'error')
        return render_template('auth.html', registering=True)

    @app.route('/entrar', methods=['GET','POST'])
    def login():
        if request.method == 'POST':
            user = query('SELECT * FROM users WHERE email=?', (request.form.get('email','').strip().lower(),), True)
            if user and check_password_hash(user['password_hash'], request.form.get('password','')):
                destination = session.get('after_login', url_for('dashboard'))
                session.clear()
                session['uid'] = user['id']
                return redirect(destination)
            flash('Email ou palavra-passe incorretos.', 'error')
        return render_template('auth.html', registering=False)

    @app.post('/sair')
    def logout():
        session.clear()
        return redirect(url_for('home'))

    @app.route('/checkout/<int:cid>', methods=['GET','POST'])
    @login_required
    def checkout(cid):
        course = course_or_404(cid, True)
        if query('SELECT 1 FROM enrollments WHERE user_id=? AND course_id=?', (g.user['id'],cid), True):
            return redirect(url_for('learn', cid=cid))
        if request.method == 'POST':
            method = request.form.get('method')
            if method not in METHODS:
                abort(400)
            oid = uuid.uuid4().hex
            with db():
                db().execute('INSERT INTO orders(id,user_id,course_id,amount,method,source) VALUES(?,?,?,?,?,?)', (oid,g.user['id'],cid,course['price'],method,app.config['PAYMENT_MODE']))
            if app.config['PAYMENT_MODE'] == 'live':
                # Your contracted gateway adapter must implement the documented contract.
                endpoint = os.environ.get('PAYMENT_ADAPTER_URL','')
                token = os.environ.get('PAYMENT_ADAPTER_TOKEN','')
                if not endpoint.startswith('https://') or not token:
                    flash('Cobranças reais ainda não estão configuradas. Nenhum valor foi cobrado.', 'error')
                else:
                    try:
                        payload = json.dumps(dict(order_id=oid, amount=course['price'], currency='MZN', method=method, email=g.user['email'], phone=g.user['phone'])).encode()
                        req = Request(endpoint, data=payload, headers={'Content-Type':'application/json','Authorization':'Bearer '+token,'Idempotency-Key':oid})
                        with urlopen(req, timeout=15) as response:
                            data = json.load(response)
                        target = data.get('checkout_url','')
                        allowed = os.environ.get('PAYMENT_CHECKOUT_HOSTS','').split(',')
                        if urlparse(target).scheme != 'https' or urlparse(target).hostname not in allowed or not data.get('reference'):
                            raise ValueError('Invalid checkout response')
                        with db():
                            db().execute('UPDATE orders SET provider_ref=? WHERE id=?', (str(data['reference']),oid))
                        return redirect(target)
                    except Exception:
                        app.logger.warning('Falha ao iniciar cobrança; verificar o adaptador sem expor credenciais.')
                        flash('Não foi possível iniciar a cobrança. Consulte o suporte antes de tentar novamente.', 'error')
            return redirect(url_for('order_page', oid=oid))
        return render_template('checkout.html', course=course)

    @app.route('/pedidos/<oid>')
    @login_required
    def order_page(oid):
        order = query('SELECT o.*,c.title FROM orders o JOIN courses c ON c.id=o.course_id WHERE o.id=? AND o.user_id=?', (oid,g.user['id']), True)
        if not order:
            abort(404)
        return render_template('order.html', order=order)

    @app.post('/pedidos/<oid>/simular')
    @login_required
    def simulate(oid):
        order = query('SELECT * FROM orders WHERE id=? AND user_id=?', (oid,g.user['id']), True)
        if not order or app.config['PAYMENT_MODE'] != 'demo' or order['source'] != 'demo':
            abort(404)
        complete_payment(order)
        flash('Pagamento de demonstração confirmado. Nenhum dinheiro foi movimentado.', 'success')
        return redirect(url_for('learn', cid=order['course_id']))

    @app.post('/api/payments/webhook')
    def webhook():
        key = os.environ.get('PAYMENT_WEBHOOK_SECRET','')
        stamp = request.headers.get('X-Payment-Timestamp','')
        signature = request.headers.get('X-Payment-Signature','')
        raw = request.get_data()
        if app.config['PAYMENT_MODE'] != 'live' or not key:
            abort(503)
        try:
            if abs(time.time()-int(stamp))>300:
                abort(401)
        except ValueError:
            abort(401)
        expected = hmac.new(key.encode(), stamp.encode()+b'.'+raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            abort(401)
        data = request.get_json(silent=True) or {}
        order = query('SELECT * FROM orders WHERE id=?', (data.get('order_id'),), True)
        if not order or order['source'] != 'live' or not order['provider_ref'] or data.get('reference') != order['provider_ref'] or data.get('amount') != order['amount'] or data.get('currency') != 'MZN' or not isinstance(data.get('event_id'),str) or not 1<=len(data['event_id'])<=200:
            abort(400)
        if data.get('status') != 'paid':
            abort(400, 'Só são aceites confirmações de pagamento concluído.')
        complete_payment(order, data['event_id'])
        return jsonify(received=True)

    @app.route('/minha-conta')
    @login_required
    def dashboard():
        courses = query('SELECT c.*, (SELECT COUNT(*) FROM lessons WHERE course_id=c.id) AS total, (SELECT COUNT(*) FROM progress p JOIN lessons l ON l.id=p.lesson_id WHERE p.user_id=? AND l.course_id=c.id) AS completed FROM courses c JOIN enrollments e ON e.course_id=c.id WHERE e.user_id=?', (g.user['id'],g.user['id']))
        return render_template('dashboard.html', courses=courses, notifications=query('SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 5', (g.user['id'],)), orders=query('SELECT o.*,c.title FROM orders o JOIN courses c ON c.id=o.course_id WHERE user_id=? ORDER BY created_at DESC', (g.user['id'],)), certificates=query('SELECT * FROM certificates WHERE user_id=?',(g.user['id'],)))

    @app.route('/aprender/<int:cid>')
    @login_required
    def learn(cid):
        require_enrollment(cid)
        course = course_or_404(cid)
        lessons = query('SELECT * FROM lessons WHERE course_id=? ORDER BY position,id', (cid,))
        selected = request.args.get('aula', type=int)
        lesson = next((l for l in lessons if l['id']==selected), lessons[0] if lessons else None)
        completed = {r['lesson_id'] for r in query('SELECT lesson_id FROM progress WHERE user_id=?',(g.user['id'],))}
        return render_template('learn.html', course=course, lessons=lessons, lesson=lesson, completed=completed)

    @app.post('/aulas/<int:lid>/concluir')
    @login_required
    def finish_lesson(lid):
        lesson = query('SELECT * FROM lessons WHERE id=?',(lid,),True)
        if not lesson:
            abort(404)
        require_enrollment(lesson['course_id'])
        with db():
            db().execute('INSERT OR IGNORE INTO progress(user_id,lesson_id) VALUES(?,?)',(g.user['id'],lid))
        return redirect(url_for('learn', cid=lesson['course_id'], aula=lid))

    @app.route('/avaliacao/<int:cid>', methods=['GET','POST'])
    @login_required
    def assessment(cid):
        require_enrollment(cid)
        course = course_or_404(cid)
        remaining = query('SELECT COUNT(*) n FROM lessons l WHERE course_id=? AND NOT EXISTS (SELECT 1 FROM progress p WHERE p.lesson_id=l.id AND p.user_id=?)',(cid,g.user['id']),True)['n']
        questions = query('SELECT * FROM questions WHERE course_id=? ORDER BY id',(cid,))
        if remaining or not questions:
            flash('Conclua todas as aulas. A avaliação também precisa estar publicada pelo administrador.', 'error')
            return redirect(url_for('learn',cid=cid))
        if request.method=='POST':
            score = round(100*sum(request.form.get('q'+str(q['id']))==str(q['answer']) for q in questions)/len(questions))
            with db():
                db().execute('INSERT INTO attempts(user_id,course_id,score) VALUES(?,?,?)',(g.user['id'],cid,score))
                if score>=course['pass_score']:
                    db().execute('INSERT OR IGNORE INTO certificates(id,user_id,course_id,score) VALUES(?,?,?,?)',(uuid.uuid4().hex,g.user['id'],cid,score))
            if score>=course['pass_score']:
                certificate = query('SELECT id FROM certificates WHERE user_id=? AND course_id=?',(g.user['id'],cid),True)
                return redirect(url_for('certificate',token=certificate['id']))
            flash(f'Resultado: {score}%. Precisa de {course["pass_score"]}%. Reveja as aulas e tente novamente.', 'error')
        return render_template('assessment.html',course=course,questions=[dict(id=q['id'],prompt=q['prompt'],options=json.loads(q['options'])) for q in questions])

    @app.route('/certificados/<token>')
    def certificate(token):
        cert = query('SELECT t.*,u.name,c.title,o.source FROM certificates t JOIN enrollments e ON e.user_id=t.user_id AND e.course_id=t.course_id JOIN orders o ON o.id=e.order_id JOIN users u ON u.id=t.user_id JOIN courses c ON c.id=t.course_id WHERE t.id=?',(token,),True)
        if not cert:
            abort(404)
        return render_template('certificate.html',cert=cert)

    @app.route('/admin', methods=['GET','POST'])
    @admin_required
    def admin():
        if request.method=='POST':
            action = request.form.get('action')
            try:
                with db():
                    if action=='course':
                        price=int(request.form['price']); title=request.form['title'].strip()
                        if price<0 or not title:
                            raise ValueError()
                        db().execute('INSERT INTO courses(title,category,description,price) VALUES(?,?,?,?)',(title,request.form['category'].strip(),request.form['description'].strip(),price))
                    elif action=='lesson':
                        cid=int(request.form['course_id']); course_or_404(cid)
                        title=request.form['title'].strip(); content=request.form['content'].strip()
                        if not title or not content:
                            raise ValueError()
                        video=request.form.get('video_url','').strip(); material=request.form.get('material_url','').strip()
                        if video and not (video.startswith('https://www.youtube-nocookie.com/embed/') or video.startswith('https://player.vimeo.com/video/')):
                            raise ValueError()
                        if material and urlparse(material).scheme!='https':
                            raise ValueError()
                        db().execute('INSERT INTO lessons(course_id,module,title,content,video_url,material_url,position) VALUES(?,?,?,?,?,?,?)',(cid,request.form['module'].strip(),title,content,video,material,int(request.form['position'])))
                    elif action=='question':
                        cid=int(request.form['course_id']);course_or_404(cid)
                        options=[request.form.get('option'+str(i),'').strip() for i in range(4)]
                        answer=int(request.form['answer']);prompt=request.form['prompt'].strip()
                        if not all(options) or answer not in range(4) or not prompt:
                            raise ValueError()
                        db().execute('INSERT INTO questions(course_id,prompt,options,answer) VALUES(?,?,?,?)',(cid,prompt,json.dumps(options),answer))
                    elif action=='publish':
                        cid=int(request.form['course_id']); course=course_or_404(cid)
                        if not query('SELECT 1 FROM lessons WHERE course_id=?',(cid,),True) or not query('SELECT 1 FROM questions WHERE course_id=?',(cid,),True):
                            raise ValueError()
                        db().execute('UPDATE courses SET published=? WHERE id=?',(0 if course['published'] else 1,cid))
                    else:
                        abort(400)
                flash('Conteúdo guardado.', 'success')
            except (ValueError, KeyError):
                flash('Verifique os campos. Publicar exige aulas e avaliação. Vídeos: URL incorporável YouTube/Vimeo; materiais: HTTPS.', 'error')
            return redirect(url_for('admin'))
        return render_template('admin.html',courses=query('SELECT c.*,(SELECT COUNT(*) FROM lessons WHERE course_id=c.id) lessons,(SELECT COUNT(*) FROM questions WHERE course_id=c.id) questions FROM courses c'),all_lessons=query('SELECT l.*,c.title course_title FROM lessons l JOIN courses c ON c.id=l.course_id ORDER BY course_id,position,id'),sales=query("SELECT o.*,u.name,c.title FROM orders o JOIN users u ON u.id=o.user_id JOIN courses c ON c.id=o.course_id ORDER BY o.created_at DESC LIMIT 50"),stats=query("SELECT COUNT(*) orders, COALESCE(SUM(CASE WHEN status='paid' AND source='live' THEN amount ELSE 0 END),0) revenue FROM orders",one=True))

    @app.route('/admin/aulas/<int:lid>', methods=['GET','POST'])
    @admin_required
    def edit_lesson(lid):
        lesson = query('SELECT * FROM lessons WHERE id=?',(lid,),True)
        if not lesson:
            abort(404)
        if request.method=='POST':
            title=request.form.get('title','').strip();content=request.form.get('content','').strip()
            video=request.form.get('video_url','').strip();material=request.form.get('material_url','').strip()
            try:
                position=int(request.form.get('position','1'))
                if not title or not content or position<1 or (video and not (video.startswith('https://www.youtube-nocookie.com/embed/') or video.startswith('https://player.vimeo.com/video/'))) or (material and urlparse(material).scheme!='https'):
                    raise ValueError()
                with db():
                    db().execute('UPDATE lessons SET module=?,title=?,content=?,video_url=?,material_url=?,position=? WHERE id=?',(request.form.get('module','').strip(),title,content,video,material,position,lid))
                flash('Aula atualizada.', 'success')
                return redirect(url_for('admin'))
            except ValueError:
                flash('Verifique título, conteúdo, ordem e URLs HTTPS suportadas.', 'error')
        return render_template('edit_lesson.html',lesson=lesson)

    @app.route('/saude')
    def health():
        db().execute('SELECT COUNT(*) FROM courses')
        return jsonify(status='ok',payment_mode=app.config['PAYMENT_MODE'])

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(503)
    def error(err):
        return render_template('error.html',error=err),err.code

    @app.cli.command('init-db')
    def init_db():
        db().executescript((ROOT/'schema.sql').read_text())
        click.echo('Base de dados inicializada (dados existentes preservados).')

    @app.cli.command('seed-demo')
    def seed_demo():
        if query('SELECT 1 FROM courses LIMIT 1',one=True):
            click.echo('Catálogo existente preservado.');return
        modules = ['Primeiros passos digitais','Documentos profissionais','Folhas de cálculo','Comunicação e emprego','Segurança e produtividade']
        topics = [
            ['Organizar ficheiros e pastas','Navegar e pesquisar na internet','Utilizar email profissional','Guardar documentos na nuvem','Prática: organizar o seu portefólio'],
            ['Criar um documento','Formatar texto com clareza','Construir um currículo','Exportar e partilhar PDF','Prática: rever o seu currículo'],
            ['Conhecer células e tabelas','Calcular receitas e despesas','Aplicar fórmulas básicas','Criar um gráfico','Prática: orçamento mensal'],
            ['Procurar oportunidades','Preparar candidatura por email','Participar em reunião online','Criar um perfil profissional','Prática: enviar uma candidatura'],
            ['Criar palavras-passe seguras','Reconhecer burlas digitais','Ativar autenticação adicional','Planear tarefas e prazos','Prática: plano de desenvolvimento']]
        with db():
            result=db().execute('INSERT INTO courses(title,category,description,price,published) VALUES(?,?,?,?,1)',('Competências Digitais para o Emprego','Carreira & tecnologia','Aprenda competências digitais essenciais para melhorar a sua empregabilidade. Do primeiro documento à candidatura profissional, avance ao seu ritmo.',750))
            cid=result.lastrowid
            for m,titles in enumerate(topics):
                for i,title in enumerate(titles):
                    content=f'{title}\n\nObjetivo: praticar esta competência no seu dia a dia.\n\n1. Escolha uma ferramenta que já tenha disponível no computador ou telemóvel.\n2. Explore as suas funções e crie um exemplo relacionado com {title.lower()}.\n3. Guarde o resultado numa pasta de aprendizagem e reveja se está claro e completo.\n\nExercício: explique os passos a outra pessoa e registe uma dificuldade que conseguiu resolver.\n\nConteúdo introdutório de demonstração. O administrador deve substituir este texto pela aula definitiva e adicionar o vídeo e materiais autorizados antes da venda real.'
                    db().execute('INSERT INTO lessons(course_id,module,title,content,position) VALUES(?,?,?,?,?)',(cid,modules[m],title,content,m*5+i+1))
            for prompt,options,answer in [
                ('Como proteger melhor a sua conta?',['Partilhar a palavra-passe','Usar a mesma palavra-passe sempre','Usar palavra-passe única e autenticação adicional','Publicar a palavra-passe'],2),
                ('Qual formato mantém a apresentação de um currículo?',['PDF','Pasta vazia','Ficheiro temporário','Atalho'],0),
                ('O que deve incluir numa candidatura por email?',['Apenas emojis','Assunto claro, apresentação e currículo','Palavra-passe do email','Dados bancários públicos'],1),
                ('Para que serve uma folha de cálculo?',['Organizar dados e calcular valores','Eliminar a internet','Substituir palavras-passe','Enviar SMS automaticamente'],0),
                ('Recebeu um link suspeito que pede a sua palavra-passe. O que faz?',['Introduzir a palavra-passe','Partilhar com todos','Verificar o remetente e evitar fornecer credenciais','Desativar a proteção'],2)]:
                db().execute('INSERT INTO questions(course_id,prompt,options,answer) VALUES(?,?,?,?)',(cid,prompt,json.dumps(options,ensure_ascii=False),answer))
        click.echo('Curso demonstrativo criado: 5 módulos, 25 aulas de leitura e avaliação. Vídeos devem ser adicionados pelo administrador.')

    @app.cli.command('create-admin')
    @click.option('--email',prompt=True)
    @click.option('--name',default='Administrador')
    @click.option('--password',prompt=True,hide_input=True,confirmation_prompt=True)
    def create_admin(email,name,password):
        if len(password)<10 or '@' not in email:
            raise click.ClickException('Email válido e palavra-passe com pelo menos 10 caracteres são obrigatórios.')
        try:
            with db():
                db().execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'admin')",(name,email.lower().strip(),generate_password_hash(password)))
        except sqlite3.IntegrityError:
            raise click.ClickException('Email já registado. Nenhuma conta foi alterada.')
        click.echo('Administrador criado.')
    return app

app = create_app()
