#!/bin/bash
# Hub SOLIVROS — coleta de visitas (versão nova, sem dependência de api.py)
# Aplica PATCH em service.py e api.py; copia o resto; migra o banco; reinicia.
set -u
AQUI="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/hub-anuncios
PY=$DEST/.venv/bin/python

echo "=============================================="
echo " Hub SOLIVROS — coleta de visitas"
echo "=============================================="
[ -d "$DEST" ] || { echo "ERRO: $DEST nao existe"; exit 1; }
[ -x "$PY" ]   || { echo "ERRO: python da venv nao encontrado"; exit 1; }

echo "[1/5] Aplicando patch (service.py e api.py)..."
if ! "$PY" "$AQUI/patch.py"; then
  echo "ERRO: o patch falhou. Nada foi alterado."
  exit 1
fi

echo "[2/5] Copiando conector, db e schema..."
for f in hub/connectors/mercadolivre.py hub/db.py db/schema.sql; do
  cp "$AQUI/$f" "$DEST/$f"
  echo "   $f -> $(stat -c%s "$DEST/$f") bytes"
done
chown -R hub:hub "$DEST/hub" "$DEST/db" 2>/dev/null || true

echo "[3/5] Migracao do banco..."
cd "$DEST" || exit 1
set -a; . "$DEST/.secrets/hub.env" 2>/dev/null; set +a
if ! "$PY" -m hub.cli init-db 2>&1 | tail -2; then
  echo "ERRO na migracao"
  exit 1
fi

echo "[4/5] Conferindo colunas..."
"$PY" -c "
import sqlite3, sys
c = sqlite3.connect('/opt/hub-anuncios/contas/livrolandia/data/hub.db')
cols = {r[1] for r in c.execute('PRAGMA table_info(listings)')}
tabs = {r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}
ok = 'visits_total' in cols and 'listing_visits' in tabs
print('   visits_total..........', 'visits_total' in cols)
print('   conversion_rate.......', 'conversion_rate' in cols)
print('   tabela listing_visits.', 'listing_visits' in tabs)
sys.exit(0 if ok else 1)
" || { echo "ERRO: colunas nao foram criadas"; exit 1; }

echo "[5/5] Reiniciando..."
systemctl restart hub-api
sleep 8
echo "   hub-api: $(systemctl is-active hub-api)"
echo "   health : $(curl -s --max-time 8 http://127.0.0.1:9000/health)"
echo
echo "PRONTO."
