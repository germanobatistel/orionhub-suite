"""Verificações de build sem acesso a banco ou segredos."""
import ast,json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
for p in [*root.glob('api/*.py'),*root.glob('backend/*.py'),*root.glob('scripts/*.py')]:ast.parse(p.read_text(encoding='utf-8'),filename=str(p))
json.loads((root/'vercel.json').read_text())
for name in ['app.js','billing-pdf.js']:subprocess.run(['node','--check',str(root/'frontend'/name)],check=True)
seed=json.loads((root/'frontend/seed.json').read_text(encoding='utf-8'))
if seed['clients'] or seed['proposals'] or seed['events']:raise SystemExit('Seed de produção deve estar vazio.')
if not (root/'public').is_dir():raise SystemExit('Diretório public ausente.')
print('Build validado: Python, JavaScript, configuração e seed vazio.')
