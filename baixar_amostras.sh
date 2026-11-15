#!/bin/bash
# Publica as 3 amostras do upscale em /var/www para o assistente baixar e
# montar a comparacao antes/depois.
#
# Por que: as imagens estao em /opt/upscale/teste/out/ na VPS, e o assistente
# nao tem SSH. O Caddy ja serve /var/www/solivros em solivros.com.br, entao
# copiar para uma subpasta resolve sem abrir porta nem instalar nada.
set -u
D=/opt/upscale/teste/out
W=/var/www/solivros/amostras
mkdir -p "$W"

i=0
for f in img1.jpg img2.jpg img3.jpg; do
  i=$((i+1))
  if [ -s "$D/$f" ]; then
    cp "$D/$f" "$W/a$i.jpg"
    echo "  a$i.jpg <- $f ($(stat -c%s "$D/$f") bytes)"
  else
    echo "  AVISO: $D/$f nao existe"
  fi
done

chmod 644 "$W"/*.jpg 2>/dev/null

echo
echo "enderecos:"
for i in 1 2 3; do
  [ -s "$W/a$i.jpg" ] && echo "  https://solivros.com.br/amostras/a$i.jpg"
done
