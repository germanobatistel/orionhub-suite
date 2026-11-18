"""Testes locais: HTTP real e armazenamento substituto transacional, sem banco remoto."""
import base64,copy,json,os,sqlite3,sys,tempfile,threading,unittest,uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend import cloud_store as store
from api import index
EMPTY={'company':{'fantasyName':'Teste'},'clients':[],'proposals':[],'events':[]}
class Adapter:
    def __init__(self,con):self.con=con
    def execute(self,sql,args=()):
        sql=sql.replace('char_length(','length(').replace('%s','?').replace(' FOR UPDATE','').replace("now() - interval '1 hour'","datetime('now','-1 hour')")
        return self.con.execute(sql,args)
class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'cloud-test.db'
        with sqlite3.connect(self.path) as con:
            con.executescript("CREATE TABLE workspace(id integer primary key,revision integer,payload text);CREATE TABLE undo_log(revision integer primary key,payload text);CREATE TABLE workspace_uploads(upload_id text,part integer,total integer,revision integer,payload text,created_at text DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(upload_id,part));")
            con.execute('INSERT INTO workspace VALUES(1,1,?)',(store.encode(EMPTY),))
        con.close()
        @contextmanager
        def local():
            con=sqlite3.connect(self.path)
            try:
                con.execute('BEGIN IMMEDIATE');yield Adapter(con);con.commit()
            except:con.rollback();raise
            finally:con.close()
        self.patch=patch.object(store,'connection',local);self.patch.start()
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def test_upload_commit_snapshot_undo_preserves_unicode(self):
        value=copy.deepcopy(EMPTY);value['company']['notes']='Informação çã 😀 '*45000
        text=store.encode(value);token=str(uuid.uuid4());total=(len(text)+store.PART_CHARS-1)//store.PART_CHARS
        for n in range(total):store.stage(token,n,total,1,text[n*store.PART_CHARS:(n+1)*store.PART_CHARS])
        self.assertEqual(store.snapshot()['revision'],1)
        self.assertEqual(store.update(1,upload=token),{'revision':2})
        meta=store.snapshot();self.assertGreater(meta['parts'],1)
        joined=''.join(store.snapshot(n,2)['text'] for n in range(meta['parts']))
        self.assertEqual(json.loads(joined),value)
        with self.assertRaises(store.StoreError) as conflict:store.update(1,data=EMPTY)
        self.assertEqual(conflict.exception.status,409)
        store.update(2,undo=True);self.assertEqual(store.snapshot()['data'],EMPTY)
    def test_incomplete_upload_never_updates_workspace(self):
        token=str(uuid.uuid4());store.stage(token,0,2,1,'{')
        with self.assertRaises(store.StoreError):store.update(1,upload=token)
        self.assertEqual(store.snapshot()['revision'],1)
    def test_invalid_workspace_rolls_back(self):
        with self.assertRaises(ValueError):store.update(1,data={'company':{}})
        self.assertEqual(store.snapshot()['data'],EMPTY)
    def test_stale_chunk_read_rejected(self):
        store.update(1,data=EMPTY)
        with self.assertRaises(store.StoreError) as err:store.snapshot(0,1)
        self.assertEqual(err.exception.status,409)
    def test_parts_are_bounded(self):
        with self.assertRaises(store.StoreError):store.stage(str(uuid.uuid4()),0,1,1,'x'*(store.PART_CHARS+1))
class HTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env=patch.dict(os.environ,{'DATABASE_URL':'postgresql://unused-test','ORION_APP_USER':'test','ORION_APP_PASSWORD':'x'*32});cls.env.start()
        cls.http=ThreadingHTTPServer(('127.0.0.1',0),index.handler);cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.http.server_port)
    @classmethod
    def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.env.stop()
    def request(self,path,method='GET',body=None,auth=True,headers=None):
        h=dict(headers or {})
        if auth:h['Authorization']='Basic '+base64.b64encode(('test:'+('x'*32)).encode()).decode()
        try:
            with urlopen(Request(self.url+path,method=method,data=body,headers=h)) as r:return r.status,r.read()
        except HTTPError as e:
            with e:return e.code,e.read()
    def test_interface_requires_authentication(self):self.assertEqual(self.request('/',auth=False)[0],401)
    def test_interface_and_assets_are_served(self):
        for path in ['/','/app.js','/styles.css','/billing-pdf.js']:self.assertEqual(self.request(path)[0],200)
    def test_no_seed_or_source_download(self):
        for path in ['/seed.json','/backend/server.py','/.env','/../.env']:self.assertEqual(self.request(path)[0],404)
    def test_unconfigured_application_fails_closed(self):
        with patch.dict(os.environ,{'ORION_APP_PASSWORD':''}):self.assertEqual(self.request('/')[0],503)
    def test_cross_origin_write_rejected(self):self.assertEqual(self.request('/api/workspace','PUT',b'{}',headers={'Origin':'https://evil.invalid','Content-Type':'application/json'})[0],403)
    def test_workspace_contract(self):
        with patch.object(index,'snapshot',return_value={'revision':1,'data':EMPTY}):self.assertEqual(json.loads(self.request('/api/workspace')[1])['data'],EMPTY)
    def test_invalid_upload_id(self):self.assertEqual(self.request('/api/upload','PUT',b'{"uploadId":"not-a-uuid"}',headers={'Content-Type':'application/json'})[0],400)
