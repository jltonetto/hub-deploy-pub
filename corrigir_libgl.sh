#!/bin/bash
# Conserta o libGL e retoma a instalacao do Real-ESRGAN.
#
# O QUE QUEBROU (15/11/2026):
#   ImportError: libGL.so.1: cannot open shared object file
#
# CAUSA: o `basicsr` declara `opencv-python` como dependencia, e o pip
# reinstalou a versao COM interface grafica por cima da `headless`. A versao
# completa carrega libGL, que nao existe num Ubuntu Server (sem tela).
#
# CORRECAO em duas frentes:
#   1. instala libgl1 e libglib2.0-0 no sistema (resolve o import);
#   2. forca a versao headless por cima, para nao voltar a acontecer.
#
# O PyTorch NAO e reinstalado: ja esta pronto (2.15.0+cpu).
set -u

DIR=/opt/upscale
PY=$DIR/venv/bin/python

echo "=============================================="
echo " Real-ESRGAN — correcao do libGL"
echo "=============================================="

echo "[1/5] Instalando libs graficas do sistema..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq 2>/dev/null || true
apt-get install -y -qq libgl1 libglib2.0-0 2>&1 | tail -3
echo "   libGL: $(ldconfig -p 2>/dev/null | grep -c libGL.so.1) entrada(s)"

echo "[2/5] Forcando OpenCV headless (sem interface grafica)..."
"$PY" -m pip uninstall -y -q opencv-python opencv-contrib-python 2>/dev/null || true
"$PY" -m pip install --quiet --force-reinstall --no-deps \
  opencv-python-headless 2>&1 | tail -2
echo "   opencv: $("$PY" -c 'import cv2;print(cv2.__version__)' 2>&1 | head -1)"

echo "[3/5] Conferindo imports..."
"$PY" -c "
import cv2, torch
from basicsr.archs.rrdbnet_arch import RRDBNet
print('   cv2', cv2.__version__)
print('   torch', torch.__version__)
print('   RRDBNet ok')
" || { echo "ERRO: imports falharam"; exit 1; }

"$PY" -c "import realesrgan; print('   realesrgan ok')" 2>/dev/null || \
  "$PY" -m pip install --quiet --no-deps \
    "git+https://github.com/xinntao/Real-ESRGAN.git@master" 2>&1 | tail -2

echo "[4/5] Conferindo o modelo..."
ls -la "$DIR/weights/RealESRGAN_x4plus.pth" 2>/dev/null || {
  mkdir -p "$DIR/weights"
  curl -fsSL --retry 3 -o "$DIR/weights/RealESRGAN_x4plus.pth" \
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
}

echo "[5/5] Teste com 3 imagens reais..."
mkdir -p "$DIR/teste/in" "$DIR/teste/out"
i=0
for pid in "618052-MLU69707909179_052023" "963142-MLU69707909181_052023" "843317-MLB76458348722_052024"; do
  i=$((i+1))
  curl -fsSL -o "$DIR/teste/in/img$i.jpg" \
    "https://http2.mlstatic.com/D_${pid}-F.jpg" 2>/dev/null || true
done
ls -la "$DIR/teste/in/" 2>/dev/null | tail -4

[ -f "$DIR/upscale.py" ] || { echo "ERRO: falta $DIR/upscale.py — rode o instalador de novo"; exit 1; }

"$PY" - <<'PYEOF'
import json, glob, os
itens = [{"id": os.path.basename(p), "entrada": p,
          "saida": f"/opt/upscale/teste/out/{os.path.basename(p)}"}
         for p in sorted(glob.glob("/opt/upscale/teste/in/*.jpg"))]
json.dump(itens, open("/opt/upscale/teste/tarefa.json", "w"))
print(f"tarefa com {len(itens)} imagens")
PYEOF

time "$PY" "$DIR/upscale.py" "$DIR/teste/tarefa.json"

echo
echo "amostras: $DIR/teste/out/"
ls -la "$DIR/teste/out/" 2>/dev/null | tail -4

echo
echo "=============================================="
echo " PRONTO"
echo "   lote:  $PY /opt/upscale/upscale.py TAREFA.json"
echo "=============================================="
