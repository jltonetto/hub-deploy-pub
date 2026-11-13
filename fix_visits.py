def visits_by_items(self, item_ids: list[str]) -> dict[str, int]:
        """Visitas totais por anúncio (últimos 2 anos).

        Endpoint: `GET /visits/items?ids=MLB1`. Devolve {item_id: total}.

        CORREÇÃO 13/11/2026 (diagnóstico em produção): o Mercado Livre aceita
        UM item por chamada, não vários. A doc de "Multi-Get" sugere lote, mas
        a API responde 400 `"maximum amount of items to query is 1"` para
        qualquer `ids` com mais de um. Então aqui é um a um — e por isso
        a função é lenta de propósito: ~8.570 chamadas.

        Visita é o dado que separa "ninguém viu" de "viu e não comprou" —
        diagnóstico que a conformidade do marketplace não dá.
        """
        saida: dict[str, int] = {}
        for item_id in item_ids:
            try:
                data = self.http.request(
                    "GET", "/visits/items", token=self.access_token,
                    params={"ids": item_id})
            except Exception as exc:  # noqa: BLE001
                # NÃO grava zero: erro de leitura não é ausência de visita.
                log.warning("visita de %s falhou: %s", item_id, exc)
                continue
            # Resposta documentada e confirmada em produção: {"MLB123": 552}
            if isinstance(data, dict):
                for k, v in data.items():
                    try:
                        saida[str(k)] = int(v)
                    except (TypeError, ValueError):
                        log.warning("visita de %s com valor inesperado: %r", k, v)
            elif isinstance(data, list):
                for linha in data:
                    if isinstance(linha, dict):
                        iid = linha.get("item_id") or linha.get("id")
                        if iid:
                            try:
                                saida[str(iid)] = int(
                                    linha.get("total_visits") or 0)
                            except (TypeError, ValueError):
                                pass
        return saida
