"""Aplica a atualização de coleta de visitas no Hub já instalado.

Em vez de sobrescrever service.py (76 KB) e api.py (29 KB), este script
INSERE o bloco novo no lugar certo, com âncora única verificada.

Vantagem: se o servidor tiver uma versão ligeiramente diferente do arquivo,
o patch falha em vez de destruir o que está lá.
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


def aplicar_patch(caminho, ancora, bloco, nome):
    """Insere `bloco` antes de `ancora`. Exige que a âncora exista 1 vez só."""
    texto = ler(caminho)
    if bloco.strip() in texto:
        print(f"   {nome}: ja aplicado (bloco presente)")
        return False
    n = texto.count(ancora)
    if n != 1:
        raise RuntimeError(
            f"ancora encontrada {n} vezes em {nome} — esperado 1. "
            f"Ancora: {ancora!r}")
    gravar(caminho, texto.replace(ancora, bloco + ancora, 1))
    print(f"   {nome}: +{len(bloco)} caracteres")
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

    mudou = aplicar_patch(
        servico,
        "    # --- fila de aplicação (escreve no marketplace) ---",
        bloco_svc, "service.py")

    texto_api = ler(api)
    if "visitas/coletar" in texto_api:
        print("   api.py: ja aplicado")
    else:
        alvo = '@app.get("/painel/api/agente/fila"'
        if texto_api.count(alvo) != 1:
            raise RuntimeError("ancora da api.py nao encontrada 1 vez")
        gravar(api, texto_api.replace(alvo, bloco_api + alvo, 1))
        print(f"   api.py: +{len(bloco_api)} caracteres")
        mudou = True

    for f in (servico, api):
        ast.parse(ler(f))
        print(f"   sintaxe ok: {os.path.basename(f)}")
    print("PATCH_OK" if mudou else "JA_APLICADO")


if __name__ == "__main__":
    main()
