import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from app import create_app
from werkzeug.security import generate_password_hash


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=self.temp.name+'/test.sqlite3'
        self.app=create_app({'TESTING':True,'DATABASE':self.path,'SECRET_KEY':'test-key','PAYMENT_MODE':'demo'})
        self.client=self.app.test_client()
        for command in ['init-db','seed-demo']:
            result=self.app.test_cli_runner().invoke(args=[command])
            self.assertEqual(result.exit_code,0,result.output)
    def tearDown(self):
        self.temp.cleanup()
    def db(self):
        con=sqlite3.connect(self.path);con.row_factory=sqlite3.Row
        return con
    def post(self,path,data=None):
        self.client.get('/entrar')
        with self.client.session_transaction() as s:
            token=s['csrf']
        return self.client.post(path,data=dict(data or {},csrf=token))
    def register(self,email='aluno@example.test'):
        self.assertEqual(self.post('/registar',{'name':'Aluno Teste','email':email,'phone':'+258840000000','password':'Palavra-passe123'}).status_code,302)
    def buy(self):
        response=self.post('/checkout/1',{'method':'mpesa'})
        self.assertEqual(response.status_code,302)
        oid=response.location.split('/')[-1]
        self.assertEqual(self.client.get(response.location).status_code,200)
        return oid
    def finish_all(self):
        with self.db() as con:
            lids=[r['id'] for r in con.execute('SELECT id FROM lessons')]
        for lid in lids:
            self.assertEqual(self.post(f'/aulas/{lid}/concluir').status_code,302)
    def test_purchase_learning_certificate_and_isolation(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/cursos/1').status_code,200)
        self.register()
        self.assertEqual(self.client.get('/aprender/1').status_code,403)
        oid=self.buy()
        self.assertEqual(self.client.get('/aprender/1').status_code,403)
        self.assertEqual(self.post('/pedidos/'+oid+'/simular').status_code,302)
        self.assertEqual(self.post('/pedidos/'+oid+'/simular').status_code,302)
        self.assertEqual(self.client.get('/aprender/1').status_code,200)
        self.assertEqual(self.client.get('/avaliacao/1').status_code,302)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM enrollments').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],1)
        self.finish_all()
        self.assertEqual(self.client.get('/avaliacao/1').status_code,200)
        wrong=self.post('/avaliacao/1',{'q1':'0','q2':'1','q3':'0','q4':'1','q5':'0'})
        self.assertEqual(wrong.status_code,200)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM certificates').fetchone()[0],0)
            answers={'q'+str(q['id']):str(q['answer']) for q in con.execute('SELECT * FROM questions')}
        result=self.post('/avaliacao/1',answers)
        self.assertEqual(result.status_code,302)
        certificate=result.location
        self.assertIn('Certificado de conclusão',self.client.get(certificate).text)
        self.assertEqual(self.client.get('/minha-conta').status_code,200)
        self.assertEqual(self.client.get('/admin').status_code,403)
        self.post('/sair')
        self.register('outro@example.test')
        self.assertEqual(self.client.get('/pedidos/'+oid).status_code,404)
        self.assertEqual(self.client.get('/aprender/1').status_code,403)
        self.assertEqual(self.client.get(certificate).status_code,200)
    def test_csrf_and_registration(self):
        self.assertEqual(self.client.post('/registar',data={'name':'A'}).status_code,400)
        self.assertEqual(self.post('/registar',{'name':'Aluno','email':'invalid','password':'short'}).status_code,200)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM users').fetchone()[0],0)
        self.register()
        self.assertEqual(self.post('/checkout/1',{'method':'unknown'}).status_code,400)
    def test_admin_content_and_publication(self):
        with self.db() as con:
            con.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,'admin')",('Admin','admin@example.test',generate_password_hash('AdminPassword123')))
        self.post('/entrar',{'email':'admin@example.test','password':'AdminPassword123'})
        self.assertEqual(self.client.get('/admin').status_code,200)
        self.post('/admin',{'action':'course','title':'Curso novo','category':'Negócio','description':'Teste','price':'900'})
        self.post('/admin',{'action':'publish','course_id':'2'})
        self.assertEqual(self.client.get('/cursos/2').status_code,404)
        self.post('/admin',{'action':'lesson','course_id':'2','module':'Módulo 1','title':'Introdução','content':'Conteúdo real','position':'1','video_url':'https://www.youtube-nocookie.com/embed/example','material_url':'https://example.test/material.pdf'})
        self.post('/admin',{'action':'question','course_id':'2','prompt':'Questão?','option0':'A','option1':'B','option2':'C','option3':'D','answer':'0'})
        self.post('/admin',{'action':'publish','course_id':'2'})
        self.assertEqual(self.client.get('/cursos/2').status_code,200)
        self.assertEqual(self.client.get('/admin/aulas/1').status_code,200)
        self.post('/admin/aulas/1',{'module':'Atualizado','title':'Nova aula','content':'Texto atualizado','position':'1','video_url':'','material_url':''})
        with self.db() as con:
            self.assertEqual(con.execute('SELECT title FROM lessons WHERE id=1').fetchone()[0],'Nova aula')
        result=self.app.test_cli_runner().invoke(args=['seed-demo'])
        self.assertEqual(result.exit_code,0)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM courses').fetchone()[0],2)
    def test_live_checkout_without_gateway_never_grants_access(self):
        self.app.config['PAYMENT_MODE']='live'
        self.register()
        with patch.dict(os.environ,{'PAYMENT_ADAPTER_URL':'','PAYMENT_ADAPTER_TOKEN':''}):
            oid=self.buy()
        self.assertEqual(self.post('/pedidos/'+oid+'/simular').status_code,404)
        self.assertEqual(self.client.get('/aprender/1').status_code,403)
        self.assertEqual(self.client.post('/api/payments/webhook',json={}).status_code,503)
    def test_signed_webhook_verifies_amount_reference_and_replays(self):
        self.app.config['PAYMENT_MODE']='live'
        self.register()
        with patch.dict(os.environ,{'PAYMENT_ADAPTER_URL':'','PAYMENT_ADAPTER_TOKEN':''}):
            oid=self.buy()
        with self.db() as con:
            con.execute('UPDATE orders SET provider_ref=? WHERE id=?',('gateway-123',oid))
        def deliver(payload,secret='webhook-test',stamp=None):
            stamp=stamp or str(int(time.time()))
            raw=json.dumps(payload).encode()
            signature=hmac.new(secret.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
            return self.client.post('/api/payments/webhook',data=raw,content_type='application/json',headers={'X-Payment-Timestamp':stamp,'X-Payment-Signature':signature})
        body={'event_id':'evt-1','order_id':oid,'reference':'gateway-123','amount':750,'currency':'MZN','status':'paid'}
        with patch.dict(os.environ,{'PAYMENT_WEBHOOK_SECRET':'webhook-test'}):
            self.assertEqual(deliver(body,secret='wrong').status_code,401)
            self.assertEqual(deliver(body,stamp='1').status_code,401)
            self.assertEqual(deliver(dict(body,amount=1)).status_code,400)
            self.assertEqual(deliver(dict(body,reference='other')).status_code,400)
            self.assertEqual(self.client.get('/aprender/1').status_code,403)
            self.assertEqual(deliver(body).status_code,200)
            self.assertEqual(deliver(body).status_code,200)
            self.assertEqual(deliver(dict(body,event_id='evt-2')).status_code,200)
        self.assertEqual(self.client.get('/aprender/1').status_code,200)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM enrollments').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM notifications').fetchone()[0],1)
    def test_authentication_and_html_escaping(self):
        self.register()
        self.post('/sair')
        self.assertEqual(self.post('/entrar',{'email':'aluno@example.test','password':'wrong'}).status_code,200)
        self.assertEqual(self.client.get('/minha-conta').status_code,302)
        self.assertEqual(self.post('/entrar',{'email':'aluno@example.test','password':'Palavra-passe123'}).status_code,302)
        self.assertEqual(self.client.get('/minha-conta').status_code,200)
        with self.db() as con:
            con.execute('UPDATE users SET name=?',('<script>alert(1)</script>',))
        self.assertNotIn('<script>alert(1)</script>',self.client.get('/minha-conta').text)

if __name__=='__main__':
    unittest.main()
