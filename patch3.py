"""Patch v3 — corrige a leitura de visitas do Mercado Livre.

CORRIGE O ERRO DO PATCH v2: o conteúdo das funções vem em base64, porque a
ferramenta de upload do GitHub corta o espaço inicial da primeira linha — foi
isso que deixou `def coletar_visitas` na coluna 0 e quebrou o arquivo.

ORDEM CORRETA (o v2 errou aqui):
  1. aplica tudo em MEMÓRIA
  2. valida a sintaxe dos três
  3. SÓ ENTÃO grava no disco
  4. se qualquer validação falhar, nada é escrito no servidor

O QUE CORRIGE:
  - /visits/items aceita UM item por chamada (400 se mandar mais)
  - falha de leitura NÃO grava zero: registra erro e deixa NULL
  - sort=trafego e sort=nao_convertem aceitos pelo painel
"""
import ast
import base64
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


def carregar_b64(nome):
    """Lê o snippet de base64 e confere que volta como texto válido."""
    bruto = ler(os.path.join(AQUI, nome)).strip()
    texto = base64.b64decode(bruto).decode("utf-8")
    # trava: um método de classe TEM de começar com 4 espaços
    primeira = texto.split("\n")[0]
    if not primeira.startswith("    def "):
        raise RuntimeError(
            f"{nome}: a primeira linha deveria começar com '    def ', "
            f"veio {primeira[:40]!r}")
    return texto


def substituir_funcao(texto, nome, novo_corpo):
    """Troca o corpo da função `nome` (método de classe, 4 espaços de recuo)."""
    inicio = texto.find(f"    def {nome}(")
    if inicio < 0:
        raise RuntimeError(f"função {nome} não encontrada no arquivo")
    padrao = re.compile(r"\n    (?:def |# )", re.MULTILINE)
    m = padrao.search(texto, inicio + 5)
    fim = m.start() if m else len(texto)
    return texto[:inicio] + novo_corpo.rstrip() + "\n\n" + texto[fim:].lstrip("\n")


def main():
    svc = os.path.join(DEST, "hub/service.py")
    con = os.path.join(DEST, "hub/connectors/mercadolivre.py")
    api = os.path.join(DEST, "hub/api.py")

    # ---------- 1. monta TUDO em memória ----------
    print("   montando as mudancas em memoria...")
    texto_con = ler(con)
    texto_svc = ler(svc)
    texto_api = ler(api)

    if "maximum amount of items" in texto_con:
        print("   mercadolivre.py: ja corrigido")
    else:
        texto_con = substituir_funcao(texto_con, "visits_by_items",
                                      carregar_b64("fix_visits.b64"))
        print("   mercadolivre.py: visits_by_items corrigido")

    if "NÃO grava zero" in texto_svc:
        print("   service.py: ja corrigido")
    else:
        texto_svc = substituir_funcao(texto_svc, "coletar_visitas",
                                      carregar_b64("fix_coletar.b64"))
        print("   service.py: coletar_visitas corrigido")

    velho = 'pattern=r"^(worst|best|az|editados)$"'
    novo = 'pattern=r"^(worst|best|az|editados|trafego|nao_convertem)$"'
    if "trafego|nao_convertem" in texto_api:
        print("   api.py: ja corrigido")
    elif texto_api.count(velho) == 1:
        texto_api = texto_api.replace(velho, novo, 1)
        print("   api.py: sort agora aceita trafego")
    else:
        raise RuntimeError("padrao do sort nao encontrado 1x em api.py")

    # ---------- 2. valida ANTES de gravar ----------
    print("   validando sintaxe (nada foi gravado ainda)...")
    for nome, texto in (("service.py", texto_svc),
                        ("mercadolivre.py", texto_con),
                        ("api.py", texto_api)):
        try:
            ast.parse(texto)
        except SyntaxError as exc:
            raise RuntimeError(
                f"{nome} ficaria invalido (linha {exc.lineno}): {exc.msg}. "
                "NADA FOI GRAVADO.") from exc
        print(f"      ok {nome}")

    # ---------- 3. só agora grava ----------
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"/root/hub-backup-{carimbo}"
    os.makedirs(backup, exist_ok=True)
    for f in (svc, con, api):
        shutil.copyfile(f, os.path.join(backup, os.path.basename(f)))
    print(f"   backup em {backup}")

    gravar(con, texto_con)
    gravar(svc, texto_svc)
    gravar(api, texto_api)
    print("   arquivos gravados")

    # ---------- 4. relê e confirma ----------
    for f in (svc, con, api):
        ast.parse(ler(f))
    print("   sintaxe conferida nos 3 apos gravar")
    print("PATCH_OK")


if __name__ == "__main__":
    main()
