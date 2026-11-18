# OrionHUB — GitHub e Vercel

Versão preparada para publicação: interface responsiva existente, Python Functions e PostgreSQL. O servidor Windows/SQLite da instalação local continua separado e não precisa ser enviado ao GitHub.

## Publicação
1. Crie um repositório **privado** no GitHub e envie o conteúdo desta pasta, incluindo `.github`, `.gitignore`, `.python-version` e `vercel.json`. Não envie a pasta antiga inteira, o SQLite, arquivos de acesso ou exports.
2. Na Vercel, importe esse repositório. Se enviar esta pasta como raiz do repositório, deixe Root Directory na raiz. Framework Preset: **Other**; as configurações de build e saída já estão no `vercel.json`. Não escolha apenas `frontend` como raiz, pois isso excluiria a API.
3. Conecte um banco PostgreSQL persistente, preferencialmente Neon pelo Marketplace da Vercel. Guarde a URL de conexão com TLS. Para a aplicação, use a URL com pool de conexões fornecida pelo provedor.
4. Configure as variáveis no painel da Vercel (Production). Use um banco separado para Preview; não copie a credencial do banco de produção para previews de branches.

| Variável | Configuração |
|---|---|
| `DATABASE_URL` | URL PostgreSQL, com banco e usuário próprios; TLS obrigatório |
| `ORION_APP_USER` | Nome de acesso ao site, sem `:` |
| `ORION_APP_PASSWORD` | Senha aleatória com pelo menos 32 caracteres |

Gere a senha localmente: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Não cole valores reais em código, issues, README ou comandos versionados. `.env.example` contém apenas exemplos. Sem configuração válida, o site devolve 503 e não permite acesso aos dados.

5. Inicialize o banco conforme a seção abaixo. Faça o primeiro deploy ou um Redeploy depois de definir as variáveis. A página solicitará usuário e senha pelo diálogo de autenticação do navegador.
6. Abra o domínio HTTPS da Vercel, entre e confira `/api/health` (deve retornar status ok). O computador local não precisa ficar ligado após a migração e publicação.

## Preservar os dados da instalação atual
Os dados reais **não estão neste repositório**. A base local original foi mantida no computador. A importação transfere o JSON completo, incluindo contratos em base64, comprovantes, decisões, modelos personalizados, snapshots e protocolos. Não cria projetos de demonstração e não remove anexos.

No PowerShell, dentro desta pasta:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# Informe DATABASE_URL nesta sessão, sem salvar a senha no repositório:
$env:DATABASE_URL = Read-Host 'URL PostgreSQL'
python scripts/migrate.py --sqlite 'C:\CAMINHO\PARA\preview-v3.sqlite3'
```

Na instalação atual, a base de uso está na pasta `work/preview-v3.sqlite3` do projeto original. Confirme esse arquivo antes de executar. O script abre o SQLite em modo somente leitura. Antes da importação final, pare as gravações na instalação local para evitar que alterações posteriores fiquem somente no computador. Faça cópia de segurança do arquivo SQLite antes de migrar.

O script não sobrescreve um workspace já existente no PostgreSQL. Se você inicializar com `--empty` e depois quiser importar dados reais, use um **novo banco vazio** e execute a importação real nele; não apague a base em uso para contornar essa proteção. Para uma instalação realmente nova, use `python scripts/migrate.py --empty` em vez de `--sqlite`.

O histórico operacional presente no JSON é importado. As antigas cinco revisões de desfazer do SQLite não são copiadas; o PostgreSQL começa a guardar novas revisões a partir da migração. O importador preserva a revisão atual e aplica somente a normalização de protocolos legados já utilizada pelo sistema.

## Testes e homologação
Requer Python 3.12 e Node.js 22 para as verificações de JavaScript:

```powershell
python scripts/check_release.py
python -m unittest discover -s backend -p test_server.py
python -m unittest discover -s tests -p 'test_*.py'
node backend/test_client.mjs
node tests/transport.mjs
```

O workflow GitHub Actions executa essas verificações em push e pull request. Os testes de armazenamento de nuvem usam um substituto SQLite transacional para validar o protocolo de partes e conflitos; **não substituem homologação contra o PostgreSQL real**. Depois de configurar um banco de teste, use as mesmas variáveis de ambiente e execute `python scripts/dev.py`, abrindo `http://127.0.0.1:8002`. O banco pode conter dados reais: use uma base de homologação separada para os testes de gravação.

Antes de liberar uso: confirme carregamento após recarregar, edição e desfazer, conflito entre duas abas, download do contrato anexado, geração de PDF, seleção dos modelos e navegação no smartphone. Revise também a primeira execução em Vercel e os logs de erro. Nenhum deploy remoto foi feito como parte da preparação destes arquivos.

## Armazenamento e limites atuais
A aplicação mantém um workspace compartilhado por organização em PostgreSQL, com revisão e transação. Os anexos continuam embutidos no JSON para preservar o comportamento existente. A comunicação acontece em partes de até 250 mil caracteres, evitando enviar todo o cadastro numa única requisição; o limite total de workspace permanece **16 MB**, e cada anexo individual segue o limite de 2 MB existente. Ao atingir o limite, a gravação é recusada explicitamente: anexos não são descartados. Para crescimento além disso, será necessário extrair arquivos para armazenamento privado de objetos e paginar o workspace antes de ampliar a operação.

Um único usuário/senha compartilhado protege toda a instalação por HTTP Basic sobre HTTPS. Isso oferece controle de acesso inicial, mas não contas individuais, permissões por funcionário, MFA ou trilha de autenticação individual. Identificações de autores de decisões continuam sendo informadas pelo operador. Não descreva esta versão como multiempresa ou com gestão de usuários.

Envios incompletos não alteram o workspace e expiram após uma hora; a limpeza ocorre no próximo envio. O último salvamento pode ser desfeito enquanto sua revisão for a atual e ainda existir no histórico (cinco revisões). Backups não se resumem ao botão desfazer: habilite retenção/backup no provedor do banco e teste restauração. Exportação privada: `python scripts/backup.py backups/workspace.json`. O script não sobrescreve arquivo de backup existente. Arquivos dessa pasta são ignorados pelo Git.

## Segurança e operação
- Somente a função autenticada serve a interface e API; não há diretório estático com cadastros nem rota pública para seed, código Python ou arquivos de ambiente.
- O servidor usa validação existente, comparação de credenciais em tempo constante, transações e controle `If-Match`. As gravações entre origens são bloqueadas.
- Banco exige TLS. A URL e as credenciais permanecem em variáveis do ambiente da Vercel, nunca no JavaScript enviado ao navegador.
- Ative as proteções de tráfego/rate limit disponíveis no projeto Vercel, monitore erros e uso do banco e mantenha dependências atualizadas. Não há limitador próprio de tentativas de login nesta versão.
- A senha compartilhada deve ser trocada no painel quando o acesso de alguém precisar ser revogado, com novo deploy para aplicar o ambiente atualizado.
- Os arquivos locais `iniciar*.cmd/.ps1` e as senhas de Wi-Fi não pertencem a esta versão de publicação.

## Estrutura
- `frontend/`: interface; seed de produção vazio, sem clientes ou projetos.
- `api/index.py`: única entrada pública Python, autenticação e rotas.
- `backend/cloud_store.py`: PostgreSQL, partes, revisões e desfazer.
- `backend/server.py`: validações de negócio compartilhadas e compatibilidade local.
- `scripts/`: migração, backup e checagens.
- `tests/`: fixtures sintéticas e testes de nuvem/transporte.
- `DOCUMENTACAO_TECNICA.md`: dicionário de dados, MER e particularidades da publicação.

## Referências verificadas
- [Python Functions na Vercel](https://vercel.com/docs/functions/runtimes/python)
- [Configuração vercel.json](https://vercel.com/docs/project-configuration/vercel-json)
- [Limites de Functions e payload](https://vercel.com/docs/functions/limitations)
- [Armazenamento na Vercel](https://vercel.com/docs/storage)
- [Driver PostgreSQL Psycopg](https://www.psycopg.org/install/)
