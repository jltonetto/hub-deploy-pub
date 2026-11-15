"""Teste: a foto do CATALOGO e melhor que a do anuncio?

Roda na VPS, onde esta o token do Mercado Livre.

O QUE ESTE TESTE RESPONDE (pedido do CEO, 15/11/2026):
  ele apontou que o upscale por IA distorce os caracteres das capas.
  A primeira fonte da cascata e o catalogo do proprio ML: se a foto oficial
  do produto tem resolucao maior, ela resolve sem reconstrucao nenhuma --
  nenhuma letra inventada, porque a imagem ja existe.

Saida: /opt/upscale/teste-catalogo/resultado.json + imagens baixadas
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/opt/hub-anuncios")
os.chdir("/opt/hub-anuncios")

for linha in open("/opt/hub-anuncios/.secrets/hub.env"):
    linha = linha.strip()
    if linha and not linha.startswith("#") and "=" in linha:
        k, v = linha.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

import urllib.request                          # noqa: E402
from hub.core.vault import Vault               # noqa: E402
from hub.db import connect                     # noqa: E402
from hub.service import HubService             # noqa: E402
import hub.api as api_mod                      # noqa: E402

SAIDA = "/opt/upscale/teste-catalogo"
MIN = 500


def dim(txt):
    try:
        w, h = str(txt).lower().split("x")
        return min(int(w), int(h))
    except Exception:
        return 0


def baixar(url, destino):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        dados = r.read()
    open(destino, "wb").write(dados)
    return len(dados)


def main():
    os.makedirs(SAIDA, exist_ok=True)
    conn = connect("/opt/hub-anuncios/contas/livrolandia/data/hub.db")
    svc = HubService(conn, Vault.from_env(), api_mod.connector_factory)
    acc = svc.accounts.get("mercadolivre", "livrolandia")
    con = svc.connector_for(acc)

    rows = list(conn.execute(
        """SELECT l.item_id, l.title, l.catalog_product_id, l.payload
             FROM listings l
            WHERE l.account_id = ? AND l.catalog_product_id IS NOT NULL
            ORDER BY l.computed_score ASC
            LIMIT 60""", (int(acc["id"]),)))
    print(f"anuncios de catalogo para testar: {len(rows)}", flush=True)

    resultados = []
    achou_melhor = 0

    for n, r in enumerate(rows, 1):
        try:
            p = json.loads(r["payload"] or "{}")
        except Exception:
            continue
        pics = (p.get("raw") or {}).get("pictures") or []
        if not pics:
            continue
        menor_anuncio = min(dim(x.get("max_size")) for x in pics)
        if menor_anuncio >= MIN:
            continue

        try:
            prod = con.http.request("GET", f"/products/{r['catalog_product_id']}",
                                    token=con.access_token) or {}
        except Exception as exc:
            resultados.append({"item": r["item_id"], "erro": str(exc)[:90]})
            continue

        melhor_url, melhor_px = "", 0
        for pic in (prod.get("pictures") or []):
            px = max(dim(pic.get("max_size")), dim(pic.get("size")))
            if px > melhor_px:
                melhor_px = px
                melhor_url = pic.get("secure_url") or pic.get("url") or ""

        achou = melhor_px > menor_anuncio
        if achou:
            achou_melhor += 1
            try:
                baixar(melhor_url, f"{SAIDA}/{r['item_id']}_catalogo.jpg")
            except Exception:
                pass

        resultados.append({
            "item": r["item_id"],
            "titulo": (r["title"] or "")[:50],
            "anuncio_px": menor_anuncio,
            "catalogo_px": melhor_px,
            "ganho": melhor_px - menor_anuncio,
            "melhor": achou,
            "url": melhor_url,
        })
        print(f"{n:2}. {r['item_id']} anuncio {menor_anuncio}px -> "
              f"catalogo {melhor_px}px {'MELHOR' if achou else 'igual/pior'}",
              flush=True)

    json.dump(resultados, open(f"{SAIDA}/resultado.json", "w"),
              ensure_ascii=False, indent=1)

    analisados = [x for x in resultados if "anuncio_px" in x]
    print()
    print("=" * 55)
    print(f"analisados            : {len(analisados)}")
    print(f"catalogo MELHOR       : {achou_melhor}")
    print(f"catalogo igual/pior   : {len(analisados) - achou_melhor}")
    if analisados:
        media_an = sum(x["anuncio_px"] for x in analisados) / len(analisados)
        media_cat = sum(x["catalogo_px"] for x in analisados) / len(analisados)
        print(f"media anuncio         : {media_an:.0f}px")
        print(f"media catalogo        : {media_cat:.0f}px")
    print(f"imagens em            : {SAIDA}/")


if __name__ == "__main__":
    main()
