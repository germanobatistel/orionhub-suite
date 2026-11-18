"""Exporta cópia consistente do workspace remoto; arquivo privado fora do Git."""
import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.cloud_store import connection
p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args()
with connection() as con:revision,payload=con.execute('SELECT revision,payload FROM workspace WHERE id=1').fetchone()
a.output.parent.mkdir(parents=True,exist_ok=True)
with a.output.open('x',encoding='utf-8') as file:file.write(payload)
print('Backup criado. Revisão:',revision)
