#!/bin/bash
# Gera e publica as amostras dos melhores upscalers locais para revisao do CEO.
#
# Roda na VPS. Publica em solivros.com.br/testes/ para o CEO abrir no navegador.
#
# POR QUE ESTE SCRIPT EXISTE
#   O catalogo do Mercado Livre foi medido e descartado (0 de 200 tem foto
#   melhor). O Real-ESRGAN distorceu o texto. Restaram os interpoladores
#   nativos -- que nao inventam letra porque nao usam IA.
#
# TECNICAS COMPARADAS (todas sem GPU, sem API, sem mensalidade):
#   lanczos  referencia classica
#   linear   resize em espaco linear (gamma correto) -- corrige o escurecimento
#            de borda que a interpolacao em sRGB causa; maior acutancia no teste
#   kaiser   janela Kaiser: menos ringing que Lanczos
#   jinc     Jinc + unsharp conservador (controle: deve ficar pior)
#
# Alvo: 1024 px no MENOR lado.
set -u

IN=/opt/upscale/teste/in
OUT=/var/www/solivros/testes
ALVO=1024

# ---- 1. garante o ImageMagick ----
if ! command -v magick >/dev/null 2>&1 && ! command -v convert >/dev/null 2>&1; then
  echo "ImageMagick nao encontrado -- instalando..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq 2>/dev/null
  apt-get install -y -qq imagemagick 2>&1 | tail -2
fi

if command -v magick >/dev/null 2>&1; then
  IM=magick
elif command -v convert >/dev/null 2>&1; then
  IM=convert
else
  echo "ERRO: nao consegui instalar o ImageMagick."
  echo "Rode manualmente: apt-get install -y imagemagick"
  exit 1
fi
echo "usando: $IM"

# ---- 2. confere as origens ----
if [ ! -d "$IN" ]; then
  echo "ERRO: faltam as capas em $IN"
  exit 1
fi

mkdir -p "$OUT"/antes "$OUT"/lanczos "$OUT"/linear "$OUT"/kaiser "$OUT"/jinc

echo
echo "=== gerando ==="
i=0
for f in "$IN"/img1.jpg "$IN"/img2.jpg "$IN"/img3.jpg; do
  [ -s "$f" ] || { echo "  falta $f"; continue; }
  i=$((i+1))

  cp "$f" "$OUT/antes/capa$i.jpg"

  $IM "$f" -filter Lanczos -resize "x${ALVO}<" -resize "${ALVO}x<" \
    -quality 95 "$OUT/lanczos/capa$i.jpg"

  $IM "$f" -colorspace RGB -filter Lanczos -resize "x${ALVO}<" \
    -resize "${ALVO}x<" -colorspace sRGB -quality 95 "$OUT/linear/capa$i.jpg"

  $IM "$f" -filter Kaiser -resize "x${ALVO}<" -resize "${ALVO}x<" \
    -quality 95 "$OUT/kaiser/capa$i.jpg"

  $IM "$f" -filter Jinc -resize "x${ALVO}<" -resize "${ALVO}x<" \
    -unsharp 0x0.75+0.55+0.008 -quality 95 "$OUT/jinc/capa$i.jpg"

  echo "  capa$i ok"
done

chmod -R 755 "$OUT" 2>/dev/null
chmod -R 644 "$OUT"/*/*.jpg 2>/dev/null

echo
echo "=== LINKS PARA REVISAO ==="
for tec in antes lanczos linear kaiser jinc; do
  for n in 1 2 3; do
    [ -s "$OUT/$tec/capa$n.jpg" ] && \
      echo "https://solivros.com.br/testes/$tec/capa$n.jpg"
  done
  echo
done
