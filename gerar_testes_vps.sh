#!/bin/bash
# Gera e publica as amostras dos melhores upscalers locais para revisao do CEO.
#
# Roda na VPS (onde estao as 3 capas em /opt/upscale/teste/in).
# Publica em solivros.com.br/testes/ para o assistente baixar.
#
# TECNICAS (todas nativas, sem GPU, sem API paga):
#   a-lanczos   referencia: o melhor interpolador simples
#   g-linear    resize em espaco linear (gamma correto) -- maior acutancia
#   f-kaiser    janela Kaiser: menos ringing que Lanczos
#   c-jinc      Jinc + leve unsharp
#
# Alvo: 1024 px no MENOR lado (pedido do CEO).
set -u

IN=/opt/upscale/teste/in
OUT=/var/www/solivros/testes
ALVO=1024

command -v magick >/dev/null 2>&1 || { echo "ERRO: ImageMagick nao instalado"; exit 1; }
mkdir -p "$OUT"/{antes,lanczos,linear,kaiser,jinc}

echo "=== gerando e publicando ==="
i=0
for f in "$IN"/img1.jpg "$IN"/img2.jpg "$IN"/img3.jpg; do
  [ -s "$f" ] || { echo "  falta $f"; continue; }
  i=$((i+1))

  cp "$f" "$OUT/antes/capa$i.jpg"

  magick "$f" -filter Lanczos -resize "x${ALVO}<" -resize "${ALVO}x<" \
    -quality 95 "$OUT/lanczos/capa$i.jpg"

  magick "$f" -colorspace RGB -filter Lanczos -resize "x${ALVO}<" \
    -resize "${ALVO}x<" -colorspace sRGB -quality 95 "$OUT/linear/capa$i.jpg"

  magick "$f" -filter Kaiser -resize "x${ALVO}<" -resize "${ALVO}x<" \
    -quality 95 "$OUT/kaiser/capa$i.jpg"

  magick "$f" -filter Jinc -resize "x${ALVO}<" -resize "${ALVO}x<" \
    -unsharp 0x0.75+0.55+0.008 -quality 95 "$OUT/jinc/capa$i.jpg"

  echo "  capa$i: $(identify -format '%wx%h' "$OUT/linear/capa$i.jpg" 2>/dev/null)"
done

chmod -R 644 "$OUT"/*/*.jpg 2>/dev/null
chmod -R 755 "$OUT" 2>/dev/null

echo
echo "=== links ==="
for tec in antes lanczos linear kaiser jinc; do
  for n in 1 2 3; do
    [ -s "$OUT/$tec/capa$n.jpg" ] && \
      echo "https://solivros.com.br/testes/$tec/capa$n.jpg"
  done
done
