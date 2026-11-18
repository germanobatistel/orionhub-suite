"""Executa a mesma entrada serverless localmente para homologação com PostgreSQL."""
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from api.index import handler
print('http://127.0.0.1:8002 (credenciais do ambiente)')
ThreadingHTTPServer(('127.0.0.1',8002),handler).serve_forever()
