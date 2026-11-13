"""Aplica a atualização de coleta de visitas no Hub já instalado.

Em vez de sobrescrever service.py (76 KB) e api.py (29 KB), este script
INSERE o bloco novo no lugar certo, com âncora única verificada.

Duas travas:
  1. detecta "já aplicado" pelo NOME DO MÉTODO, não pelo bloco inteiro —
     assim uma diferença de comentário não faz o método ser duplicado;
  2. exige que a âncora apareça exatamente 1 vez, senão aborta sem alterar.
"""
import ast
import os
import shutil
from datetime import datetime

DEST = "/opt/hub-anuncios"
AQUI = os.path.dirname(os.path.abspath(__file__))


def ler(caminho):
    with open(caminho, encoding="utf-8") as f:
        return f.read()


def gravar(caminho, texto):
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(texto)


def aplicar(caminho, ancora, bloco, marcador, nome):
    """Insere `bloco` antes de `ancora` se `marcador` ainda não existir."""
    texto = ler(caminho)
    if marcador in texto:
        print(f"   {nome}: ja aplicado ({marcador!r} presente)")
        return False
    n = texto.count(ancora)
    if n != 1:
        raise RuntimeError(
            f"ancora encontrada {n} vezes em {nome} — esperado 1. "
            f"Ancora: {ancora!r}")
    gravar(caminho, texto.replace(ancora, bloco + ancora, 1))
    print(f"   {nome}: +{len(bloco)} caracteres inseridos")
    return True


def main():
    servico = os.path.join(DEST, "hub/service.py")
    api = os.path.join(DEST, "hub/api.py")
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"/root/hub-backup-{carimbo}"
    os.makedirs(backup, exist_ok=True)
    for f in (servico, api):
        shutil.copyfile(f, os.path.join(backup, os.path.basename(f)))
    print(f"   backup em {backup}")

    bloco_svc = ler(os.path.join(AQUI, "bloco_visitas.py"))
    bloco_api = ler(os.path.join(AQUI, "rotas_visitas.py"))

    mudou = aplicar(
        servico,
        "    # --- fila de aplicação (escreve no marketplace) ---",
        bloco_svc,
        "def coletar_visitas",
        "service.py")

    if aplicar(
        api,
        '@app.get("/painel/api/agente/fila"',
        bloco_api,
        "visitas/coletar",
        "api.py"):
        mudou = True

    for f in (servico, api):
        ast.parse(ler(f))
        print(f"   sintaxe ok: {os.path.basename(f)}")
    print("PATCH_OK" if mudou else "JA_APLICADO")


if __name__ == "__main__":
    main()
