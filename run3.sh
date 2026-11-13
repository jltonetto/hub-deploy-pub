#!/bin/bash
# Hub SOLIVROS — correcao v3 da coleta de visitas
# Restaura do backup se necessario, aplica o patch, limpa zeros falsos e testa.
set -u
AQUI="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/hub-anuncios
PY=$DEST/.venv/bin/python
DB=$DEST/contas/livrolandia/data/hub.db

echo "=============================================="
echo " Hub SOLIVROS — correcao da coleta de visitas"
echo "=============================================="
[ -d "$DEST" ] || { echo "ERRO: $DEST nao existe"; exit 1; }
[ -x "$PY" ]   || { echo "ERRO: python da venv nao encontrado"; exit 1; }

# ---- 0. o patch anterior deixou arquivo quebrado? entao restaura ----
echo "[0/5] Verificando se os arquivos estao validos..."
QUEBRADO=0
for f in hub/service.py hub/api.py hub/connectors/mercadolivre.py; do
  if ! "$PY" -c "import ast;ast.parse(open('$DEST/$f').read())" 2>/dev/null; then
    echo "   INVALIDO: $f"
    QUEBRADO=1
  else
    echo "   ok: $f"
  fi
done

if [ "$QUEBRADO" -eq 1 ]; then
  ULTIMO=$(ls -dt /root/hub-backup-* 2>/dev/null | head -1)
  if [ -z "$ULTIMO" ]; then
    echo "ERRO: arquivos invalidos e nenhum backup encontrado."
    exit 1
  fi
  echo "   restaurando de $ULTIMO"
  for f in service.py api.py mercadolivre.py; do
    case $f in
      service.py|api.py) [ -f "$ULTIMO/$f" ] && cp "$ULTIMO/$f" "$DEST/hub/$f" && echo "     restaurado hub/$f" ;;
      mercadolivre.py)   [ -f "$ULTIMO/$f" ] && cp "$ULTIMO/$f" "$DEST/hub/connectors/$f" && echo "     restaurado connectors/$f" ;;
    esac
  done
  echo "   revalidando..."
  for f in hub/service.py hub/api.py hub/connectors/mercadolivre.py; do
    "$PY" -c "import ast;ast.parse(open('$DEST/$f').read())" || {
      echo "ERRO: $f continua invalido apos restauracao. Pare aqui e me avise."; exit 1; }
  done
  echo "   restaurado e valido"
fi

echo "[1/5] Aplicando patch3 (valida em memoria antes de gravar)..."
if ! "$PY" "$AQUI/patch3.py"; then
  echo "ERRO: patch falhou. Nada foi gravado."
  exit 1
fi

echo "[2/5] Limpando as visitas ZERO falsas do bug anterior..."
"$PY" -c "
import sqlite3
c = sqlite3.connect('$DB')
n = c.execute('SELECT COUNT(*) FROM listing_visits WHERE total = 0').fetchone()[0]
print(f'   visitas zero a limpar: {n}')
c.execute('DELETE FROM listing_visits WHERE total = 0')
c.execute('UPDATE listings SET visits_total=NULL, visits_at=NULL, conversion_rate=NULL WHERE visits_total=0')
c.commit()
print('   limpeza concluida')
"

echo "[3/5] Reiniciando..."
systemctl restart hub-api
sleep 8
echo "   hub-api: $(systemctl is-active hub-api)"
echo "   health : $(curl -s --max-time 8 http://127.0.0.1:9000/health)"

echo "[4/5] Testando a coleta com 20 anuncios..."
TOKEN=$(grep '^HUB_API_TOKEN=' "$DEST/.secrets/hub.env" | cut -d= -f2)
curl -s -X POST -H "X-Hub-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"limite":20,"somente_sem_dado":true}' \
  "http://127.0.0.1:9000/painel/api/visitas/coletar"
echo

echo "[5/5] Resumo:"
curl -s -H "X-Hub-Token: $TOKEN" "http://127.0.0.1:9000/painel/api/visitas/resumo?limite=5" | "$PY" -m json.tool 2>/dev/null || \
curl -s -H "X-Hub-Token: $TOKEN" "http://127.0.0.1:9000/painel/api/visitas/resumo?limite=5"

echo
echo "=============================================="
echo " Se 'com_trafego' > 0, a correcao funcionou."
echo "=============================================="
