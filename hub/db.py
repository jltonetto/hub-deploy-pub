"""Camada de persistência — SQLite com o esquema de db/schema.sql.

Sem ORM de propósito: o volume é modesto (dezenas de milhares de linhas) e o
SQL explícito deixa claro o que acontece com os dados de cada anúncio.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Cria/atualiza o esquema. Idempotente por contrato: roda a cada abertura.

    ALTER TABLE ADD COLUMN não é idempotente (falha com "duplicate column name"
    na segunda vez), então migração de coluna é feita aqui, com verificação —
    nunca no schema.sql, que é executado inteiro a cada chamada.
    """
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _add_column_if_missing(conn, "listings", "selected_for_analysis",
                           "INTEGER NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "oauth_states", "code_verifier", "TEXT")
    # Elegibilidade para edição (ver models.Listing)
    _add_column_if_missing(conn, "listings", "sold_quantity", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "listings", "catalog_listing", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "listings", "catalog_product_id", "TEXT")
    # Campos de anúncio que o ML exige na criação/edição (ver models.Listing).
    # São criados aqui porque ALTER TABLE não é idempotente.
    for col, ddl in (
        ("condition", "TEXT"),
        ("listing_type_id", "TEXT"),
        ("buying_mode", "TEXT"),
        ("warranty", "TEXT"),          # JSON
        ("sale_terms", "TEXT"),        # JSON
        ("shipping", "TEXT"),          # JSON
        ("dimensions", "TEXT"),        # JSON
        ("official_store_id", "INTEGER"),
        ("channels", "TEXT"),          # JSON
        ("accepts_mercadopago", "INTEGER"),
        ("automatic_relist", "INTEGER"),
        ("base_price", "REAL"),
        ("original_price", "REAL"),
        ("initial_quantity", "INTEGER"),
        ("domain_id", "TEXT"),
        ("family_id", "INTEGER"),
        ("family_name", "TEXT"),
        ("user_product_id", "TEXT"),
        ("video_id", "TEXT"),
        ("videos", "TEXT"),
        ("visits_total", "INTEGER"),      # visita mais recente (cache p/ ordenar)
        ("visits_at", "TEXT"),            # quando foi coletada
        ("conversion_rate", "REAL"),      # vendas / visitas
        ("parent_item_id", "TEXT"),
        ("permalink", "TEXT"),
        ("date_created", "TEXT"),
        ("expiration_time", "TEXT"),
        ("end_time", "TEXT"),
        ("seller_custom_field", "TEXT"),
        ("inventory_id", "TEXT"),
        ("international_delivery_mode", "TEXT"),
        ("sub_status", "TEXT"),        # JSON
        ("variations", "TEXT"),        # JSON
        # Estado de publicação no marketplace (o "publicar" do painel).
        ("published_at", "TEXT"),      # quando o Hub gravou no ML
        ("published_fields", "TEXT"),  # JSON: campos que foram gravados
        ("publish_status", "TEXT"),    # applied | pending (catálogo) | error
    ):
        _add_column_if_missing(conn, "listings", col, ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_selected "
                 "ON listings(account_id, selected_for_analysis)")
    # A lista "já editados" consulta por published_at: precisa de índice.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_published "
                 "ON listings(account_id, published_at)")
    # ordenar por tráfego é a prioridade do Marketing: precisa de índice
    conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_visits "
                 "ON listings(account_id, visits_total)")
    conn.commit()


def _add_column_if_missing(conn: sqlite3.Connection, table: str,
                           column: str, ddl: str) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# ----------------------------------------------------------------------
class AccountRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, platform: str, alias: str, seller_id: str | None = None,
               nickname: str | None = None) -> int:
        self.conn.execute(
            """INSERT INTO accounts (platform, alias, seller_id, nickname)
               VALUES (?,?,?,?)
               ON CONFLICT(platform, alias) DO UPDATE SET
                 seller_id = COALESCE(excluded.seller_id, seller_id),
                 nickname  = COALESCE(excluded.nickname, nickname)""",
            (platform, alias, seller_id, nickname))
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM accounts WHERE platform=? AND alias=?",
                                (platform, alias)).fetchone()
        return int(row["id"])

    def save_credentials(self, account_id: int, credentials_enc: str,
                         expires_at: str | None) -> None:
        self.conn.execute(
            """UPDATE accounts SET credentials_enc=?, token_status='valid',
               token_expires_at=? WHERE id=?""", (credentials_enc, expires_at, account_id))
        self.conn.commit()

    def set_token_status(self, account_id: int, status: str) -> None:
        self.conn.execute("UPDATE accounts SET token_status=? WHERE id=?", (status, account_id))
        self.conn.commit()

    def get(self, platform: str, alias: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM accounts WHERE platform=? AND alias=?",
                                 (platform, alias)).fetchone()

    def all(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM accounts ORDER BY platform, alias"))


class ListingRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, account_id: int, listing, computed_score: float) -> int:
        payload = json.dumps(listing.to_dict(), ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO listings (account_id, item_id, status, title, description,
                   category_id, category_path, price_amount, currency, stock,
                   quality_score, quality_level, computed_score, payload,
                   collected_at, updated_at_platform, sold_quantity,
                   catalog_listing, catalog_product_id,
                   condition, listing_type_id, buying_mode, warranty, sale_terms,
                   shipping, dimensions, official_store_id, channels,
                   accepts_mercadopago, automatic_relist, base_price,
                   original_price, initial_quantity, domain_id, family_id,
                   family_name, user_product_id, parent_item_id, permalink,
                   date_created, video_id, videos,
                   expiration_time, end_time, seller_custom_field, inventory_id,
                   international_delivery_mode, sub_status, variations)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(account_id, item_id) DO UPDATE SET
                   status=excluded.status, title=excluded.title,
                   description=excluded.description, category_id=excluded.category_id,
                   category_path=excluded.category_path, price_amount=excluded.price_amount,
                   currency=excluded.currency, stock=excluded.stock,
                   quality_score=excluded.quality_score, quality_level=excluded.quality_level,
                   computed_score=excluded.computed_score, payload=excluded.payload,
                   collected_at=excluded.collected_at,
                   updated_at_platform=excluded.updated_at_platform,
                   sold_quantity=excluded.sold_quantity,
                   catalog_listing=excluded.catalog_listing,
                   catalog_product_id=excluded.catalog_product_id,
                   condition=excluded.condition,
                   listing_type_id=excluded.listing_type_id,
                   buying_mode=excluded.buying_mode,
                   warranty=excluded.warranty, sale_terms=excluded.sale_terms,
                   shipping=excluded.shipping, dimensions=excluded.dimensions,
                   official_store_id=excluded.official_store_id,
                   channels=excluded.channels,
                   accepts_mercadopago=excluded.accepts_mercadopago,
                   automatic_relist=excluded.automatic_relist,
                   base_price=excluded.base_price,
                   original_price=excluded.original_price,
                   initial_quantity=excluded.initial_quantity,
                   domain_id=excluded.domain_id, family_id=excluded.family_id,
                   family_name=excluded.family_name,
                   user_product_id=excluded.user_product_id,
                   video_id=excluded.video_id, videos=excluded.videos,
                   parent_item_id=excluded.parent_item_id,
                   permalink=excluded.permalink,
                   date_created=excluded.date_created,
                   expiration_time=excluded.expiration_time,
                   end_time=excluded.end_time,
                   seller_custom_field=excluded.seller_custom_field,
                   inventory_id=excluded.inventory_id,
                   international_delivery_mode=excluded.international_delivery_mode,
                   sub_status=excluded.sub_status,
                   variations=excluded.variations""",
            (account_id, listing.key.item_id, listing.status, listing.title,
             listing.description, listing.category_id, json.dumps(listing.category_path),
             listing.price_amount, listing.currency, listing.stock,
             listing.quality.score, listing.quality.level, computed_score, payload,
             listing.collected_at, listing.updated_at_platform,
             listing.sold_quantity, 1 if listing.catalog_listing else 0,
             listing.catalog_product_id,
             listing.condition, listing.listing_type_id, listing.buying_mode,
             json.dumps(listing.warranty, ensure_ascii=False) if listing.warranty is not None else None,
             json.dumps(listing.sale_terms, ensure_ascii=False),
             json.dumps(listing.shipping, ensure_ascii=False),
             json.dumps(listing.dimensions, ensure_ascii=False),
             listing.official_store_id,
             json.dumps(listing.channels, ensure_ascii=False),
             (1 if listing.accepts_mercadopago else 0) if listing.accepts_mercadopago is not None else None,
             (1 if listing.automatic_relist else 0) if listing.automatic_relist is not None else None,
             listing.base_price, listing.original_price, listing.initial_quantity,
             listing.domain_id, listing.family_id, listing.family_name,
             listing.user_product_id,
             listing.parent_item_id, listing.permalink, listing.date_created,
             listing.video_id, json.dumps(listing.videos, ensure_ascii=False),
             listing.expiration_time, listing.end_time, listing.seller_custom_field,
             listing.inventory_id, listing.international_delivery_mode,
             json.dumps(listing.sub_status, ensure_ascii=False),
             json.dumps(listing.variations, ensure_ascii=False)))
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM listings WHERE account_id=? AND item_id=?",
                                (account_id, listing.key.item_id)).fetchone()
        return int(row["id"])

    def snapshot(self, listing_id: int, reason: str, payload: str) -> None:
        self.conn.execute(
            "INSERT INTO listing_snapshots (listing_id, reason, payload) VALUES (?,?,?)",
            (listing_id, reason, payload))
        self.conn.commit()

    def get(self, account_id: int, item_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM listings WHERE account_id=? AND item_id=?",
            (account_id, item_id)).fetchone()

    def list(self, account_id: int, limit: int = 50, worst_first: bool = True) -> list[sqlite3.Row]:
        order = "computed_score ASC" if worst_first else "computed_score DESC"
        return list(self.conn.execute(
            f"SELECT * FROM listings WHERE account_id=? ORDER BY {order} LIMIT ?",
            (account_id, limit)))

    # --- painel -------------------------------------------------------
    def panel_rows(self, account_id: int, limit: int = 50, offset: int = 0,
                   sort: str = "worst", q: str = "", finding: str | None = None,
                   item_id: str = "", sku: str = "", so_editados: bool = False) -> list[sqlite3.Row]:
        """Lista paginada para o painel. `sort` e `finding` são allowlist —
        nunca interpolamos texto vindo do cliente em ORDER BY."""
        order = {
            # Já publicados vão SEMPRE para o fim da listagem normal: o que
            # ainda precisa de trabalho fica no topo.
            "worst": "(l.published_at IS NOT NULL), computed_score ASC",
            "best": "computed_score DESC, (l.published_at IS NULL)",
            "az": "title COLLATE NOCASE ASC",
            # Prioridade do Marketing (11/11/2026): tráfego primeiro. Anúncio
            # sem visita não devolve nada ao ser corrigido.
            "trafego": "COALESCE(l.visits_total,0) DESC, computed_score ASC",
            # Quem visita e não compra: problema de PÁGINA, não de exposição.
            "nao_convertem": ("(COALESCE(l.visits_total,0) > 0 AND "
                              "COALESCE(l.sold_quantity,0) = 0), "
                              "COALESCE(l.visits_total,0) DESC"),
            # lista dos já editados: mais recente primeiro
            "editados": "l.published_at DESC",
        }.get(sort, "(l.published_at IS NOT NULL), computed_score ASC")
        where = ["l.account_id = ?"]
        args: list[Any] = [account_id]
        if q:
            where.append("l.title LIKE ?")
            args.append(f"%{q}%")
        if item_id:
            # busca exata e sem diferenciar maiúscula: o ID do ML é MLB + dígitos
            where.append("UPPER(l.item_id) = UPPER(?)")
            args.append(item_id.strip())
        if sku:
            where.append("l.seller_custom_field LIKE ?")
            args.append(f"%{sku.strip()}%")
        if so_editados:
            where.append("l.published_at IS NOT NULL")
        if finding:
            where.append(
                "EXISTS (SELECT 1 FROM findings f WHERE f.listing_id = l.id "
                "AND f.resolved_at IS NULL AND f.code = ?)")
            args.append(finding)
        sql = (f"SELECT l.*, (SELECT COUNT(*) FROM findings f WHERE f.listing_id = l.id "
               f"AND f.resolved_at IS NULL) AS n_findings "
               f"FROM listings l WHERE {' AND '.join(where)} "
               f"ORDER BY {order} LIMIT ? OFFSET ?")
        args += [limit, offset]
        return list(self.conn.execute(sql, args))

    def set_selected(self, account_id: int, item_ids: list[str], value: int = 1) -> int:
        """Marca/desmarca anúncios para análise. Devolve quantos foram tocados."""
        if not item_ids:
            return 0
        marks = ",".join("?" * len(item_ids))
        cur = self.conn.execute(
            f"UPDATE listings SET selected_for_analysis=? WHERE account_id=? "
            f"AND item_id IN ({marks})", [value, account_id, *item_ids])
        self.conn.commit()
        return cur.rowcount

    def selected_ids(self, account_id: int) -> list[str]:
        return [r["item_id"] for r in self.conn.execute(
            "SELECT item_id FROM listings WHERE account_id=? AND selected_for_analysis=1 "
            "ORDER BY computed_score ASC", (account_id,))]

    def summary(self, account_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT COUNT(*) AS total,
                      ROUND(AVG(computed_score), 1)  AS avg_computed,
                      ROUND(AVG(quality_score), 1)   AS avg_native,
                      ROUND(MIN(computed_score), 1)  AS min_computed
               FROM listings WHERE account_id=?""", (account_id,)).fetchone()
        return dict(row) if row else {}


class UsageRepo:
    """Consumo de IA — base do teto de gasto.

    O teto é conferido ANTES de cada chamada. Sem isso, um laço com bug gera
    centenas de chamadas e a fatura chega depois. Com isso, o pior caso é o teto.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def registrar(self, item_id: str, modelo: str, tokens_in: int,
                  tokens_out: int, custo_usd: float, resultado: str = "ok",
                  detalhe: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO ai_usage (item_id, modelo, tokens_in, tokens_out,
                   custo_usd, resultado, detalhe) VALUES (?,?,?,?,?,?,?)""",
            (item_id, modelo, tokens_in, tokens_out, custo_usd, resultado,
             detalhe[:500]))
        self.conn.commit()
        return int(cur.lastrowid)

    def do_mes(self, ano_mes: str | None = None) -> dict:
        """Gasto acumulado no mês corrente (UTC)."""
        if not ano_mes:
            from datetime import datetime, timezone
            ano_mes = datetime.now(timezone.utc).strftime("%Y-%m")
        r = self.conn.execute(
            """SELECT COUNT(*) n, COALESCE(SUM(tokens_in),0) ti,
                      COALESCE(SUM(tokens_out),0) to_, COALESCE(SUM(custo_usd),0) usd
                 FROM ai_usage WHERE substr(quando,1,7)=?""", (ano_mes,)).fetchone()
        return {"mes": ano_mes, "chamadas": r["n"], "tokens_in": r["ti"],
                "tokens_out": r["to_"], "custo_usd": round(r["usd"], 6)}

    def esquecer(self) -> None:
        self.conn.execute("DELETE FROM ai_usage WHERE resultado='teste'")
        self.conn.commit()


class EditRepo:
    """Edições do operador — memória do gosto dele.

    Cada linha é uma diferença entre o que a IA propôs e o que o operador
    deixou. Serve para dois fins: reverter (saber de onde veio) e aprender
    (o que ele corrige com frequência).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def registrar(self, listing_id: int, field: str, final_value: str,
                  ia_value: str | None = None, suggestion_id: int | None = None,
                  operator: str = "ceo") -> int:
        alterado = 1 if (ia_value is None or final_value != ia_value) else 0
        cur = self.conn.execute(
            """INSERT INTO edits (listing_id, suggestion_id, field, ia_value,
                   final_value, alterado, operator) VALUES (?,?,?,?,?,?,?)""",
            (listing_id, suggestion_id, field, ia_value, final_value, alterado, operator))
        self.conn.commit()
        return int(cur.lastrowid)

    def for_listing(self, listing_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM edits WHERE listing_id=? ORDER BY id DESC", (listing_id,)))

    def aprendizado(self, limite: int = 40) -> list[sqlite3.Row]:
        """O que o operador mais corrige — insumo para ajustar o prompt."""
        return list(self.conn.execute(
            """SELECT field, ia_value, final_value, created_at FROM edits
                WHERE alterado=1 ORDER BY id DESC LIMIT ?""", (limite,)))

    def estatisticas(self, account_id: int) -> dict:
        r = self.conn.execute(
            """SELECT COUNT(*) AS n, SUM(alterado) AS alterados
                 FROM edits e JOIN listings l ON l.id = e.listing_id
                WHERE l.account_id=?""", (account_id,)).fetchone()
        return {"total": r["n"] or 0, "alterados": r["alterados"] or 0}


class FindingRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def replace_for_listing(self, listing_id: int, findings: Iterable, run_id: int | None) -> None:
        """Achados são regerados a cada coleta: fecha os antigos, grava os novos.
        Mantém histórico (resolved_at) em vez de apagar — é o que permite medir
        se a correção realmente resolveu."""
        self.conn.execute(
            "UPDATE findings SET resolved_at=datetime('now') "
            "WHERE listing_id=? AND resolved_at IS NULL", (listing_id,))
        for f in findings:
            self.conn.execute(
                """INSERT INTO findings (listing_id, run_id, code, severity, field,
                       message, current_val, recommended, source)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (listing_id, run_id, f.code, f.severity, f.field, f.message,
                 json.dumps(f.current, ensure_ascii=False),
                 json.dumps(f.recommended, ensure_ascii=False), f.source))
        self.conn.commit()

    def for_listing(self, listing_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM findings WHERE listing_id=? AND resolved_at IS NULL "
            "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 ELSE 3 END", (listing_id,)))

    def by_code(self, account_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            """SELECT code, severity, COUNT(*) AS n FROM findings f
               JOIN listings l ON l.id = f.listing_id
               WHERE l.account_id=? AND f.resolved_at IS NULL
               GROUP BY code, severity ORDER BY n DESC""", (account_id,)))


class RunRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def start(self, account_id: int, mode: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO collection_runs (account_id, mode, status) VALUES (?,?, 'running')",
            (account_id, mode))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish(self, run_id: int, items_read: int, errors: int, status: str,
               detail: str = "") -> None:
        self.conn.execute(
            """UPDATE collection_runs SET finished_at=datetime('now'), items_read=?,
               errors=?, status=?, detail=? WHERE id=?""",
            (items_read, errors, status, detail[:2000], run_id))
        self.conn.commit()
