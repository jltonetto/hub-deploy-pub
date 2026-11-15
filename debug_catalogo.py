"""Diagnostico: por que /products/{id} devolveu 0px?

O teste do catalogo (15/11/2026) rodou 60 anuncios e todos deram
"catalogo 0px igual/pior". Isso NAO significa que o catalogo e ruim:
significa que o script nao conseguiu LER as fotos da resposta.

Hipoteses:
  1. a chave das fotos nao e "pictures"
  2. "max_size" e "size" nao existem nessa resposta
  3. a chamada falhou e o except engoliu (mesmo padrao do bug de 13/11)

Este script imprime a resposta CRUA do primeiro produto encontrado.
Sem interpretacao: o JSON como o Mercado Livre devolve.

Roda na VPS:
    cd /opt/hub-anuncios && .venv/bin/python /root/debug_catalogo.py
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


def encurtar(obj, nivel=0):
    """Imprime o JSON cortando URLs longas, para caber na tela."""
    pad = "  " * nivel
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}:")
                encurtar(v, nivel + 1)
            else:
                txt = str(v)
                if txt.startswith("http"):
                    txt = txt[:70] + "..."
                print(f"{pad}{k}: {txt}")
    elif isinstance(obj, list):
        print(f"{pad}[{len(obj)} item(ns)]")
        if obj:
            encurtar(obj[0], nivel + 1)


def main():
    conn = connect("/opt/hub-anuncios/contas/livrolandia/data/hub.db")
    svc = HubService(conn, Vault.from_env(), api_mod.connector_factory)
    acc = svc.accounts.get("mercadolivre", "livrolandia")
    con = svc.connector_for(acc)

    row = conn.execute(
        """SELECT item_id, catalog_product_id FROM listings
            WHERE account_id = ? AND catalog_product_id IS NOT NULL
            LIMIT 1""", (int(acc["id"]),)).fetchone()
    if not row:
        print("nenhum anuncio com catalog_product_id")
        return

    pid = row["catalog_product_id"]
    print(f"item: {row['item_id']}")
    print(f"catalog_product_id: {pid}")
    print()

    print("=" * 60)
    print("GET /products/{id}  —  resposta crua")
    print("=" * 60)
    try:
        prod = con.http.request("GET", f"/products/{pid}",
                                token=con.access_token)
        print("tipo:", type(prod).__name__)
        if isinstance(prod, dict):
            print("chaves de topo:", list(prod.keys()))
            print()
            fotos = prod.get("pictures")
            print(f"campo 'pictures': {type(fotos).__name__} com "
                  f"{len(fotos) if isinstance(fotos, list) else 'n/d'} item(ns)")
            if isinstance(fotos, list) and fotos:
                print()
                print("primeira foto (estrutura completa):")
                for k, v in fotos[0].items():
                    txt = str(v)
                    if txt.startswith("http"):
                        txt = txt[:80] + "..."
                    print(f"    {k}: {txt}")
            print()
            print("--- resumo do produto ---")
            encurtar({k: v for k, v in prod.items() if k != "pictures"})
    except Exception as exc:
        print("ERRO:", type(exc).__name__, str(exc)[:300])

    print()
    print("=" * 60)
    print("comparacao: fotos do ANUNCIO (o que o script usa hoje)")
    print("=" * 60)
    r2 = conn.execute(
        "SELECT payload FROM listings WHERE item_id = ?",
        (row["item_id"],)).fetchone()
    p = json.loads(r2["payload"] or "{}")
    pics = (p.get("raw") or {}).get("pictures") or []
    print(f"fotos no anuncio: {len(pics)}")
    if pics:
        print("primeira foto do anuncio:")
        for k in ("id", "size", "max_size", "quality"):
            if k in pics[0]:
                print(f"    {k}: {pics[0][k]}")


if __name__ == "__main__":
    main()
