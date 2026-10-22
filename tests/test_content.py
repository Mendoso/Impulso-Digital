import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from content_authoring import build, student_text
from content_import import PACKAGE, load_package, review_digest
from werkzeug.security import generate_password_hash


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'db.sqlite3')
        self.app=create_app({'TESTING':True,'DATABASE':self.path,'SECRET_KEY':'test'})
        self.cli=self.app.test_cli_runner()
        self.assertEqual(self.cli.invoke(args=['seed-demo']).exit_code,0)

    def tearDown(self):
        self.temp.cleanup()

    def run_import(self):
        result=self.cli.invoke(args=['import-content','--apply','--backup',str(Path(self.temp.name)/'backup.sqlite3')])
        self.assertEqual(result.exit_code,0,result.output)

    def test_editorial_integrity_and_reproducibility(self):
        package,_=load_package(PACKAGE)
        self.assertEqual(package,build())
        self.assertEqual(len(set(l['theory'] for l in package['lessons'])),25)
        self.assertEqual(sum(l['duration_minutes'] for l in package['lessons']),1200)
        for lesson in package['lessons']:
            self.assertGreater(len(lesson['theory'].split()),70)
            self.assertNotIn('Guia do formador',student_text(lesson))
            self.assertEqual(lesson['assets']['video_url'],'')
        for mutate in [lambda p:p['lessons'].pop(),lambda p:p['lessons'][0].update(position=2),lambda p:p['lessons'][0]['assets'].update(video_url='https://example.test'),lambda p:p['lessons'][0].update(theory=''),lambda p:p['lessons'][0].update(rubric=[])]:
            bad=copy.deepcopy(package);mutate(bad)
            path=Path(self.temp.name)/'bad.json';path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):load_package(path)

    def test_dry_run_backup_preservation_and_idempotence(self):
        with sqlite3.connect(self.path) as con:
            con.execute("UPDATE lessons SET content='Conteúdo personalizado' WHERE id=1")
            before=list(con.execute('SELECT * FROM lessons'))
        result=self.cli.invoke(args=['import-content'])
        self.assertEqual(result.exit_code,0,result.output)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(list(con.execute('SELECT * FROM lessons')),before)
        self.assertNotEqual(self.cli.invoke(args=['import-content','--apply']).exit_code,0)
        self.run_import()
        with sqlite3.connect(self.path) as con:
            self.assertEqual(list(con.execute('SELECT * FROM lessons WHERE course_id=1')),before)
            self.assertEqual(con.execute('SELECT published FROM courses WHERE id=2').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM lessons WHERE course_id=2').fetchone()[0],25)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM questions WHERE course_id=2').fetchone()[0],10)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM enrollments').fetchone()[0],0)
        with sqlite3.connect(str(Path(self.temp.name)/'backup.sqlite3')) as backup:
            self.assertEqual(list(backup.execute('SELECT * FROM lessons')),before)
        result=self.cli.invoke(args=['import-content','--apply','--backup',str(Path(self.temp.name)/'second.sqlite3')])
        self.assertEqual(result.exit_code,0,result.output)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM courses').fetchone()[0],2)
        # Backup path cannot overwrite the operational DB or an existing backup.
        self.assertNotEqual(self.cli.invoke(args=['import-content','--apply','--backup',self.path]).exit_code,0)

    def test_review_gate_approval_and_edit_revocation(self):
        self.run_import()
        client=self.app.test_client()
        with sqlite3.connect(self.path) as con:
            uid=con.execute("INSERT INTO users(name,email,password_hash,role) VALUES('Admin','admin@example.test',?,'admin')",(generate_password_hash('test-password'),)).lastrowid
        with client.session_transaction() as session:
            session['uid']=uid;session['csrf']='token'
        def post(url,data):return client.post(url,data=dict(data,csrf='token'))
        self.assertEqual(client.get('/cursos/2').status_code,404)
        post('/admin',dict(action='publish',course_id=2))
        self.assertEqual(client.get('/cursos/2').status_code,404)
        with sqlite3.connect(self.path) as con:digest=review_digest(con,2)
        args=['approve-content','--course-id','2','--sha256',digest,'--reviewer','Revisor de teste']
        self.assertNotEqual(self.cli.invoke(args=args).exit_code,0)
        self.assertEqual(self.cli.invoke(args=args+['--confirm-reviewed']).exit_code,0)
        self.assertEqual(client.get('/cursos/2').status_code,404) # approval does not publish
        post('/admin',dict(action='publish',course_id=2))
        self.assertEqual(client.get('/cursos/2').status_code,200)
        with sqlite3.connect(self.path) as con:lid=con.execute('SELECT id FROM lessons WHERE course_id=2 LIMIT 1').fetchone()[0]
        post(f'/admin/aulas/{lid}',dict(title='Revisão',module='Teste',content='Texto revisto',position=1))
        self.assertEqual(client.get('/cursos/2').status_code,404)
        self.assertNotEqual(self.cli.invoke(args=args+['--confirm-reviewed']).exit_code,0)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute('SELECT review_status FROM content_imports').fetchone()[0],'pending')

    def test_import_rollback_and_changed_package_refused(self):
        from content_import import import_draft
        payload,digest=load_package(PACKAGE)
        with sqlite3.connect(self.path) as con:
            con.execute("CREATE TRIGGER reject_lesson BEFORE INSERT ON lessons WHEN NEW.course_id=2 BEGIN SELECT RAISE(ABORT,'test failure'); END")
        result=self.cli.invoke(args=['import-content','--apply','--backup',str(Path(self.temp.name)/'failed.sqlite3')])
        self.assertNotEqual(result.exit_code,0)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM courses').fetchone()[0],1)
            con.execute('DROP TRIGGER reject_lesson')
        self.run_import()
        with sqlite3.connect(self.path) as con:
            with self.assertRaises(ValueError):import_draft(con,payload,'changed-digest')

