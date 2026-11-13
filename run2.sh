#!/bin/bash
# Hub SOLIVROS — correcao da coleta de visitas (v2)
# Aplica patch2.py, limpa as visitas zero falsas e reinicia.
set -u
AQUI="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/hub-anuncios
PY=$DEST/.venv/bin/python

echo "=============================================="
echo " Hub SOLIVROS — correcao da coleta de visitas"
echo "=============================================="
[ -d "$DEST" ] || { echo "ERRO: $DEST nao existe"; exit 1; }
[ -x "$PY" ]   || { echo "ERRO: python da venv nao encontrado"; exit 1; }

echo "[1/4] Aplicando patch (conector, service, api)..."
if ! "$PY" "$AQUI/patch2.py"; then
  echo "ERRO: o patch falhou. Nada foi alterado."
  exit 1
fi

echo "[2/4] Limpando as visitas ZERO falsas gravadas com o bug..."
"$PY" -c "
import sqlite3
c = sqlite3.connect('/opt/hub-anuncios/contas/livrolandia/data/hub.db')
n = c.execute('SELECT COUNT(*) FROM listing_visits WHERE total = 0').fetchone()[0]
print(f'   visitas zero a limpar: {n}')
c.execute('DELETE FROM listing_visits WHERE total = 0')
c.execute('UPDATE listings SET visits_total = NULL, visits_at = NULL, conversion_rate = NULL WHERE visits_total = 0')
c.commit()
print('   limpeza concluida')
"

echo "[3/4] Reiniciando..."
systemctl restart hub-api
sleep 8
echo "   hub-api: $(systemctl is-active hub-api)"
echo "   health : $(curl -s --max-time 8 http://127.0.0.1:9000/health)"

echo "[4/4] Testando a coleta com 20 anuncios..."
TOKEN=$(grep '^HUB_API_TOKEN=' "$DEST/.secrets/hub.env" | cut -d= -f2)
curl -s -X POST -H "X-Hub-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"limite":20,"somente_sem_dado":true}' \
  "http://127.0.0.1:9000/painel/api/visitas/coletar"
echo
echo "--- resumo ---"
curl -s -H "X-Hub-Token: $TOKEN" "http://127.0.0.1:9000/painel/api/visitas/resumo?limite=3"
echo
echo
echo "PRONTO. Se o resumo mostrar visitas > 0, a correcao funcionou."
