"""Camada de serviço — orquestra coleta, diagnóstico e leitura para a API.

O conector é injetado (nunca instanciado aqui): é isso que permite testar o
fluxo inteiro com um conector falso, sem rede e sem credencial.

ATENÇÃO: este arquivo é montado a partir de 3 partes (svc_p1.py, svc_p2.py,
svc_p3.py) no instalador. NÃO editar aqui — editar as partes.
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
        expires_in = int(tokens.get("expires_in") or 21600)  # ML: 6 h
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        blob = json.dumps({
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": expires_at,
            "scope": tokens.get("scope"),
            "user_id": tokens.get("user_id"),
        })
        self.accounts.save_credentials(account_id, self.vault.encrypt(blob), expires_at)

    def load_tokens(self, account_row) -> dict[str, Any]:
        if not account_row["credentials_enc"]:
            raise RuntimeError(f"Conta {account_row['alias']} sem credencial: autorize antes.")
        return json.loads(self.vault.decrypt(account_row["credentials_enc"]))

    def connector_for(self, account_row) -> Connector:
        """Conector com token válido.

        O access token do Mercado Livre vale 6 h e o refresh token é de USO ÚNICO:
        a cada renovação um novo é devolvido. Se a renovação acontecesse e não
        fosse gravada, o token antigo seria queimado e a conta só voltaria com
        autorização manual — por isso aqui a renovação é persistida na mesma
        chamada, antes de qualquer coleta.
        """
        tokens = self.load_tokens(account_row)
        connector = self.connector_factory(account_row["alias"], tokens)
