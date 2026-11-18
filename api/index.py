"""Entrada Vercel Python: protege API e interface com credenciais do ambiente."""
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import base64,hmac,json,logging,mimetypes,os,sys,uuid
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from backend.cloud_store import snapshot,stage,update,StoreError
ASSETS={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/styles.css':'styles.css','/billing-pdf.js':'billing-pdf.js'}
class handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        super().end_headers()
    def reply(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def authorized(self):
        user,password=os.environ.get('ORION_APP_USER',''),os.environ.get('ORION_APP_PASSWORD','')
        if not user or ':' in user or len(password)<32 or password.startswith('SUBSTITUA') or not os.environ.get('DATABASE_URL'):
            self.reply(503,{'error':'Configure DATABASE_URL, ORION_APP_USER e ORION_APP_PASSWORD antes de publicar.'});return False
        expected='Basic '+base64.b64encode((user+':'+password).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization','').encode(),expected.encode()):return True
        self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="OrionHUB", charset="UTF-8"');self.send_header('Content-Length','0');self.end_headers();return False
    def do_GET(self):
        if not self.authorized():return
        parsed=urlsplit(self.path);path=parsed.path;qs=parse_qs(parsed.query)
        try:
            if path=='/api/health':
                snapshot();return self.reply(200,{'status':'ok','storage':'postgresql'})
            if path=='/api/workspace':
                part=int(qs['part'][0]) if 'part' in qs else None
                revision=int(qs['revision'][0]) if 'revision' in qs else None
                return self.reply(200,snapshot(part,revision))
            if path in ASSETS:
                asset=ROOT/'frontend'/ASSETS[path];body=asset.read_bytes();mime=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream'
                self.send_response(200);self.send_header('Content-Type',mime+'; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
            self.reply(404,{'error':'Rota não encontrada.'})
        except Exception as error:self.failure(error)
    def do_PUT(self):
        if not self.authorized():return
        origin=self.headers.get('Origin');host=self.headers.get('Host','')
        if origin and origin not in ['https://'+host,'http://'+host] or self.headers.get('Sec-Fetch-Site')=='cross-site':
            return self.reply(403,{'error':'Origem não permitida.'})
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.reply(415,{'error':'Envie application/json.'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=2_000_000:raise StoreError(413,'Envie o workspace em partes menores.')
            data=json.loads(self.rfile.read(size));expected=int(self.headers.get('If-Match','-1'));path=urlsplit(self.path).path
            if path=='/api/upload':
                upload=str(uuid.UUID(data['uploadId']))
                return self.reply(200,stage(upload,data['part'],data['total'],expected,data['text']))
            if path=='/api/commit':return self.reply(200,update(expected,upload=str(uuid.UUID(data['uploadId']))))
            if path=='/api/workspace':return self.reply(200,update(expected,data=data))
            if path=='/api/undo':return self.reply(200,update(expected,undo=True))
            self.reply(404,{'error':'Rota não encontrada.'})
        except Exception as error:self.failure(error)
    def failure(self,error):
        if isinstance(error,StoreError):return self.reply(error.status,{'error':error.message})
        if isinstance(error,(ValueError,TypeError,KeyError,UnicodeDecodeError)):return self.reply(400,{'error':str(error) if isinstance(error,ValueError) else 'Dados inválidos.'})
        logging.error('OrionHUB request failed: %s',type(error).__name__)
        self.reply(503,{'error':'Serviço indisponível. Confira banco, migração e logs do servidor.'})
