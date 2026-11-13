"""Patch v2 — corrige a leitura de visitas do Mercado Livre.

O QUE CORRIGE (descoberto em produção em 13/11/2026):
  1. /visits/items aceita UM item por chamada. O código pedia 100 e recebia
     400 "maximum amount of items to query is 1" — o except engolia o erro e
     tudo virava zero.
  2. Falha de leitura agora NÃO grava zero: registra erro e deixa NULL.
  3. sort=trafego e sort=nao_convertem passam a ser aceitos pelo painel.

ESTRATÉGIA: substitui a função inteira pelo nome, sem tocar no resto do
arquivo. Se a função não for encontrada, aborta sem alterar nada.
"""
import ast
import os
import re
import shutil
from datetime import datetime

DEST = "/opt/hub-anuncios"
AQUI = os.path.dirname(os.path.abspath(__file__))


def ler(c):
    with open(c, encoding="utf-8") as f:
        return f.read()


def gravar(c, t):
    with open(c, "w", encoding="utf-8") as f:
        f.write(t)


def substituir_funcao(texto, nome, novo_corpo):
    """Troca o corpo da função `nome` (método de classe, 4 espaços de recuo)."""
    inicio = texto.find(f"    def {nome}(")
    if inicio < 0:
        raise RuntimeError(f"função {nome} não encontrada")
    padrao = re.compile(r"\n    (?:def |# )", re.MULTILINE)
    m = padrao.search(texto, inicio + 5)
    fim = m.start() if m else len(texto)
    return texto[:inicio] + novo_corpo.rstrip() + "\n\n" + texto[fim:].lstrip("\n")


def main():
    svc = os.path.join(DEST, "hub/service.py")
    con = os.path.join(DEST, "hub/connectors/mercadolivre.py")
    api = os.path.join(DEST, "hub/api.py")

    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"/root/hub-backup-{carimbo}"
    os.makedirs(backup, exist_ok=True)
    for f in (svc, con, api):
        shutil.copyfile(f, os.path.join(backup, os.path.basename(f)))
    print(f"   backup em {backup}")

    # ---- 1. conector: visits_by_items um item por chamada ----
    novo_visits = ler(os.path.join(AQUI, "fix_visits.py"))
    texto = ler(con)
    if "maximum amount of items" in texto:
        print("   mercadolivre.py: ja corrigido")
    else:
        gravar(con, substituir_funcao(texto, "visits_by_items", novo_visits))
        print("   mercadolivre.py: visits_by_items corrigido")

    # ---- 2. service: coletar_visitas sem zero falso ----
    novo_coletar = ler(os.path.join(AQUI, "fix_coletar.py"))
    texto = ler(svc)
    if "NÃO grava zero" in texto:
        print("   service.py: ja corrigido")
    else:
        gravar(svc, substituir_funcao(texto, "coletar_visitas", novo_coletar))
        print("   service.py: coletar_visitas corrigido")

    # ---- 3. api: sort aceita trafego ----
    texto = ler(api)
    velho = 'pattern=r"^(worst|best|az|editados)$"'
    novo = 'pattern=r"^(worst|best|az|editados|trafego|nao_convertem)$"'
    if "trafego|nao_convertem" in texto:
        print("   api.py: ja corrigido")
    elif texto.count(velho) == 1:
        gravar(api, texto.replace(velho, novo, 1))
        print("   api.py: sort agora aceita trafego e nao_convertem")
    else:
        raise RuntimeError("padrao do sort nao encontrado 1x em api.py")

    for f in (svc, con, api):
        ast.parse(ler(f))
        print(f"   sintaxe ok: {os.path.basename(f)}")
    print("PATCH_OK")


if __name__ == "__main__":
    main()
