import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
spec=importlib.util.spec_from_file_location('server',Path(__file__).with_name('server.py'))
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
server.FRONTEND=Path(__file__).resolve().parents[1]/"tests"/"fixtures"
class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();server.DB=Path(cls.tmp.name)/'test.sqlite3'
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        cls.url=f'http://127.0.0.1:{cls.http.server_port}'
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.tmp.cleanup()
    def request(self,path,body=None,revision=None,origin=None):
        headers={'Content-Type':'application/json'}
        if revision is not None:headers['If-Match']=str(revision)
        if origin:headers['Origin']=origin
        req=Request(self.url+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method='GET' if body is None else 'PUT')
        try:
            with urlopen(req) as r:return r.status,json.load(r)
        except HTTPError as e:
            with e:return e.code,json.load(e)
    def test_lan_authentication(self):
        from unittest.mock import patch
        import base64
        with patch.dict(server.os.environ,{'ORION_LAN_PASSWORD':'test-network-password'}):
            for path in ['/api/workspace','/billing-pdf.js']:
                with self.assertRaises(HTTPError) as failure: urlopen(self.url+path)
                self.assertEqual(failure.exception.code,401)
                token=base64.b64encode(b'orion:test-network-password').decode()
                with urlopen(Request(self.url+path,headers={'Authorization':'Basic '+token})) as response:
                    self.assertEqual(response.status,200)
            with self.assertRaises(HTTPError) as failure:
                urlopen(Request(self.url+'/api/workspace',data=b'{}',method='PUT',headers={'Content-Type':'application/json'}))
            self.assertEqual(failure.exception.code,401)

    def test_persistence_and_conflict(self):
        status,p=self.request('/api/workspace');self.assertEqual(status,200)
        p['data']['company']['fantasyName']='Empresa teste'
        status,r=self.request('/api/workspace',p['data'],p['revision']);self.assertEqual(status,200)
        self.assertEqual(self.request('/api/workspace')[1]['data']['company']['fantasyName'],'Empresa teste')
        self.assertEqual(self.request('/api/workspace',p['data'],p['revision'])[0],409)
    def test_bad_receipt(self):
        _,p=self.request('/api/workspace');p['data']['proposals'][0]['receivedValue']=999999999
        self.assertEqual(self.request('/api/workspace',p['data'],p['revision'])[0],400)
    def test_origin_and_routes(self):
        _,p=self.request('/api/workspace')
        self.assertEqual(self.request('/api/workspace',p['data'],p['revision'],'https://foreign.example')[0],403)
        self.assertEqual(self.request('/api/unknown')[0],404)
    def test_valid_seed(self):
        server.validate(json.loads((server.FRONTEND/'seed.json').read_text(encoding='utf-8')))

    def test_pdf_generator_is_served(self):
        with urlopen(self.url+'/billing-pdf.js') as response:
            self.assertEqual(response.status,200)
            self.assertIn(b'OrionBillingPDF',response.read())

class OperationalRules(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((server.FRONTEND/'seed.json').read_text(encoding='utf-8'))
    def reject(self,mutate,transition=False):
        import copy
        new=copy.deepcopy(self.data);mutate(new)
        with self.assertRaises((ValueError,TypeError,KeyError)):
            server.validate(new)
            if transition:server.validate_transition(self.data,new)
    def test_client_duplicate(self):
        self.reject(lambda d:d['clients'].append({**d['clients'][0],'code':'duplicate'}))
    def test_client_reference(self):
        self.reject(lambda d:d['proposals'][0].update(clientCode='missing'))
    def test_unapproved_order(self):
        self.reject(lambda d:d['proposals'][3].update(projectCode='OS-invalid'))
    def test_duplicate_order(self):
        self.reject(lambda d:d['proposals'][1].update(projectCode=d['proposals'][0]['projectCode']))
    def test_order_dates(self):
        self.reject(lambda d:d['proposals'][0].update(deliveryDate='2020-01-01'))
    def test_locked_terms(self):
        self.reject(lambda d:d['proposals'][0].update(netValue=8000),True)
    def test_kanban_allows_stage_changes_and_return(self):
        import copy
        changed=copy.deepcopy(self.data);changed['proposals'][0]['erpStage']='Concluído'
        server.validate(changed);server.validate_transition(self.data,changed)
        returned=copy.deepcopy(changed);returned['proposals'][0]['erpStage']='Em produção'
        server.validate(returned);server.validate_transition(changed,returned)
    def test_kanban_rejects_unknown_stage(self):
        self.reject(lambda d:d['proposals'][0].update(erpStage='Etapa inexistente'),True)
    def test_receipt_requires_ledger(self):
        self.reject(lambda d:d['proposals'][0].update(receivedValue=200),True)
    def test_no_receipt_reversal(self):
        self.reject(lambda d:d['proposals'][0].update(receivedValue=0),True)
    def test_complete_flow(self):
        import copy
        old=copy.deepcopy(self.data)
        self.data['clients'].append(dict(code=105,name='Cliente de teste',legalName='Empresa teste',cnpj='11.222.333/0001-81',contact='Contato teste',address='Local teste'))
        server.validate(self.data);server.validate_transition(old,self.data)
        old=copy.deepcopy(self.data)
        p=dict(code='ORC-20261115-19300100',clientCode=105,client='Cliente de teste',campaign='Projeto teste',netValue=1000,receivedValue=0,status='Em negociação',responsible='Equipe')
        self.data['proposals'].append(p);server.validate(self.data);server.validate_transition(old,self.data)
        old=copy.deepcopy(self.data);p.update(status='Aprovado');server.validate(self.data);server.validate_transition(old,self.data)
        old=copy.deepcopy(self.data);p.update(projectCode='PJT-20261115-19300100',erpStage='Planejamento',captureDate='2026-11-20',deliveryDate='2026-11-25',scopeItems='Escopo teste',location='Local teste');server.validate(self.data);server.validate_transition(old,self.data)
        old=copy.deepcopy(self.data);p.update(receivedValue=250,receipts=[dict(id='R-TEST',amount=250,date='2026-11-15',noAttachment=True)]);server.validate(self.data);server.validate_transition(old,self.data)
        for stage in ['Em produção','Em revisão','Concluído']:
            old=copy.deepcopy(self.data);p['erpStage']=stage;server.validate(self.data);server.validate_transition(old,self.data)

class NewRules(unittest.TestCase):
    setUp=OperationalRules.setUp
    reject=OperationalRules.reject
    def test_new_client_sequence(self):
        self.reject(lambda d:d['clients'].append(dict(code=999,name='Teste',legalName='Teste',cnpj='11.222.333/0001-81',contact='Teste',address='Teste')),True)
    def test_invalid_budget_protocol(self):
        self.reject(lambda d:d['proposals'].append(dict(code='uuid',clientCode=100,client='Teste',campaign='Teste',netValue=100,receivedValue=0,status='Enviado',responsible='Equipe')),True)
    def test_proof_required(self):
        self.reject(lambda d:d['proposals'][0].update(receivedValue=200,receipts=[dict(id='new',amount=100,date='2026-11-15')]),True)
    def test_explicit_proof_waiver(self):
        import copy
        new=copy.deepcopy(self.data);new['proposals'][0].update(receivedValue=200,receipts=[dict(id='new',amount=100,date='2026-11-15',noAttachment=True)])
        server.validate_transition(self.data,new)
    def test_attachment_signature(self):
        with self.assertRaises(ValueError):server.validate_attachment(dict(name='fake.pdf',type='application/pdf',content='aGVsbG8='))
    def test_attachment_pdf(self):
        import base64
        server.validate_attachment(dict(name='test.pdf',type='application/pdf',content=base64.b64encode(b'%PDF-1.4\ntest').decode()))
    def test_billing_protocol_and_limits(self):
        invoice=dict(code='20261115-193010',amount=7100,dueDate='2026-11-20',supplierName='Empresa',supplierDocument='teste',contractReference='contrato')
        self.reject(lambda d:d['proposals'][0].update(invoices=[invoice]),True)
    def test_receipt_allocation_limit(self):
        invoice=dict(code='20261115-193010',amount=50,dueDate='2026-11-20',supplierName='Empresa',supplierDocument='teste',contractReference='contrato')
        self.data['proposals'][0]['invoices']=[invoice]
        self.reject(lambda d:d['proposals'][0].update(receivedValue=200,receipts=[dict(id='new',amount=100,date='2026-11-15',invoiceCode=invoice['code'],noAttachment=True)]),True)

    def test_earlier_receipt_can_be_allocated_without_double_counting(self):
        import copy
        new=copy.deepcopy(self.data)
        new['proposals'][0]['invoices']=[dict(code='20261115-193010',amount=7000,openingCredit=100,dueDate='2026-11-20',supplierName='Empresa',supplierDocument='teste',contractReference='contrato')]
        server.validate_transition(self.data,new)
        new['proposals'][0]['invoices'][0]['openingCredit']=200
        with self.assertRaises(ValueError):server.validate_transition(self.data,new)

    def test_budget_project_use_same_numeric_part(self):
        self.reject(lambda d:d['proposals'][0].update(projectCode='PJT-20261115-20395500'),True)
    def test_protocol_format(self):
        self.assertTrue(server.valid_entity_protocol('ORC-20261115-20395500','ORC'))
        self.assertTrue(server.valid_entity_protocol('PJT-20261115-20395500','PJT'))
        self.assertFalse(server.valid_entity_protocol('ORC-20261315-20395500','ORC'))
        self.assertFalse(server.valid_entity_protocol('ORC-20261115-25395500','ORC'))
    def test_migration_preserves_links_and_is_idempotent(self):
        import copy
        old=copy.deepcopy(self.data);p=old['proposals'][0]
        previous_budget=p['code'];p['code']='20261114-091500';p['projectCode']='20261114-092000'
        old['events'][0]['proposalCode']=p['code'];old['events'][0]['projectCode']=p['projectCode']
        p['invoices']=[dict(code='20261115-203010',contractReference=p['code'])]
        migrated=server.migrate_legacy(old);p=migrated['proposals'][0]
        self.assertEqual(p['code'],'ORC-20261114-09150000')
        self.assertEqual(p['projectCode'],'PJT-20261114-09150000')
        self.assertEqual(migrated['events'][0]['proposalCode'],p['code'])
        self.assertEqual(migrated['events'][0]['projectCode'],p['projectCode'])
        self.assertEqual(p['invoices'][0]['contractReference'],p['code'])
        self.assertEqual(server.migrate_legacy(migrated),migrated)

class UndoAPI(unittest.TestCase):
    setUpClass=classmethod(API.setUpClass.__func__)
    tearDownClass=classmethod(API.tearDownClass.__func__)
    request=API.request
    def test_undo_is_persisted_and_checks_revision(self):
        _,before=self.request('/api/workspace')
        original=before['data']['company']['fantasyName']
        before['data']['company']['fantasyName']='Mudança reversível'
        status,saved=self.request('/api/workspace',before['data'],before['revision']);self.assertEqual(status,200)
        self.assertEqual(self.request('/api/undo',{},before['revision'])[0],409)
        status,undone=self.request('/api/undo',{},saved['revision']);self.assertEqual(status,200)
        self.assertEqual(undone['data']['company']['fantasyName'],original)
        self.assertEqual(self.request('/api/workspace')[1]['data']['company']['fantasyName'],original)
        self.assertEqual(self.request('/api/undo',{},saved['revision'])[0],409)
    def test_receipt_proof_roundtrip(self):
        import base64
        _,before=self.request('/api/workspace')
        p=before['data']['proposals'][0]
        proof=dict(name='comprovante-teste.pdf',type='application/pdf',content=base64.b64encode(b'%PDF-1.4\nproof').decode())
        old_received=p['receivedValue']
        p['receivedValue']+=10
        p.setdefault('receipts',[]).append(dict(id='receipt-proof-test',amount=10,date='2026-11-15',attachment=proof,noAttachment=False))
        status,saved=self.request('/api/workspace',before['data'],before['revision']);self.assertEqual(status,200)
        _,fresh=self.request('/api/workspace');self.assertEqual(fresh['data']['proposals'][0]['receipts'][-1]['attachment'],proof)
        status,undone=self.request('/api/undo',{},saved['revision']);self.assertEqual(status,200)
        self.assertEqual(undone['data']['proposals'][0]['receivedValue'],old_received)
    def test_invoice_issue_and_allocation_roundtrip(self):
        _,before=self.request('/api/workspace');p=before['data']['proposals'][0]
        invoice=dict(code='20261115-203000',amount=7000,openingCredit=100,dueDate='2026-11-20',supplierName='Empresa',supplierDocument='00.000.000/0001-00',contractReference=p['code'])
        p['invoices']=[invoice]
        status,saved=self.request('/api/workspace',before['data'],before['revision']);self.assertEqual(status,200)
        _,fresh=self.request('/api/workspace');self.assertEqual(fresh['data']['proposals'][0]['invoices'][0],invoice)
        self.assertEqual(self.request('/api/undo',{},saved['revision'])[0],200)
