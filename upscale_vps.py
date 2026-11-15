"""Upscale de fotos de anuncio — Real-ESRGAN x4plus em CPU.

Vai para /opt/upscale/upscale.py na VPS.

DETALHE CRITICO (verificado no codigo-fonte da biblioteca):
  RealESRGANer.enhance() espera a imagem em BGR e devolve em BGR — ela faz
  internamente cv2.cvtColor(img, COLOR_BGR2RGB) na entrada e devolve
  transposto para BGR na saida. Se eu passar RGB e ler como RGB, a imagem
  sai com VERMELHO e AZUL trocados. Por isso converto nas duas pontas.

Uso:
    python upscale.py TAREFA.json
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from basicsr.archs.rrdbnet_arch import RRDBNet
from PIL import Image
from realesrgan import RealESRGANer

PESOS = "/opt/upscale/weights/RealESRGAN_x4plus.pth"
ALVO = 1200
FIDELIDADE_MIN = 0.97


def construir():
    modelo = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23,
                     num_grow_ch=32, scale=4)
    return RealESRGANer(
        scale=4,
        model_path=PESOS,
        model=modelo,
        tile=200,
        tile_pad=10,
        pre_pad=0,
        half=False,
        device=torch.device("cpu"),
    )


def fidelidade(orig, saida):
    a = np.asarray(orig.convert("L"), dtype=np.float32)
    b = np.asarray(saida.convert("L").resize(orig.size), dtype=np.float32)
    ca = (a - a.mean()) / (a.std() + 1e-6)
    cb = (b - b.mean()) / (b.std() + 1e-6)
    return float((ca * cb).mean())


def processar(up, origem, destino):
    img = Image.open(origem).convert("RGB")
    antes = img.size

    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    saida_bgr, _ = up.enhance(arr, outscale=4)
    saida = Image.fromarray(cv2.cvtColor(saida_bgr, cv2.COLOR_BGR2RGB))

    f = ALVO / min(saida.size)
    if f < 1:
        saida = saida.resize((max(1, round(saida.width * f)),
                              max(1, round(saida.height * f))), Image.LANCZOS)

    destino.parent.mkdir(parents=True, exist_ok=True)
    saida.save(destino, "JPEG", quality=95, optimize=True)

    return {"ok": True, "fidelidade": round(fidelidade(img, saida), 4),
            "antes": list(antes), "depois": list(saida.size)}


def main(tarefa):
    itens = json.loads(Path(tarefa).read_text())
    print(f"carregando modelo... ({len(itens)} imagem(ns) na fila)", flush=True)
    up = construir()
    print("modelo pronto\n", flush=True)

    resultados = []
    for n, it in enumerate(itens, 1):
        origem = Path(it["entrada"])
        destino = Path(it["saida"])
        t0 = time.time()
        try:
            r = processar(up, origem, destino)
            dur = time.time() - t0
            r.update({"item": it.get("id"), "segundos": round(dur, 1)})
            marca = "OK" if r["fidelidade"] >= FIDELIDADE_MIN else "BAIXA FIDELIDADE"
            print(f"{n:3}. {str(it.get('id','?'))[:24]:24} "
                  f"{r['antes'][0]}x{r['antes'][1]} -> "
                  f"{r['depois'][0]}x{r['depois'][1]} | "
                  f"corr {r['fidelidade']:.4f} | {dur:.0f}s | {marca}", flush=True)
        except Exception as exc:
            r = {"item": it.get("id"), "ok": False,
                 "erro": f"{type(exc).__name__}: {str(exc)[:150]}"}
            print(f"{n:3}. {it.get('id','?')} ERRO {type(exc).__name__}: "
                  f"{str(exc)[:60]}", flush=True)
        resultados.append(r)

    Path("/opt/upscale/resultado.json").write_text(
        json.dumps(resultados, ensure_ascii=False, indent=1))

    bons = [r for r in resultados if r.get("ok")
            and r.get("fidelidade", 0) >= FIDELIDADE_MIN]
    print()
    print(f"RESULTADO: {len(bons)} de {len(resultados)} com fidelidade >= {FIDELIDADE_MIN}")
    if resultados and len(bons) == len(resultados):
        print("TODAS APROVADAS — pode seguir para o lote grande")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
