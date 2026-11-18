"""Cria schema e importa SQLite explicitamente; nunca sobrescreve uma base existente."""
import argparse,json,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.cloud_store import connection,encode
from backend.server import migrate_legacy,validate
parser=argparse.ArgumentParser();parser.add_argument('--sqlite',type=Path);parser.add_argument('--empty',action='store_true');args=parser.parse_args()
if bool(args.sqlite)==bool(args.empty):parser.error('Escolha --sqlite ARQUIVO ou --empty.')
if args.sqlite:
    if not args.sqlite.is_file():parser.error('SQLite não encontrado.')
    with sqlite3.connect(args.sqlite.resolve().as_uri()+'?mode=ro',uri=True) as source:
        revision,text=source.execute('SELECT revision,payload FROM workspace WHERE id=1').fetchone()
    data=migrate_legacy(json.loads(text))
else:
    revision=1;data=json.loads((Path(__file__).resolve().parents[1]/'frontend/seed.json').read_text(encoding='utf-8'))
validate(data);text=encode(data)
with connection() as con:
    con.execute('CREATE TABLE IF NOT EXISTS workspace(id integer PRIMARY KEY CHECK(id=1),revision bigint NOT NULL,payload text NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS undo_log(revision bigint PRIMARY KEY,payload text NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS workspace_uploads(upload_id uuid NOT NULL,part integer NOT NULL,total integer NOT NULL,revision bigint NOT NULL,payload text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(upload_id,part))')
    con.execute('CREATE INDEX IF NOT EXISTS workspace_uploads_created ON workspace_uploads(created_at)')
    con.execute('INSERT INTO workspace(id,revision,payload) VALUES(1,%s,%s) ON CONFLICT(id) DO NOTHING',(revision,text))
    actual=con.execute('SELECT revision,payload FROM workspace WHERE id=1').fetchone()
    if actual!=(revision,text):print('Base já preenchida: dados existentes preservados; importação não aplicada.')
    else:print('Base pronta. Clientes:',len(data['clients']),'Propostas:',len(data['proposals']),'Eventos:',len(data['events']))
