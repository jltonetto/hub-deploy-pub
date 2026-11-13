"""Diagnóstico: mostra a resposta CRUA do ML em /visits/items.

Roda no servidor com a venv do Hub, para ver exatamente o que a API devolve —
o código atual engole a diferença entre "zero visitas" e "não consegui ler".
"""
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

from hub.core.vault import Vault
from hub.db import connect
from hub.service import HubService
import hub.api as api_mod

conn = connect("/opt/hub-anuncios/contas/livrolandia/data/hub.db")
svc = HubService(conn, Vault.from_env(), api_mod.connector_factory)
acc = svc.accounts.get("mercadolivre", "livrolandia")
print("conta:", acc["alias"], "| token:", acc["token_status"])

conector = svc.connector_for(acc)
print("token ML:", "ok" if conector.access_token else "AUSENTE")
print()

rows = list(conn.execute(
    "SELECT item_id, sold_quantity FROM listings WHERE account_id=? "
    "AND sold_quantity > 0 LIMIT 3", (int(acc["id"]),)))
ids = [r["item_id"] for r in rows]
print("itens com venda (devem ter visita):")
for r in rows:
    print(f"   {r['item_id']} vendas={r['sold_quantity']}")
print()

print("--- 1) metodo do conector ---")
try:
    print("visits_by_items():", conector.visits_by_items(ids))
except Exception as e:
    print("ERRO:", type(e).__name__, str(e)[:300])
print()

print("--- 2) chamada CRUA (sem tratamento) ---")
try:
    cru = conector.http.request("GET", "/visits/items",
                                token=conector.access_token,
                                params={"ids": ",".join(ids)})
    print("tipo:", type(cru).__name__)
    print("conteudo:", json.dumps(cru, ensure_ascii=False)[:1000])
except Exception as e:
    print("ERRO:", type(e).__name__, str(e)[:500])
print()

print("--- 3) um item de cada vez ---")
for i in ids[:2]:
    try:
        cru1 = conector.http.request("GET", "/visits/items",
                                     token=conector.access_token,
                                     params={"ids": i})
        print(f"  {i}: {json.dumps(cru1, ensure_ascii=False)[:200]}")
    except Exception as e:
        print(f"  {i}: ERRO {type(e).__name__} {str(e)[:150]}")
