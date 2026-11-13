def coletar_visitas(self, alias: str, limite: int | None = None,
                        somente_sem_dado: bool = True) -> dict:
        """Coleta visitas totais por anúncio.

        Por que este dado é o mais importante agora: sem visita não se separa
        "ninguém viu" (problema de exposição) de "viu e não comprou" (problema
        de página). São diagnósticos opostos com soluções opostas.

        Retomável: com `somente_sem_dado=True` (padrão), só busca quem ainda
        não tem visita coletada hoje — rodar de novo no mesmo dia não repete.

        IMPORTANTE (13/11/2026): a API do ML aceita UM item por chamada.
        Coletar 8.570 anúncios são 8.570 requisições — por isso o parâmetro
        `limite` existe: rode em lotes (ex.: 500) e chame de novo depois.
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
        total = erros = 0
        for r in rows:
            item_id = r["item_id"]
            try:
                # 1 item por chamada: é o que a API aceita (ver conector).
                visitas = connector.visits_by_items([item_id])
            except Exception as exc:  # noqa: BLE001
                log.warning("visita de %s falhou: %s", item_id, exc)
                erros += 1
                continue
            if item_id not in visitas:
                # leitura não trouxe este item: registra falha, NÃO grava zero.
                # Gravar zero aqui foi o bug de 13/11: transformava erro de
                # leitura em "anúncio sem visita", escondendo o problema.
                erros += 1
                continue
            v = int(visitas[item_id])
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
                log.warning("gravar visita de %s: %s", item_id, exc)
                erros += 1
            if total and total % 100 == 0:
                self.conn.commit()
        self.conn.commit()

        return {"coletados": total, "erros": erros, "processados": len(rows),
                "faltam": len(rows) - total}
