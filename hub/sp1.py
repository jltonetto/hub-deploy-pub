"""Camada de serviço — orquestra coleta, diagnóstico e leitura para a API.

O conector é injetado (nunca instanciado aqui): é isso que permite testar o
fluxo inteiro com um conector falso, sem rede e sem credencial.

ATENÇÃO: este arquivo é montado pelo instalador a partir de 4 partes
(sp1.py, sp2.py, sp3.py, sp4.py). Para alterar, edite as partes.
"""
from __future__ import annotations

import json
import os
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from hub.connectors.base import Connector
from hub.core.vault import Vault
from hub.db import (AccountRepo, EditRepo, FindingRepo, ListingRepo,
                    RunRepo, UsageRepo)
from hub.engine.diagnose import diagnose, load_rules, quality_score
from hub.engine.eligibility import Acao, avaliar, escolher_anuncio_familia
from hub.engine.apply import PlanoAplicacao, exigir_confirmacao, montar_plano
from hub.engine.contexto import (contexto_a_partir_de_fonte,
                                 corpo_sem_ficha, extrair_ficha,
                                 precisa_contexto)

# Câmbio conservador: converte o custo da IA em reais para o teto mensal.
CAMBIO_BRL_IA = float(os.environ.get("AI_CAMBIO_BRL", "5.40"))
from hub.engine.copycheck import limpar, verificar

log = logging.getLogger(__name__)


class HubService:
    def __init__(self, conn, vault: Vault, connector_factory, platform: str = "mercadolivre"):
        self.conn = conn
        self.vault = vault
        self.connector_factory = connector_factory
        self.platform = platform
        self.accounts = AccountRepo(conn)
        self.listings = ListingRepo(conn)
        self.findings = FindingRepo(conn)
        self.runs = RunRepo(conn)
        self.edits = EditRepo(conn)
        self.usage = UsageRepo(conn)
        self.rules = load_rules(platform)

    # --- credenciais ----------------------------------------------------
    def store_tokens(self, account_id: int, tokens: dict[str, Any]) -> None:
        """Guarda tokens cifrados. O refresh token do ML é de uso único: a cada
        renovação um novo é devolvido, e é ele que precisa ser preservado."""
