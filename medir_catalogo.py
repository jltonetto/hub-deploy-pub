"""O catalogo ajuda? Mede por IDENTIDADE e por TAMANHO, nao por suposicao.

POR QUE ESTE SCRIPT EXISTE:
  o teste anterior (15/11) devolveu "0px" em 60 anuncios. O debug mostrou que
  a resposta de /products/{id} nao traz as fotos no campo que eu lia -- e que,
  em muitos casos, o anuncio JA USA a foto do catalogo (mesmo id, ex.: MLA...).

  A pergunta certa nao e "o catalogo tem foto?", e:
     a foto do catalogo e DIFERENTE da do anuncio e MAIOR?

  So nesse caso vale trocar. Se for a mesma, trocar nao muda nada.

Roda na VPS:
    cd /opt/hub-anuncios && .venv/bin/python /root/medir_catalogo.py
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

from hub.core.vault import Vault          # noqa: E402
from hub.db import connect               # noqa: E402
from hub.service import HubService       # noqa: E402
import hub.api as api_mod                # noqa: E402

MIN = 500
LIMITE = 200


def dim(txt):
    try:
        w, h = str(txt).lower().split("x")
        return min(int(w), int(h))
    except Exception:
        return 0


def extrair_fotos(prod):
    """Procura as fotos em QUALQUER campo plausivel da resposta do /products."""
    fotos = []
    for campo in ("pictures", "main_pictures", "images", "variations_pictures"):
        valor = prod.get(campo)
        if isinstance(valor, list):
            for pic in valor:
                if isinstance(pic, dict):
                    fotos.append(pic)
    return fotos


def main():
    conn = connect("/opt/hub-anuncios/contas/livrolandia/data/hub.db")
    svc = HubService(conn, Vault.from_env(), api_mod.connector_factory)
    acc = svc.accounts.get("mercadolivre", "livrolandia")
    con = svc.connector_for(acc)

    rows = list(conn.execute(
        """SELECT item_id, title, catalog_product_id, payload
             FROM listings
            WHERE account_id = ? AND catalog_product_id IS NOT NULL
            ORDER BY computed_score ASC
            LIMIT ?""", (int(acc["id"]), LIMITE)))
    print(f"anuncios de catalogo a analisar: {len(rows)}", flush=True)

    mesma_foto = 0
    catalogo_melhor = 0
    catalogo_pior = 0
    sem_foto_catalogo = 0
    analisados = 0
    detalhes = []

    for n, r in enumerate(rows, 1):
        try:
            p = json.loads(r["payload"] or "{}")
        except Exception:
            continue
        pics_anuncio = (p.get("raw") or {}).get("pictures") or []
        if not pics_anuncio:
            continue
        menor_anuncio = min(dim(x.get("max_size")) for x in pics_anuncio)
        if menor_anuncio >= MIN:
            continue

        analisados += 1
        ids_anuncio = {str(x.get("id")) for x in pics_anuncio if x.get("id")}

        try:
            prod = con.http.request(
                "GET", f"/products/{r['catalog_product_id']}",
                token=con.access_token) or {}
        except Exception as exc:
            detalhes.append({"item": r["item_id"], "erro": str(exc)[:80]})
            sem_foto_catalogo += 1
            continue

        fotos_cat = extrair_fotos(prod)
        if not fotos_cat:
            sem_foto_catalogo += 1
            detalhes.append({"item": r["item_id"],
                             "caso": "produto sem campo de foto",
                             "campos": [k for k in prod.keys()
                                        if "pic" in k or "imag" in k]})
            continue

        novas = [x for x in fotos_cat if str(x.get("id")) not in ids_anuncio]
        if not novas:
            mesma_foto += 1
            detalhes.append({"item": r["item_id"], "caso": "mesma foto",
                             "anuncio_px": menor_anuncio})
            continue

        melhor = max(novas, key=lambda x: max(dim(x.get("max_size")),
                                             dim(x.get("size"))))
        px_novo = max(dim(melhor.get("max_size")), dim(melhor.get("size")))
        if px_novo > menor_anuncio:
            catalogo_melhor += 1
            detalhes.append({"item": r["item_id"], "caso": "catalogo MELHOR",
                             "anuncio_px": menor_anuncio, "catalogo_px": px_novo,
                             "url": melhor.get("secure_url") or melhor.get("url")})
        else:
            catalogo_pior += 1
            detalhes.append({"item": r["item_id"], "caso": "catalogo pior/igual",
                             "anuncio_px": menor_anuncio, "catalogo_px": px_novo})

        if n % 25 == 0:
            print(f"  {n}/{len(rows)} mesmo {mesma_foto} melhor "
                  f"{catalogo_melhor} pior {catalogo_pior} "
                  f"sem_foto {sem_foto_catalogo}", flush=True)

    print()
    print("=" * 62)
    print("O CATALOGO AJUDA?  (resultado medido)")
    print("=" * 62)
    print(f"anuncios analisados        : {analisados}")
    print()
    print(f"  mesma foto (nada a fazer) : {mesma_foto}")
    print(f"  catalogo MELHOR           : {catalogo_melhor}")
    print(f"  catalogo pior ou igual    : {catalogo_pior}")
    print(f"  produto sem campo de foto : {sem_foto_catalogo}")
    print()
    if analisados:
        pct = catalogo_melhor * 100 // analisados
        print(f"  => o catalogo resolve {pct}% destes anuncios")
        print(f"  => {analisados - catalogo_melhor} precisam de outra fonte")
    print()

    os.makedirs("/opt/upscale", exist_ok=True)
    json.dump(detalhes, open("/opt/upscale/medicao-catalogo.json", "w"),
              ensure_ascii=False, indent=1)
    print("detalhe: /opt/upscale/medicao-catalogo.json")

    sem_campo = [d for d in detalhes if d.get("caso") == "produto sem campo de foto"]
    if sem_campo:
        print()
        print("campos com 'pic'/'imag' nos que falharam:")
        amostra = sem_campo[0].get("campos") or []
        print("  ", amostra if amostra else "(nenhum - a resposta nao tem foto)")


if __name__ == "__main__":
    main()
