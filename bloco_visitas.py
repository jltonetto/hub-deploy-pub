    # --- visitas por anúncio (dado de tráfego) --------------------------
    def coletar_visitas(self, alias: str, limite: int | None = None,
                        somente_sem_dado: bool = True) -> dict:
        """Coleta visitas totais por anúncio, em lote.

        Por que este dado é o mais importante agora: sem visita não se separa
        "ninguém viu" (problema de exposição) de "viu e não comprou" (problema
        de página). São diagnósticos opostos com soluções opostas.

        Retomável: com `somente_sem_dado=True` (padrão), só busca quem ainda
        não tem visita coletada hoje — rodar de novo no mesmo dia não repete.
        """
        account = self.accounts.get(self.platform, alias)
        if account is None:
            raise LookupError(f"Conta desconhecida: {self.platform}/{alias}")
        aid = int(account["id"])

        if somente_sem_dado:
            rows = list(self.conn.execute(
                """SELECT l.id, l.item_id, l.sold_quantity FROM listings l
                    WHERE l.account_id = ?
                      AND NOT EXISTS (SELECT 1 FROM listing_visits v
                                       WHERE v.listing_id = l.id
                                         AND v.coletado_em = date('now'))
                    ORDER BY l.computed_score ASC""", (aid,)))
        else:
            rows = list(self.conn.execute(
                """SELECT l.id, l.item_id, l.sold_quantity FROM listings l
                    WHERE l.account_id = ? ORDER BY l.id""", (aid,)))

        if limite:
            rows = rows[:limite]
        if not rows:
            return {"coletados": 0, "motivo": "todos já têm visita de hoje"}

        connector = self.connector_for(account)
        total, erros = 0, 0
        # lotes de 100 (o endpoint aceita vários ids por chamada)
        for i in range(0, len(rows), 100):
            chunk = rows[i:i + 100]
            try:
                visitas = connector.visits_by_items([r["item_id"] for r in chunk])
            except Exception as exc:  # noqa: BLE001
                log.warning("lote de visitas falhou: %s", exc)
                erros += len(chunk)
                continue
            for r in chunk:
                v = int(visitas.get(r["item_id"], 0))
                try:
                    self.conn.execute(
                        """INSERT INTO listing_visits (listing_id, total)
                           VALUES (?, ?)
                           ON CONFLICT(listing_id, coletado_em)
                           DO UPDATE SET total=excluded.total""",
                        (int(r["id"]), v))
                    vendas = int(r["sold_quantity"] or 0)
                    conv = (vendas / v) if v > 0 else None
                    self.conn.execute(
                        """UPDATE listings SET visits_total=?, visits_at=datetime('now'),
                               conversion_rate=? WHERE id=?""",
                        (v, conv, int(r["id"])))
                    total += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("gravar visita de %s: %s", r["item_id"], exc)
                    erros += 1
            self.conn.commit()

        return {"coletados": total, "erros": erros,
                "lotes": (len(rows) + 99) // 100,
                "faltam": len(rows) - total}

    def resumo_trafego(self, alias: str, limite: int = 20) -> dict:
        """Panorama de tráfego: quem tem visita, quem não tem, quem converte."""
        account = self.accounts.get(self.platform, alias)
        if account is None:
            raise LookupError(f"Conta desconhecida: {self.platform}/{alias}")
        aid = int(account["id"])

        def q(sql):
            return self.conn.execute(sql, (aid,)).fetchone()[0]

        com_visita = q("""SELECT COUNT(*) FROM listings
                           WHERE account_id=? AND visits_total IS NOT NULL""")
        sem_visita = q("""SELECT COUNT(*) FROM listings
                           WHERE account_id=? AND visits_total IS NULL""")
        zero = q("""SELECT COUNT(*) FROM listings
                     WHERE account_id=? AND visits_total = 0""")
        com_trafego = q("""SELECT COUNT(*) FROM listings
                            WHERE account_id=? AND visits_total > 0""")
        # quem tem visita e não vendeu: problema de PÁGINA
        visita_sem_venda = q("""SELECT COUNT(*) FROM listings
                                 WHERE account_id=? AND visits_total > 0
                                   AND COALESCE(sold_quantity,0) = 0""")
        # quem vendeu sem visita registrada: dado anterior à coleta
        venda_sem_visita = q("""SELECT COUNT(*) FROM listings
                                 WHERE account_id=? AND COALESCE(sold_quantity,0) > 0
                                   AND COALESCE(visits_total,0) = 0""")

        top = list(self.conn.execute(
            """SELECT item_id, title, visits_total, sold_quantity, conversion_rate,
                      computed_score
                 FROM listings WHERE account_id=? AND visits_total > 0
                ORDER BY visits_total DESC LIMIT ?""", (aid, limite)))

        return {
            "com_visita": com_visita, "sem_visita": sem_visita,
            "zero_visita": zero, "com_trafego": com_trafego,
            "visita_sem_venda": visita_sem_venda,
            "venda_sem_visita": venda_sem_visita,
            "top": [{"item_id": r["item_id"], "title": r["title"],
                     "visitas": r["visits_total"], "vendas": r["sold_quantity"],
                     "conversao": round(r["conversion_rate"], 4) if r["conversion_rate"] else None,
                     "score": r["computed_score"]} for r in top],
        }

    # --- agente de IA (propõe; nunca publica) ---------------------------
    def teto_ia(self) -> dict:
        """Quanto já foi gasto no mês e quanto resta do teto."""
        teto_brl = float(os.environ.get("AI_TETO_BRL", "100"))
        gasto = self.usage.do_mes()
        gasto_brl = gasto["custo_usd"] * CAMBIO_BRL_IA
        return {
            "mes": gasto["mes"], "chamadas": gasto["chamadas"],
            "gasto_usd": round(gasto["custo_usd"], 4),
            "gasto_brl": round(gasto_brl, 2),
            "teto_brl": teto_brl,
            "restante_brl": round(max(0.0, teto_brl - gasto_brl), 2),
            "percentual": round(min(100.0, gasto_brl / teto_brl * 100), 1) if teto_brl else 0,
            "atingido": gasto_brl >= teto_brl,
            "aviso_80": gasto_brl >= teto_brl * 0.8,
        }

    def _conferir_teto(self) -> None:
        t = self.teto_ia()
        if t["atingido"]:
            raise ValueError(
                f"Teto mensal de R$ {t['teto_brl']:.0f} atingido "
                f"(gasto R$ {t['gasto_brl']:.2f}). O agente para aqui. "
                "Ajuste AI_TETO_BRL para liberar mais.")
        if t["aviso_80"]:
            log.warning("Consumo de IA em %.0f%% do teto mensal (R$ %.2f de R$ %.0f)",
                        t["percentual"], t["gasto_brl"], t["teto_brl"])

    def fila_agente(self, alias: str, limite: int = 20,
                    estoque_minimo: int = 10) -> dict:
        """Quem o agente deve atender primeiro, por valor em jogo.

        Critério aprovado pelo CEO (09/11/2026): só entra anúncio com estoque
        que justifique o investimento. Otimizar item com 1 unidade gasta o
        mesmo token que um com 20 — mas o texto trabalha 20 vezes.
        """
        account = self.accounts.get(self.platform, alias)
        if account is None:
            raise LookupError(f"Conta desconhecida: {self.platform}/{alias}")
        aid = int(account["id"])
        rows = list(self.conn.execute(
            """SELECT l.id, l.item_id, l.title, l.stock, l.price_amount,
                      l.computed_score, l.sold_quantity,
                      (SELECT COUNT(*) FROM findings f WHERE f.listing_id=l.id
                        AND f.resolved_at IS NULL) AS n_findings
                 FROM listings l
                WHERE l.account_id = ?
                  AND l.status = 'active'
                  AND l.stock >= ?
                  AND NOT EXISTS (SELECT 1 FROM suggestions s
                                   WHERE s.listing_id = l.id AND s.field='description'
                                     AND s.status IN ('proposed','approved','applied'))
                ORDER BY (COALESCE(l.stock,0) * COALESCE(l.price_amount,0)) DESC,
                         n_findings DESC, l.computed_score ASC
                LIMIT ?""", (aid, estoque_minimo, max(1, min(limite, 100)))))
        itens = [{
            "item_id": r["item_id"], "title": r["title"],
            "estoque": int(r["stock"] or 0),
            "preco": float(r["price_amount"] or 0),
            "valor_estoque": round(float(r["stock"] or 0) * float(r["price_amount"] or 0), 2),
            "findings": int(r["n_findings"] or 0),
            "score": r["computed_score"],
        } for r in rows]
        total_prontos = self.conn.execute(
            """SELECT COUNT(*) FROM listings l WHERE l.account_id=? AND l.status='active'
                AND l.stock >= ? AND NOT EXISTS (SELECT 1 FROM suggestions s
                  WHERE s.listing_id=l.id AND s.field='description'
                    AND s.status IN ('proposed','approved','applied'))""",
            (aid, estoque_minimo)).fetchone()[0]
        return {"total_elegiveis": total_prontos, "estoque_minimo": estoque_minimo,
                "itens": itens, "teto": self.teto_ia()}

    def agente_gerar(self, alias: str, limite: int = 10,
                     estoque_minimo: int = 10, operador: str = "ceo") -> dict:
        """Gera descrições com IA e grava como sugestão PROPOSTA.

        Nada é publicado: o texto entra em `suggestions` com status 'proposed'
        e um operador humano aprova no painel. As travas de escrita seguem
        intactas (snapshot, confirmado=True, verificação).

        O teto de gasto é conferido ANTES de cada chamada — nunca depois.
        """
        from hub.engine.agent import Agente, AgenteIndisponivel, montar_prompt

        self._conferir_teto()

        account = self.accounts.get(self.platform, alias)
        if account is None:
            raise LookupError(f"Conta desconhecida: {self.platform}/{alias}")

        fila = self.fila_agente(alias, limite, estoque_minimo)
        if not fila["itens"]:
            return {"gerados": 0, "falhas": 0, "motivo": "fila vazia",
                    "teto": fila["teto"], "detalhe": []}

        try:
            agente = Agente()
        except AgenteIndisponivel as exc:
            raise ValueError(str(exc)) from exc

        gerados, falhas, detalhe = 0, 0, []
        for item in fila["itens"]:
            # teto conferido a cada item: um lote grande não pode estourar
            try:
                self._conferir_teto()
            except ValueError as exc:
                detalhe.append({"item_id": item["item_id"], "erro": str(exc)[:120]})
                falhas += 1
                break

            row = self.listings.get(int(account["id"]), item["item_id"])
            if row is None:
                continue
            payload = json.loads(row["payload"] or "{}")
            attrs = {}
            for a in payload.get("attributes") or []:
                v = a.get("value")
                if isinstance(v, list):
                    v = ", ".join(str(x) for x in v if x)
                if v:
                    attrs[(a.get("id") or "").upper()] = str(v)

            dados = {
                "item_id": item["item_id"],
                "titulo": row["title"],
                "isbn": attrs.get("GTIN") or attrs.get("ISBN") or "",
                "autor": attrs.get("AUTHOR") or "",
                "editora": attrs.get("BOOK_PUBLISHER") or "",
                "idioma": attrs.get("LANGUAGE") or "",
                "formato": attrs.get("BOOK_FORMAT") or attrs.get("FORMAT") or "",
            }
            # ETAPA 1 — o modelo pesquisa sozinho, usando SÓ o ISBN.
            ctx_salvo = self.conn.execute(
                "SELECT contexto FROM listing_context WHERE listing_id=?",
                (int(row["id"]),)).fetchone()
            ficha_local = extrair_ficha(row["description"] or "")
            if ctx_salvo and ctx_salvo["contexto"]:
                ctx = ctx_salvo["contexto"]
            elif len(ficha_local) >= 10:
                ctx = ""      # ficha rica no próprio anúncio: economiza a busca
            else:
                ctx, t_in_busca, t_out_busca = agente.buscar_fatos(dados["isbn"])
                if t_in_busca or t_out_busca:
                    custo_busca = (t_in_busca * agente.preco_in
                                   + t_out_busca * agente.preco_out) / 1_000_000
                    self.usage.registrar(item["item_id"], agente.modelo,
                                         t_in_busca, t_out_busca, custo_busca,
                                         "ok", "busca de fatos")
                if ctx:
                    self.conn.execute(
                        """INSERT INTO listing_context (listing_id, isbn, contexto,
                               fonte, criado_em) VALUES (?,?,?,?,datetime('now'))""",
                        (int(row["id"]), dados["isbn"], ctx, "web_search"))
                    self.conn.commit()

            # ETAPA 2 — redação com a ficha que já existe no anúncio.
            ficha = extrair_ficha(row["description"] or "")
            corpo_antigo = corpo_sem_ficha(row["description"] or "")

            faltando = [] if (ctx or ficha) else ["dados bibliográficos não encontrados"]
            res = agente.gerar_descricao(
                dados, montar_prompt(dados, contexto=ctx, faltando=faltando,
                                     ficha=ficha, corpo_antigo=corpo_antigo))
            self.usage.registrar(
                item["item_id"], agente.modelo, res.tokens_in, res.tokens_out,
                res.custo_usd, "ok" if res.ok else "erro", res.erro[:400])

            if not res.ok:
                falhas += 1
                detalhe.append({"item_id": item["item_id"], "erro": res.erro[:150]})
                continue

            # grava como proposta — NUNCA como aprovado
            try:
                self.conn.execute(
                    """INSERT INTO suggestions (listing_id, field, old_value,
                           new_value, justification, risk, author, status)
                       VALUES (?,?,?,?,?,?,?, 'proposed')""",
                    (int(row["id"]), "description", row["description"] or "",
                     res.texto,
                     f"Gerado pelo agente ({agente.modelo}); {len(res.texto)} caracteres",
                     "low", "llm"))
                self.conn.commit()
                gerados += 1
                detalhe.append({"item_id": item["item_id"], "ok": True,
                                "chars": len(res.texto),
                                "custo_brl": round(res.custo_brl, 4),
                                "problemas": res.problemas})
            except Exception as exc:  # noqa: BLE001
                falhas += 1
                detalhe.append({"item_id": item["item_id"],
                                "erro": f"gravar: {str(exc)[:120]}"})

        return {"gerados": gerados, "falhas": falhas, "modelo": agente.modelo,
                "teto": self.teto_ia(), "detalhe": detalhe}
