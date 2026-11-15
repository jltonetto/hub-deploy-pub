"""Teste DEFINITIVO do Qwen-Image nas 3 capas.

CONTEXTO (16/11/2026):
  Oito tentativas falharam por um motivo so: o TEXTO da capa fica ilegivel.
  Este script testa a nona via -- Qwen-Image, o unico modelo que declara
  preservar texto em edicao.

  A LICAO DAS OITO FALHAS: metrica nenhuma substituiu o olho do CEO. A
  correlacao global aprovou o Real-ESRGAN com 0,9936 enquanto as letras
  viravam pseudo-letras; o SSIM deu 0,99 na interpolacao e o texto continuou
  ruim. Por isso: gera a imagem, salva em TAMANHO REAL, publica para ele
  abrir, e so entao mostra os numeros como apoio -- nunca como veredito.

COMO RODAR
  export DASHSCOPE_API_KEY="sk-..."
  export DASHSCOPE_HOST="ws-xxxx.ap-southeast-1.maas.aliyuncs.com"
  python3 testar_qwen_final.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from PIL import Image

RAIZ = Path("/home/ethos/.assistant/workspace")
SAIDA = RAIZ / "work/qwen-teste"
SAIDA.mkdir(parents=True, exist_ok=True)

ALVO = 1024

MODELOS = [
    "qwen-image-2.0",
    "qwen-image-3.0",
]

CAPAS = [
    ("capa1", "https://http2.mlstatic.com/D_618052-MLU69707909179_052023-F.jpg"),
    ("capa2", "https://http2.mlstatic.com/D_963142-MLU69707909181_052023-F.jpg"),
    ("capa3", "https://http2.mlstatic.com/D_843317-MLB76458348722_052024-F.jpg"),
]

PROMPT = (
    "Upscale and improve the quality of this book cover image. "
    "CRITICAL REQUIREMENT: preserve every single letter, word and number "
    "EXACTLY as they appear in the original. Do not translate. Do not "
    "paraphrase. Do not redraw the typography. Do not change the font style. "
    "Do not add any text. Do not remove any text. "
    "The text must remain perfectly readable and identical to the original, "
    "character by character. "
    "Only improve sharpness, remove blur and pixelation, and increase resolution."
)


def baixar(url, destino):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    im.save(destino, "JPEG", quality=95)
    return im


def chamar(chave, host, modelo, img_b64):
    corpo = {
        "model": modelo,
        "input": {"messages": [{"role": "user", "content": [
            {"image": f"data:image/jpeg;base64,{img_b64}"},
            {"text": PROMPT},
        ]}]},
        "parameters": {"size": "1024*1024", "n": 1, "watermark": False},
    }
    t0 = time.time()
    r = requests.post(
        f"https://{host}/api/v1/services/aigc/multimodal-generation/generation",
        headers={"Authorization": f"Bearer {chave}",
                 "Content-Type": "application/json"},
        json=corpo, timeout=300)
    dur = time.time() - t0

    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")

    d = r.json()
    if d.get("code"):
        raise RuntimeError(f"{d['code']}: {d.get('message', '')[:200]}")

    try:
        conteudo = d["output"]["choices"][0]["message"]["content"]
        for item in conteudo:
            if isinstance(item, dict) and item.get("image"):
                return item["image"], dur
    except (KeyError, IndexError, TypeError):
        pass
    raise RuntimeError(f"resposta sem imagem: {json.dumps(d)[:300]}")


def ajustar(im):
    f = ALVO / min(im.size)
    if abs(f - 1) < 0.01:
        return im
    return im.resize((round(im.width * f), round(im.height * f)), Image.LANCZOS)


def publicar(pastas):
    destino = Path("/var/www/solivros/testes/qwen")
    try:
        destino.mkdir(parents=True, exist_ok=True)
        for pasta in pastas:
            for f in pasta.glob("*.jpg"):
                alvo = destino / f.name
                alvo.write_bytes(f.read_bytes())
                subprocess.run(["chmod", "644", str(alvo)], check=False)
        print(f"  publicado em {destino}")
    except Exception as exc:
        print(f"  (nao publiquei: {type(exc).__name__} -- fora da VPS)")


def main():
    chave = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    host = os.environ.get("DASHSCOPE_HOST", "").strip()
    if not chave or not host:
        print("defina DASHSCOPE_API_KEY e DASHSCOPE_HOST")
        sys.exit(1)

    print(f"host: {host}")
    print(f"alvo: {ALVO}px no menor lado\n")

    relatorio = []
    pastas_publicar = []

    for modelo in MODELOS:
        pasta = SAIDA / modelo.replace(".", "-")
        pasta.mkdir(exist_ok=True)
        pastas_publicar.append(pasta)
        print(f"=== {modelo} ===")

        for nome, url in CAPAS:
            antes = pasta / f"{nome}-antes.jpg"
            depois = pasta / f"{nome}-depois.jpg"
            try:
                orig = baixar(url, antes)
            except Exception as exc:
                print(f"  {nome}: download falhou ({type(exc).__name__})")
                continue

            b64 = base64.b64encode(antes.read_bytes()).decode()
            try:
                url_out, dur = chamar(chave, host, modelo, b64)
            except Exception as exc:
                print(f"  {nome}: API falhou -- {str(exc)[:120]}")
                relatorio.append({"modelo": modelo, "capa": nome,
                                  "erro": str(exc)[:150]})
                continue

            try:
                r2 = requests.get(url_out, timeout=120)
                novo = Image.open(io.BytesIO(r2.content)).convert("RGB")
                novo = ajustar(novo)
                novo.save(depois, "JPEG", quality=95, optimize=True)
            except Exception as exc:
                print(f"  {nome}: resultado nao baixou ({type(exc).__name__})")
                continue

            relatorio.append({
                "modelo": modelo, "capa": nome, "ok": True,
                "antes": list(orig.size), "depois": list(novo.size),
                "segundos": round(dur, 1),
                "antes_arquivo": str(antes.relative_to(RAIZ)),
                "depois_arquivo": str(depois.relative_to(RAIZ)),
            })
            print(f"  {nome}: {orig.size[0]}x{orig.size[1]} -> "
                  f"{novo.size[0]}x{novo.size[1]} ({dur:.0f}s)")

    (SAIDA / "resultado-final.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=1))

    publicar(pastas_publicar)

    feitos = [r for r in relatorio if r.get("ok")]
    print()
    print("=" * 60)
    print(f"geradas: {len(feitos)} de {len(CAPAS) * len(MODELOS)}")
    print(f"arquivos: {SAIDA}")
    print()
    print("AGORA O QUE IMPORTA: abrir as imagens e OLHAR o texto.")
    print("Metrica nao decide. So o olho.")
    for r in feitos:
        print(f"  {r['modelo']}/{r['capa']}-depois.jpg")


if __name__ == "__main__":
    main()
