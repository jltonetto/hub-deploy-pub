#!/bin/bash
# Atualização do Hub SOLIVROS — coleta de visitas por anúncio
# Reconstrói service.py a partir de 2 partes, confere o SHA-256 do arquivo
# final e só então instala. Backup + rollback automático.
set -u

REPO="https://raw.githubusercontent.com/jltonetto/hub-deploy-pub/main"
DEST=/opt/hub-anuncios
PY=$DEST/.venv/bin/python
WORK=/root/hub-deploy
BACK=/root/hub-backup-$(date +%Y%m%d-%H%M%S)

echo "=============================================="
echo " Hub SOLIVROS — atualizacao: coleta de visitas"
echo "=============================================="

[ -d "$DEST" ] || { echo "ERRO: $DEST nao existe"; exit 1; }
[ -x "$PY" ]   || { echo "ERRO: python da venv nao encontrado em $PY"; exit 1; }

mkdir -p "$WORK" "$BACK"
cd "$WORK" || exit 1

baixar() {
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
  else wget -qO "$2" "$1"; fi
}

echo "[1/6] Baixando arquivos..."
FALHA=0
for f in manifest.json hub__connectors__mercadolivre.py.b64 hub__db.py.b64 hub__api.py.b64 svc_a.b64 svc_b.b64 db__schema.sql.b64; do
  if baixar "$REPO/$f" "$f"; then echo "   ok $f ($(stat -c%s "$f") bytes)"
  else echo "   FALHOU: $f"; FALHA=1; fi
done
[ "$FALHA" -eq 0 ] || { echo "ERRO: download incompleto. Nada foi alterado."; exit 1; }

echo "[2/6] Reconstruindo e conferindo o SHA-256..."
"$PY" - <<'PYEOF'
import base64, gzip, hashlib, json, os, sys
falhou = False
man = json.load(open('manifest.json'))
def dec(nome):
    return gzip.decompress(base64.b64decode(open(nome).read()))
sa, sb = dec('svc_a.b64'), dec('svc_b.b64')
service = sa + b'\n' + sb
esperado = man['hub/service.py']['sha256']
obtido = hashlib.sha256(service).hexdigest()
print(f"   service.py: {len(service)} bytes")
print(f"   esperado: {esperado[:24]}...")
print(f"   obtido  : {obtido[:24]}...")
if obtido != esperado:
    print("   DIVERGENTE — arquivo nao sera instalado"); falhou = True
else:
    open('/tmp/service_novo.py','wb').write(service)
    print("   CONFERIDO OK")
for chave, arq in (('hub/connectors/mercadolivre.py','hub__connectors__mercadolivre.py.b64'),
                   ('hub/db.py','hub__db.py.b64'),
                   ('hub/api.py','hub__api.py.b64'),
                   ('db/schema.sql','db__schema.sql.b64')):
    raw = dec(arq)
    h = hashlib.sha256(raw).hexdigest()
    marca = "ok" if h == man[chave]['sha256'] else "DIVERGENTE"
    print(f"   {chave}: {len(raw)} bytes [{marca}]")
    if h != man[chave]['sha256']: falhou = True
sys.exit(1 if falhou else 0)
PYEOF
[ $? -eq 0 ] || { echo "ERRO: pacote corrompido. Nada foi alterado."; exit 1; }

echo "[3/6] Backup dos arquivos atuais..."
for f in hub/connectors/mercadolivre.py hub/db.py hub/service.py hub/api.py db/schema.sql; do
  [ -f "$DEST/$f" ] && { mkdir -p "$BACK/$(dirname $f)"; cp "$DEST/$f" "$BACK/$f"; echo "   $f"; }
done

echo "[4/6] Gravando arquivos..."
"$PY" - <<'PYEOF'
import base64, gzip, os, shutil
def instalar(origem, destino):
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    shutil.copyfile(origem, destino)
    print(f"   {os.path.basename(destino)} -> {os.path.getsize(destino)} bytes")
instalar('/tmp/service_novo.py', '/opt/hub-anuncios/hub/service.py')
for arq, dest in (('hub__connectors__mercadolivre.py.b64','/opt/hub-anuncios/hub/connectors/mercadolivre.py'),
                  ('hub__db.py.b64','/opt/hub-anuncios/hub/db.py'),
                  ('hub__api.py.b64','/opt/hub-anuncios/hub/api.py'),
                  ('db__schema.sql.b64','/opt/hub-anuncios/db/schema.sql')):
    raw = gzip.decompress(base64.b64decode(open(arq).read()))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, 'wb').write(raw)
    print(f"   {os.path.basename(dest)} -> {len(raw)} bytes")
PYEOF
chown -R hub:hub "$DEST/hub" "$DEST/db" 2>/dev/null || true

echo "[5/6] Migracao do banco..."
cd "$DEST" || exit 1
set -a; . "$DEST/.secrets/hub.env" 2>/dev/null; set +a
if ! "$PY" -m hub.cli init-db 2>&1 | tail -2; then
  echo "ERRO na migracao — restaurando backup"
  cp -r "$BACK"/* "$DEST"/ 2>/dev/null
  systemctl restart hub-api
  exit 1
fi

echo "[6/6] Verificando e reiniciando..."
"$PY" -c "
import sqlite3
c = sqlite3.connect('/opt/hub-anuncios/contas/livrolandia/data/hub.db')
cols = {r[1] for r in c.execute('PRAGMA table_info(listings)')}
tabs = {r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}
print('   visits_total na tabela :', 'visits_total' in cols)
print('   conversion_rate        :', 'conversion_rate' in cols)
print('   tabela listing_visits  :', 'listing_visits' in tabs)
"
systemctl restart hub-api
sleep 8
echo "   hub-api: $(systemctl is-active hub-api)"
echo "   health : $(curl -s --max-time 8 http://127.0.0.1:9000/health)"

echo
echo "=============================================="
echo " CONCLUIDO. Backup em: $BACK"
echo " Reverter: cp -r $BACK/* $DEST/ && systemctl restart hub-api"
echo "=============================================="
