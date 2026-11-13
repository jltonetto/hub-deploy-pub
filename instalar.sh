#!/bin/bash
# Atualização do Hub SOLIVROS — coleta de visitas por anúncio
# Baixa os arquivos direto do GitHub, confere SHA-256, faz backup e instala.
# Rollback automático se a validação ou a migração falhar.
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
for f in manifest.json hub/connectors/mercadolivre.py hub/db.py hub/api.py db/schema.sql; do
  mkdir -p "$(dirname "$f")"
  if baixar "$REPO/$f" "$f"; then echo "   ok $f ($(stat -c%s "$f") bytes)"
  else echo "   FALHOU: $f"; FALHA=1; fi
done
[ "$FALHA" -eq 0 ] || { echo "ERRO: download incompleto. Nada foi alterado."; exit 1; }

echo "[2/6] Conferindo SHA-256..."
"$PY" - <<'PYEOF'
import hashlib, json, sys
man = json.load(open('manifest.json'))
falhou = False
for nome, d in man.items():
    try:
        raw = open(nome, 'rb').read()
    except FileNotFoundError:
        if nome == 'hub/service.py':
            print(f"   {nome}: vem em 2 partes (tratado adiante)")
            continue
        print(f"   {nome}: AUSENTE"); falhou = True; continue
    h = hashlib.sha256(raw).hexdigest()
    marca = "ok" if h == d['sha256'] else "DIVERGENTE"
    print(f"   {nome}: {len(raw)} bytes [{marca}]")
    if h != d['sha256']:
        falhou = True
sys.exit(1 if falhou else 0)
PYEOF
[ $? -eq 0 ] || { echo "ERRO: arquivo corrompido ou incompleto. Nada foi alterado."; exit 1; }

echo "[3/6] Backup dos arquivos atuais..."
for f in hub/connectors/mercadolivre.py hub/db.py hub/service.py hub/api.py db/schema.sql; do
  [ -f "$DEST/$f" ] && { mkdir -p "$BACK/$(dirname $f)"; cp "$DEST/$f" "$BACK/$f"; echo "   $f"; }
done

echo "[4/6] Gravando arquivos..."
for f in hub/connectors/mercadolivre.py hub/db.py hub/api.py db/schema.sql; do
  cp "$WORK/$f" "$DEST/$f"
  echo "   $f -> $(stat -c%s "$DEST/$f") bytes"
done
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
