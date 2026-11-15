#!/bin/bash
# Finaliza a instalacao do Real-ESRGAN: baixa o script, cria a lista de
# tarefas e roda o teste de 3 imagens.
#
# POR QUE ESTE ARQUIVO EXISTE:
#   o corrigir_libgl.sh e o instalar_upscale.sh abortavam antes de criar o
#   upscale.py e o tarefa.json — cada um parava num ponto diferente. Este
#   script faz o fechamento completo e idempotente: pode rodar quantas vezes
#   precisar.
set -u

DIR=/opt/upscale
PY=$DIR/venv/bin/python
BASE=https://raw.githubusercontent.com/jltonetto/hub-deploy-pub/main

echo "=============================================="
echo " Real-ESRGAN — finalizacao e teste"
echo "=============================================="

[ -x "$PY" ] || { echo "ERRO: falta $DIR/venv — rode instalar_upscale.sh"; exit 1; }

echo "[1/4] Baixando o script de upscale..."
curl -fsSL -o "$DIR/upscale.py" "$BASE/upscale_vps.py" \
  && echo "   upscale.py: $(stat -c%s "$DIR/upscale.py") bytes" \
  || { echo "ERRO: download falhou"; exit 1; }
"$PY" -c "import ast;ast.parse(open('$DIR/upscale.py').read())" \
  && echo "   sintaxe ok" || { echo "ERRO: script invalido"; exit 1; }

echo "[2/4] Preparando as 3 imagens de teste..."
mkdir -p "$DIR/teste/in" "$DIR/teste/out"
i=0
for pid in "618052-MLU69707909179_052023" "963142-MLU69707909181_052023" "843317-MLB76458348722_052024"; do
  i=$((i+1))
  alvo="$DIR/teste/in/img$i.jpg"
  if [ ! -s "$alvo" ]; then
    curl -fsSL -o "$alvo" "https://http2.mlstatic.com/D_${pid}-F.jpg" 2>/dev/null || true
  fi
done
for f in "$DIR"/teste/in/*.jpg; do
  [ -s "$f" ] && echo "   $(basename "$f"): $(stat -c%s "$f") bytes"
done

echo "[3/4] Criando a lista de tarefas..."
"$PY" - <<'PYEOF'
import glob, json, os
pasta = "/opt/upscale/teste/in"
saida = "/opt/upscale/teste/out"
itens = [
    {"id": os.path.basename(p),
     "entrada": p,
     "saida": os.path.join(saida, os.path.basename(p))}
    for p in sorted(glob.glob(os.path.join(pasta, "*.jpg")))
]
destino = "/opt/upscale/teste/tarefa.json"
os.makedirs(saida, exist_ok=True)
json.dump(itens, open(destino, "w"), ensure_ascii=False)
print(f"   {destino}: {len(itens)} imagem(ns)")
PYEOF

[ -s "$DIR/teste/tarefa.json" ] || { echo "ERRO: tarefa.json vazia"; exit 1; }

echo "[4/4] Rodando o teste (a 1a imagem demora ~40s carregando o modelo)..."
echo
time "$PY" "$DIR/upscale.py" "$DIR/teste/tarefa.json"

echo
echo "imagens geradas:"
ls -la "$DIR/teste/out/" 2>/dev/null | tail -4

echo
echo "=============================================="
echo " Se 'fidelidade >= 0.97' nas 3, o upscale e fiel."
echo " Amostras para comparar: $DIR/teste/out/"
echo "=============================================="
