"""Persistência compartilhada; sem estado em memória entre invocações."""
import json, os
from contextlib import contextmanager
from backend.server import validate, validate_transition, MAX_BODY
PART_CHARS = 250_000
class StoreError(Exception):
    def __init__(self,status,message): self.status,self.message=status,message
@contextmanager
def connection():
    import psycopg
    url=os.environ.get('DATABASE_URL','')
    if not url: raise StoreError(503,'Banco não configurado.')
    with psycopg.connect(url,connect_timeout=10,sslmode='require',options='-c statement_timeout=20000') as con:
        yield con

def encode(data):
    text=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    if len(text.encode())>MAX_BODY: raise StoreError(413,'Workspace excede 16 MB. Exporte e planeje a ampliação do armazenamento.')
    return text

def snapshot(part=None,expected=None):
    if part is not None and part<0: raise StoreError(400,'Parte inválida.')
    start=(part or 0)*PART_CHARS+1
    with connection() as con:
        row=con.execute('SELECT revision,substring(payload,%s,%s),char_length(payload) FROM workspace WHERE id=1',(start,PART_CHARS)).fetchone()
    if not row: raise StoreError(503,'Banco ainda não inicializado. Execute a migração.')
    revision,text,length=row
    if expected is not None and expected!=revision: raise StoreError(409,'A revisão mudou durante a leitura. Recarregue.')
    count=(length+PART_CHARS-1)//PART_CHARS
    if part is not None:
        if part>=count: raise StoreError(400,'Parte inválida.')
        return {'revision':revision,'text':text}
    if count==1: return {'revision':revision,'data':json.loads(text)}
    return {'revision':revision,'parts':count}

def stage(upload,part,total,revision,text):
    if not isinstance(text,str) or len(text)>PART_CHARS or type(total) is not int or not 1<=total<=128 or type(part) is not int or not 0<=part<total:
        raise StoreError(400,'Parte de envio inválida.')
    with connection() as con:
        con.execute("DELETE FROM workspace_uploads WHERE created_at < now() - interval '1 hour'")
        current=con.execute('SELECT revision FROM workspace WHERE id=1').fetchone()
        if not current: raise StoreError(503,'Banco não inicializado.')
        if current[0]!=revision: raise StoreError(409,'Os dados mudaram. Recarregue antes de salvar.')
        # Limite global contra acúmulo de partes abandonadas (um workspace compartilhado).
        count=con.execute('SELECT count(*) FROM workspace_uploads').fetchone()[0]
        if count>=512: raise StoreError(429,'Há muitos envios em andamento. Aguarde a limpeza dos envios expirados.')
        con.execute('INSERT INTO workspace_uploads (upload_id,part,total,revision,payload) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(upload_id,part) DO UPDATE SET payload=excluded.payload,total=excluded.total,revision=excluded.revision', (upload,part,total,revision,text))
    return {'ok':True}

def update(expected,data=None,upload=None,undo=False):
    with connection() as con:
        row=con.execute('SELECT revision,payload FROM workspace WHERE id=1 FOR UPDATE').fetchone()
        if not row: raise StoreError(503,'Banco não inicializado.')
        revision,previous=row
        if expected!=revision: raise StoreError(409,'Conflito de revisão. Recarregue os dados.')
        if undo:
            old=con.execute('SELECT payload FROM undo_log WHERE revision=%s',(revision,)).fetchone()
            if not old: raise StoreError(409,'Esta ação não está disponível para desfazer.')
            text=old[0]
            con.execute('DELETE FROM undo_log WHERE revision=%s',(revision,))
        else:
            if upload:
                rows=con.execute('SELECT part,total,revision,payload FROM workspace_uploads WHERE upload_id=%s ORDER BY part FOR UPDATE',(upload,)).fetchall()
                if not rows or len(rows)!=rows[0][1] or any(r[0]!=i or r[1]!=len(rows) or r[2]!=revision for i,r in enumerate(rows)):
                    raise StoreError(400,'Envio incompleto ou revisão incompatível.')
                joined=''.join(r[3] for r in rows)
                if len(joined.encode())>MAX_BODY: raise StoreError(413,'Workspace excede 16 MB.')
                data=json.loads(joined)
            validate(data);validate_transition(json.loads(previous),data)
            text=encode(data)
            con.execute('INSERT INTO undo_log(revision,payload) VALUES (%s,%s)',(revision+1,previous))
            con.execute('DELETE FROM undo_log WHERE revision<%s',(revision-4,))
        con.execute('UPDATE workspace SET revision=%s,payload=%s WHERE id=1',(revision+1,text))
        if upload: con.execute('DELETE FROM workspace_uploads WHERE upload_id=%s',(upload,))
    return {'revision':revision+1}
