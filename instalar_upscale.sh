#!/bin/bash
# Real-ESRGAN em CPU na VPS — instalacao e teste.
#
# DECISAO (15/11/2026, tomada pelo assistente a pedido do CEO):
#
#   METODO: Real-ESRGAN com o modelo RealESRGAN_x4plus, rodando em PYTORCH CPU.
#   TAMANHO FINAL: 1200 px no menor lado (recomendacao oficial do Mercado Livre).
#   TESTE: 3 imagens antes de qualquer lote grande.
#
# Por que PyTorch CPU e nao o binario ncnn-vulkan:
#   o binario portatil exige GPU com Vulkan; a VPS nao tem GPU. O PyTorch roda
#   em CPU puro: ~8-15 s por imagem, fiel ao original.
#
# Por que o modelo x4plus e nao o anime:
#   x4plus e treinado em fotos reais. Capa de livro e foto/arte com tipografia;
#   o modelo anime suaviza texto e inventa traco.
#
# Porque 1200 px:
#   o Mercado Livre pede 1200x1200 para a versao F. Acima disso ele reduz;
#   abaixo, ele mantem pequeno. 1200 e o melhor que a plataforma aceita.
set -u

DIR=/opt/upscale
PY=$DIR/venv/bin/python

echo "=============================================="
echo " Real-ESRGAN CPU — instalacao"
echo "=============================================="

echo "[1/6] Preparando Python..."
python3 --version || { echo "ERRO: sem python3"; exit 1; }
apt-get install -y -qq python3-venv python3-pip 2>/dev/null || true
mkdir -p "$DIR"
[ -x "$PY" ] || python3 -m venv "$DIR/venv"
echo "   venv: $("$PY" --version 2>&1)"

echo "[2/6] PyTorch CPU (download grande, 1-3 min)..."
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet \
  --index-url https://download.pytorch.org/whl/cpu \
  torch torchvision 2>&1 | tail -2
echo "   torch $("$PY" -c 'import torch;print(torch.__version__)')"

echo "[3/6] Bibliotecas do Real-ESRGAN..."
"$PY" -m pip install --quiet numpy pillow opencv-python-headless tqdm 2>&1 | tail -2
"$PY" -m pip install --quiet --no-deps \
  "git+https://github.com/xinntao/BasicSR.git@master" 2>&1 | tail -2 \
  || echo "   aviso: basicsr do git falhou"
"$PY" -c "import basicsr" 2>/dev/null || \
  "$PY" -m pip install --quiet basicsr 2>&1 | tail -2
"$PY" -m pip install --quiet --no-deps \
  "git+https://github.com/xinntao/Real-ESRGAN.git@master" 2>&1 | tail -2
"$PY" -c "from basicsr.archs.rrdbnet_arch import RRDBNet; print('   basicsr ok')" \
  || { echo "ERRO: basicsr nao importa"; exit 1; }

echo "[4/6] Baixando o modelo x4plus (64 MB)..."
mkdir -p "$DIR/weights"
if [ ! -s "$DIR/weights/RealESRGAN_x4plus.pth" ]; then
  curl -fsSL --retry 3 -o "$DIR/weights/RealESRGAN_x4plus.pth" \
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
fi
echo "   modelo: $(stat -c%s "$DIR/weights/RealESRGAN_x4plus.pth") bytes"

echo "[5/6] Escrevendo o script de lote..."
cat > "$DIR/upscale.py" <<'PYEOF'
"""Upscale de foto de anuncio — Real-ESRGAN x4plus em CPU.

Le uma lista de tarefas JSON, grava as imagens e mede FIDELIDADE de cada
saida: correlacao entre o original e a ampliada reduzida ao tamanho original.
Foi esse teste que reprovou o modelo generativo (0,835). Aqui esperamos > 0,97.

Uso: python upscale.py TAREFA.json
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from basicsr.archs.rrdbnet_arch import RRDBNet
from realesrgan import RealESRGANer

ALVO = 1200


def upscaler():
    model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23,
                    num_grow_ch=32, scale=4)
    return RealESRGANer(
        scale=4,
        model_path="/opt/upscale/weights/RealESRGAN_x4plus.pth",
        model=model, tile=192, tile_pad=8, pre_pad=0,
        half=False,
        device=torch.device("cpu"),
    )


def fidelidade(orig, saida):
    a = np.asarray(orig.convert("L"), dtype=np.float32)
    b = np.asarray(saida.convert("L").resize(orig.size), dtype=np.float32)
    ca = (a - a.mean()) / (a.std() + 1e-6)
    cb = (b - b.mean()) / (b.std() + 1e-6)
    return float((ca * cb).mean())


def main(tarefa):
    itens = json.loads(Path(tarefa).read_text())
    up = upscaler()
    print(f"modelo carregado · {len(itens)} imagem(ns)", flush=True)

    resultados = []
    for n, it in enumerate(itens, 1):
        origem = Path(it["entrada"])
        destino = Path(it["saida"])
        destino.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        try:
            img = Image.open(origem).convert("RGB")
            antes = img.size
            saida, _ = up.enhance(np.array(img), outscale=4)
            saida = Image.fromarray(saida)

            f = ALVO / min(saida.size)
            if f < 1:
                saida = saida.resize(
                    (round(saida.width * f), round(saida.height * f)),
                    Image.LANCZOS)
            saida.save(destino, "JPEG", quality=95, optimize=True)

            corr = fidelidade(img, saida)
            dur = time.time() - t0
            resultados.append({"item": it.get("id"), "ok": corr >= 0.97,
                               "fidelidade": round(corr, 4),
                               "antes": list(antes), "depois": list(saida.size),
                               "segundos": round(dur, 1)})
            marca = "OK" if corr >= 0.97 else "BAIXA FIDELIDADE"
            print(f"{n:3}. {str(it.get('id','?'))[:22]:22} "
                  f"{antes[0]}x{antes[1]} -> {saida.size[0]}x{saida.size[1]} "
                  f"| corr {corr:.4f} | {dur:.0f}s | {marca}", flush=True)
        except Exception as exc:
            resultados.append({"item": it.get("id"), "ok": False,
                               "erro": f"{type(exc).__name__}: {str(exc)[:120]}"})
            print(f"{n:3}. {it.get('id','?')} ERRO {type(exc).__name__}", flush=True)

    Path("/opt/upscale/resultado.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=1))
    bons = sum(1 for r in resultados if r.get("ok"))
    print()
    print(f"RESULTADO: {bons} de {len(resultados)} com fidelidade >= 0,97")
    print("detalhe: /opt/upscale/resultado.json")


if __name__ == "__main__":
    main(sys.argv[1])
PYEOF

echo "[6/6] Teste com 3 imagens reais..."
mkdir -p "$DIR/teste/in" "$DIR/teste/out"
i=0
for pid in "618052-MLU69707909179_052023" "963142-MLU69707909181_052023" "374-MLB3549474823"; do
  i=$((i+1))
  curl -fsSL -o "$DIR/teste/in/img$i.jpg" \
    "https://http2.mlstatic.com/D_${pid}-F.jpg" 2>/dev/null || true
done
ls -la "$DIR/teste/in/" 2>/dev/null | tail -4

"$PY" - <<'PYEOF'
import json, glob, os
itens = [{"id": os.path.basename(p), "entrada": p,
          "saida": f"/opt/upscale/teste/out/{os.path.basename(p)}"}
         for p in sorted(glob.glob("/opt/upscale/teste/in/*.jpg"))]
json.dump(itens, open("/opt/upscale/teste/tarefa.json", "w"))
print(f"tarefa com {len(itens)} imagens")
PYEOF

if [ -s "$DIR/teste/tarefa.json" ]; then
  time "$PY" "$DIR/upscale.py" "$DIR/teste/tarefa.json"
  echo
  echo "amostras para comparar: $DIR/teste/out/"
  ls -la "$DIR/teste/out/" 2>/dev/null | tail -4
fi

echo
echo "=============================================="
echo " PRONTO"
echo "   lote:  $PY /opt/upscale/upscale.py TAREFA.json"
echo "   saida: /opt/upscale/resultado.json"
echo "=============================================="
