"""API demonstrativa OrionHUB, Python 3.10+, sem dependências externas."""
from contextlib import closing
import base64
import binascii
import hmac
import json
import math
import os
import re
from datetime import date, datetime
import sqlite3
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from threading import Lock

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / 'frontend'
DB = Path(os.environ.get('ORION_DB', str(ROOT / 'backend' / 'orionhub.sqlite3')))
LOCK = Lock()
MAX_BODY = 16_000_000

def migrate_legacy(data):
    import copy
    result=copy.deepcopy(data)
    next_code=max([99]+[c['code'] for c in result['clients'] if type(c.get('code')) is int])+1
    client_map={}
    for c in result['clients']:
        if type(c.get('code')) is not int or c['code']<100:
            client_map[str(c['code'])]=next_code
            c['legacyCode']=c['code'];c['code']=next_code;next_code+=1
    budget_map={};project_map={};used=set()
    for p in result['proposals']:
        p['clientCode']=client_map.get(str(p.get('clientCode')),p.get('clientCode'))
        old_budget=p['code'];old_project=p.get('projectCode')
        match=re.fullmatch(r'(?:ORC-)?(\d{8}-\d{6})(\d{2})?',str(old_budget))
        if match:
            base=match[1]+(match[2] or '00')
        else:
            try: moment=datetime.fromisoformat(p.get('issuedAt') or p.get('captureDate'))
            except (ValueError,TypeError): moment=datetime.now()
            base=moment.strftime('%Y%m%d-%H%M%S')+'00'
        if base in used:
            from datetime import timedelta
            moment=datetime.strptime(base[:15],'%Y%m%d-%H%M%S')
            counter=int(base[-2:])
            while base in used:
                counter+=1
                if counter>=100: moment+=timedelta(seconds=1);counter=0
                base=moment.strftime('%Y%m%d-%H%M%S')+f'{counter:02}'
        used.add(base)
        budget='ORC-'+base
        if budget!=old_budget:p.setdefault('legacyBudgetCode',old_budget)
        p['code']=budget;budget_map[old_budget]=budget
        if old_project:
            project='PJT-'+base
            if project!=old_project:p.setdefault('legacyProjectCode',old_project)
            p['projectCode']=project;project_map[old_project]=project
        for invoice in p.get('invoices',[]):
            if invoice.get('contractReference')==old_budget:invoice['contractReference']=budget
    for e in result['events']:
        if e.get('proposalCode') in budget_map:e['proposalCode']=budget_map[e['proposalCode']]
        if e.get('projectCode') in project_map:e['projectCode']=project_map[e['projectCode']]
    return result

def connect():
    con = sqlite3.connect(DB)
    con.execute('CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL)')
    con.execute('INSERT OR IGNORE INTO workspace VALUES (1, 1, ?)', ((FRONTEND / 'seed.json').read_text(encoding='utf-8'),))
    con.execute('CREATE TABLE IF NOT EXISTS undo_log (revision INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
    revision,payload=con.execute('SELECT revision,payload FROM workspace WHERE id=1').fetchone()
    migrated=migrate_legacy(json.loads(payload))
    if json.loads(payload)!=migrated:
        con.execute('UPDATE workspace SET revision=?,payload=? WHERE id=1',(revision+1,json.dumps(migrated,ensure_ascii=False)))
        con.execute('DELETE FROM undo_log')
    con.commit()
    return con

def validate(data):
    if not isinstance(data, dict) or set(data) != {'company', 'clients', 'proposals', 'events'}:
        raise ValueError('Estrutura de workspace inválida.')
    if not isinstance(data['company'], dict):
        raise ValueError('Empresa inválida.')
    templates=data['company'].get('contractTemplates',[])
    if not isinstance(templates,list) or len(templates)>200:
        raise ValueError('Lista de modelos inválida.')
    template_ids=set()
    for template in templates:
        if not isinstance(template,dict) or not isinstance(template.get('id'),str) or not template['id'] or template['id'] in template_ids:
            raise ValueError('Identificação de modelo inválida ou repetida.')
        template_ids.add(template['id'])
        if not isinstance(template.get('name'),str) or not template['name'].strip() or len(template['name'])>120 or not isinstance(template.get('body'),str) or not template['body'].strip() or len(template['body'])>60000 or type(template.get('version')) is not int or template['version']<1:
            raise ValueError('Conteúdo ou versão do modelo inválidos.')
    for key in ['clients', 'proposals', 'events']:
        if not isinstance(data[key], list) or len(data[key]) > 10000 or any(not isinstance(x, dict) for x in data[key]):
            raise ValueError('Coleção inválida.')
    for key, identity in [('clients','code'),('proposals','code'),('events','id')]:
        ids = [str(x.get(identity, '')) for x in data[key]]
        if any(not x for x in ids) or len(ids) != len(set(ids)):
            raise ValueError('Identificadores ausentes ou duplicados.')
    for p in data['proposals']:
        if not valid_entity_protocol(p.get('code'),'ORC'):raise ValueError('Protocolo de orçamento inválido.')
        if p.get('projectCode') and p['projectCode']!='PJT-'+p['code'][4:]:raise ValueError('Projeto e orçamento devem compartilhar a mesma numeração.')
        for key in ['netValue','receivedValue']:
            n = p.get(key)
            if isinstance(n, bool) or not isinstance(n, (float,int)) or not math.isfinite(n) or n < 0 or n > 999999999:
                raise ValueError('Valor monetário inválido.')
        if p['receivedValue'] > p['netValue']:
            raise ValueError('Recebimento superior ao valor contratado.')
        if not isinstance(p.get('campaign'),str) or not p['campaign'].strip():
            raise ValueError('Projeto é obrigatório.')
    clients = {str(c['code']): c for c in data['clients']}
    documents = []
    for c in data['clients']:
        for k in ['name', 'legalName', 'contact', 'address']:
            if not isinstance(c.get(k), str) or not c[k].strip():
                raise ValueError('Preencha os dados do cliente.')
        document = re.sub(r'\D', '', str(c.get('cnpj', '')))
        if not document:
            raise ValueError('Documento do cliente ausente.')
        documents.append(document)
    if len(documents) != len(set(documents)):
        raise ValueError('CNPJ já cadastrado.')
    orders = []
    for p in data['proposals']:
        if str(p.get('clientCode')) not in clients:
            raise ValueError('Cliente da proposta não encontrado.')
        if p.get('status') not in ['Em negociação','Enviado','Aprovado','Recusado','Cancelado']:
            raise ValueError('Situação comercial inválida.')
        if p['netValue'] <= 0:
            raise ValueError('Valor da proposta deve ser positivo.')
        if p['receivedValue'] and p['status'] != 'Aprovado':
            raise ValueError('Recebimentos exigem aprovação.')
        if not isinstance(p.get('responsible'),str) or not p['responsible'].strip():
            raise ValueError('Informe um responsável.')
        if p.get('projectCode'):
            if p['status'] != 'Aprovado':
                raise ValueError('Ordem exige proposta aprovada.')
            orders.append(str(p['projectCode']))
            for k in ['scopeItems','location','deliveryDate','captureDate']:
                if not isinstance(p.get(k),str) or not p[k].strip():
                    raise ValueError('Complete escopo, local e datas da ordem.')
            begin, end = date.fromisoformat(p['captureDate']), date.fromisoformat(p['deliveryDate'])
            if end < begin:
                raise ValueError('Entrega anterior ao início.')
            if p.get('erpStage','Planejamento') not in ['Planejamento','Em produção','Em revisão','Concluído']:
                raise ValueError('Etapa de produção inválida.')
    if len(orders) != len(set(orders)):
        raise ValueError('Identificador de ordem duplicado.')
    for e in data['events']:
        date.fromisoformat(e.get('date',''))
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',e.get('time','')) or not str(e.get('title','')).strip():
            raise ValueError('Compromisso inválido.')
    def walk(value):
        if isinstance(value,str) and len(value)>3_000_000:
            raise ValueError('Texto muito longo.')
        if isinstance(value,dict):
            for v in value.values(): walk(v)
        if isinstance(value,list):
            for v in value: walk(v)
    walk(data)

def valid_cnpj(value):
    s = re.sub(r'\D','',str(value))
    if len(s)!=14 or len(set(s))==1: return False
    def digit(n):
        weights = [6,5,4,3,2,9,8,7,6,5,4,3,2] if n==13 else [5,4,3,2,9,8,7,6,5,4,3,2]
        rem = sum(int(x)*w for x,w in zip(s[:n],weights)) % 11
        return 0 if rem<2 else 11-rem
    return digit(12)==int(s[12]) and digit(13)==int(s[13])

def valid_entity_protocol(value, prefix):
    if not isinstance(value,str) or not re.fullmatch(prefix+r'-\d{8}-\d{8}',value):return False
    try:datetime.strptime(value[4:19],'%Y%m%d-%H%M%S');return True
    except ValueError:return False

def valid_protocol(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{8}-\d{6}',value): return False
    try: datetime.strptime(value,'%Y%m%d-%H%M%S'); return True
    except ValueError: return False

def validate_attachment(a):
    if not isinstance(a,dict) or not str(a.get('name','')).strip() or a.get('type') not in ['application/pdf','image/png','image/jpeg']:
        raise ValueError('Anexe PDF, PNG ou JPG.')
    try: body=base64.b64decode(a.get('content',''),validate=True)
    except (ValueError,TypeError,binascii.Error): raise ValueError('Conteúdo do anexo inválido.')
    if not 0<len(body)<=2_000_000: raise ValueError('Cada arquivo deve ter no máximo 2 MB.')
    magic={'application/pdf':body.startswith(b'%PDF-'),'image/png':body.startswith(b'\x89PNG\r\n\x1a\n'),'image/jpeg':body.startswith(b'\xff\xd8\xff')}
    if not magic[a['type']]: raise ValueError('O conteúdo do arquivo não corresponde ao tipo informado.')

def validate_financial(p):
    invoices=p.get('invoices',[])
    if invoices and p.get('status')!='Aprovado': raise ValueError('Faturamento exige orçamento aprovado.')
    if not isinstance(invoices,list) or len(invoices)>120: raise ValueError('Parcelas inválidas.')
    codes=set()
    for i in invoices:
        if not isinstance(i,dict) or not valid_protocol(i.get('code')) or i['code'] in codes: raise ValueError('Protocolo de faturamento inválido ou duplicado.')
        codes.add(i['code'])
        amount=i.get('amount')
        if isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<=0: raise ValueError('Valor da parcela inválido.')
        credit=i.get('openingCredit',0)
        if isinstance(credit,bool) or not isinstance(credit,(int,float)) or not math.isfinite(credit) or not 0<=credit<=amount: raise ValueError('Crédito anterior inválido.')
        date.fromisoformat(i.get('dueDate',''))
        for k in ['supplierName','supplierDocument','contractReference']:
            if not isinstance(i.get(k),str) or not i[k].strip(): raise ValueError('Complete fornecedor e referência contratual.')
    if round(sum(i['amount'] for i in invoices)*100)>round(p['netValue']*100): raise ValueError('Faturamento superior ao contratado.')
    documents=p.get('documents',[])
    if not isinstance(documents,list) or len(documents)>20: raise ValueError('Limite de documentos atingido.')
    for a in documents: validate_attachment(a)

def validate_transition(old, new):
    protocols=[]
    for p in new['proposals']:
        validate_financial(p)
        protocols.extend([p['code']]+([p['projectCode']] if p.get('projectCode') else [])+[i['code'] for i in p.get('invoices',[])])
    if len(protocols)!=len(set(protocols)): raise ValueError('Protocolo já utilizado no workspace.')
    old_clients = {str(c['code']):c for c in old['clients']}
    next_client=max([99]+[c['code'] for c in old['clients'] if type(c['code']) is int])+1
    new_clients=[c for c in new['clients'] if str(c['code']) not in old_clients]
    if [c['code'] for c in new_clients]!=list(range(next_client,next_client+len(new_clients))):
        raise ValueError('Código de cliente deve seguir a sequência a partir de 100.')
    for c in new['clients']:
        previous=old_clients.get(str(c['code']))
        if (previous is None or c.get('cnpj') != previous.get('cnpj')) and not valid_cnpj(c.get('cnpj')):
            raise ValueError('CNPJ inválido.')
    proposals={str(p['code']):p for p in new['proposals']}
    stages=['Planejamento','Em produção','Em revisão','Concluído']
    for previous in old['proposals']:
        p=proposals.get(str(previous['code']))
        if p is None:
            raise ValueError('Exclusão de propostas não permitida neste fluxo.')
        if previous.get('projectCode') or previous.get('receivedValue',0)>0:
            for k in ['clientCode','netValue','status']:
                if p.get(k)!=previous.get(k): raise ValueError('Termos protegidos por ordem ou recebimento.')
        if previous.get('projectCode'):
            if p.get('projectCode')!=previous['projectCode']: raise ValueError('Ordem emitida não pode ser removida ou substituída.')
            a,b=stages.index(previous.get('erpStage','Planejamento')),stages.index(p.get('erpStage','Planejamento'))
            # The board allows moving an issued project to any valid stage.
        if not previous.get('projectCode') and p.get('projectCode') and not valid_entity_protocol(p['projectCode'],'PJT'):
            raise ValueError('Protocolo de projeto deve usar PJT e a numeração do orçamento.')
        if not previous.get('projectCode') and p.get('projectCode') and p.get('erpStage')!='Planejamento':
            raise ValueError('Uma ordem deve iniciar em planejamento.')
        before=previous.get('receivedValue',0)
        after=p.get('receivedValue',0)
        if after < before: raise ValueError('Estorno exige fluxo específico.')
        old_receipts=previous.get('receipts',[])
        receipts=p.get('receipts',[])
        if receipts[:len(old_receipts)]!=old_receipts: raise ValueError('Histórico de recebimento protegido.')
        added=receipts[len(old_receipts):]
        if any(not isinstance(r,dict) or isinstance(r.get('amount'),bool) or not isinstance(r.get('amount'),(int,float)) or not math.isfinite(r['amount']) or r['amount']<=0 or not r.get('id') or not r.get('date') for r in added):
            raise ValueError('Recebimento inválido.')
        for r in added:
            paid_date=date.fromisoformat(r['date'])
            if paid_date>date.today(): raise ValueError('Pagamento não pode ter data futura.')
            if r.get('noAttachment') is not True and not r.get('attachment'):
                raise ValueError('Anexe o comprovante ou marque Não inserir anexos.')
            if r.get('noAttachment') is True and r.get('attachment'):
                raise ValueError('Escolha comprovante ou dispensa de anexo.')
            if r.get('attachment'): validate_attachment(r['attachment'])
            if p.get('invoices') and r.get('invoiceCode') not in {i['code'] for i in p['invoices']}:
                raise ValueError('Selecione a parcela do recebimento.')
        old_invoices=previous.get('invoices',[])
        if p.get('invoices',[])[:len(old_invoices)]!=old_invoices:
            raise ValueError('Parcelas já emitidas são imutáveis.')
        unallocated=previous.get('receivedValue',0)-sum(r['amount'] for r in old_receipts if r.get('invoiceCode'))-sum(i.get('openingCredit',0) for i in old_invoices)
        new_credits=sum(i.get('openingCredit',0) for i in p.get('invoices',[])[len(old_invoices):])
        if round(new_credits*100)>round(max(0,unallocated)*100): raise ValueError('Crédito anterior supera os recebimentos disponíveis.')
        for invoice in p.get('invoices',[]):
            allocated=invoice.get('openingCredit',0)+sum(r['amount'] for r in receipts if r.get('invoiceCode')==invoice['code'])
            if round(allocated*100)>round(invoice['amount']*100): raise ValueError('Baixa superior ao saldo da parcela.')
        if len({r['id'] for r in receipts})!=len(receipts): raise ValueError('Recebimento duplicado.')
        if round(sum(r['amount'] for r in added)*100)!=round((after-before)*100):
            raise ValueError('Recebimento exige lançamento correspondente.')
    for p in new['proposals']:
        if not any(str(x['code'])==str(p['code']) for x in old['proposals']) and not valid_entity_protocol(p['code'],'ORC'):
            raise ValueError('Protocolo de orçamento deve seguir ORC-YYYYMMDD-HHMMSScc.')
        if not any(str(x['code'])==str(p['code']) for x in old['proposals']) and (p.get('receivedValue') or p.get('projectCode')):
            raise ValueError('Crie a proposta antes de emitir a ordem ou receber.')

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(FRONTEND),**kwargs)
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Cache-Control','no-store')
        super().end_headers()
    def reply(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def authorized(self):
        password=os.environ.get('ORION_LAN_PASSWORD','')
        if not password: return True
        expected='Basic '+base64.b64encode(('orion:'+password).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization',''),expected): return True
        self.send_response(401)
        self.send_header('WWW-Authenticate','Basic realm="OrionHUB rede local", charset="UTF-8"')
        self.send_header('Content-Length','0')
        self.end_headers()
        return False
    def do_GET(self):
        if not self.authorized(): return
        if self.path == '/api/health': return self.reply(200,{'status':'ok','mode':'local-demo'})
        if self.path == '/api/workspace':
            with LOCK, closing(connect()) as con, con:
                revision,payload=con.execute('SELECT revision,payload FROM workspace WHERE id=1').fetchone()
            return self.reply(200,{'revision':revision,'data':json.loads(payload)})
        if self.path.startswith('/api/'): return self.reply(404,{'error':'Rota não encontrada.'})
        if self.path.split('?')[0] not in ['/', '/index.html','/app.js','/billing-pdf.js','/styles.css','/seed.json','/favicon.ico']:
            return self.reply(404,{'error':'Arquivo não encontrado.'})
        super().do_GET()
    def do_PUT(self):
        if not self.authorized(): return
        if self.path not in ['/api/workspace','/api/undo']: return self.reply(404,{'error':'Rota não encontrada.'})
        # Block cross-site writes, including browser requests from other local origins.
        origin=self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host',''):
            return self.reply(403,{'error':'Origem não permitida.'})
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return self.reply(403,{'error':'Origem não permitida.'})
        if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
            return self.reply(415,{'error':'Envie application/json.'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0 < size <= MAX_BODY: return self.reply(413,{'error':'Corpo ausente ou muito grande.'})
            data=json.loads(self.rfile.read(size))
            if self.path=='/api/workspace': validate(data)
            expected=int(self.headers.get('If-Match','-1'))
            with LOCK, closing(connect()) as con, con:
                revision,previous_payload=con.execute('SELECT revision,payload FROM workspace WHERE id=1').fetchone()
                if expected!=revision: return self.reply(409,{'error':'Conflito de versão. Atualize os dados.'})
                if self.path=='/api/undo':
                    row=con.execute('SELECT payload FROM undo_log WHERE revision=?',(revision,)).fetchone()
                    if row is None: return self.reply(409,{'error':'Esta ação não está mais disponível para desfazer.'})
                    con.execute('UPDATE workspace SET revision=?,payload=? WHERE id=1',(revision+1,row[0]))
                    con.execute('DELETE FROM undo_log WHERE revision=?',(revision,))
                    return self.reply(200,{'revision':revision+1,'data':json.loads(row[0])})
                validate_transition(json.loads(previous_payload),data)
                con.execute('INSERT INTO undo_log VALUES (?,?)',(revision+1,previous_payload))
                con.execute('DELETE FROM undo_log WHERE revision < ?',(revision-5,))
                con.execute('UPDATE workspace SET revision=?,payload=? WHERE id=1',(revision+1,json.dumps(data,ensure_ascii=False,allow_nan=False)))
            self.reply(200,{'revision':revision+1})
        except (ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
            self.reply(400,{'error':str(error) if isinstance(error,ValueError) else 'Dados inválidos. Verifique os campos.'})

if __name__ == '__main__':
    connect().close()
    host=os.environ.get('ORION_HOST','127.0.0.1')
    if host not in ['127.0.0.1','localhost','::1'] and len(os.environ.get('ORION_LAN_PASSWORD',''))<16:
        raise SystemExit('Acesso de rede exige ORION_LAN_PASSWORD com no mínimo 16 caracteres.')
    port=int(os.environ.get('PORT','8000'))
    print(f'OrionHUB: http://{host}:{port} · modo demonstrativo local',flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
