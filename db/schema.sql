-- Hub de Anúncios SOLIVROS — esquema inicial (SQLite; compatível com Postgres
-- trocando TEXT->UUID e INTEGER PK->SERIAL).
-- Regra: nada de segredo em texto puro. Credenciais vão cifradas (AES-256-GCM).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY,
    platform        TEXT NOT NULL,
    alias           TEXT NOT NULL,              -- "livrolandia", "solivros"
    seller_id       TEXT,
    nickname        TEXT,
    -- blob cifrado: {"access_token":..., "refresh_token":..., "expires_at":...}
    credentials_enc TEXT,
    token_status    TEXT NOT NULL DEFAULT 'missing',  -- missing|valid|expiring|revoked
    token_expires_at TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, alias)
);

CREATE TABLE IF NOT EXISTS listings (
    id                INTEGER PRIMARY KEY,
    account_id        INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    item_id           TEXT NOT NULL,
    status            TEXT,
    title             TEXT,
    description       TEXT,
    category_id       TEXT,
    category_path     TEXT,          -- JSON
    price_amount      REAL,
    currency          TEXT,
    stock             INTEGER,
    quality_score     REAL,          -- score nativo quando existir
    quality_level     TEXT,
    computed_score    REAL,          -- score derivado das regras
    payload           TEXT NOT NULL, -- JSON do modelo canônico
    collected_at      TEXT NOT NULL,
    updated_at_platform TEXT,
    UNIQUE (account_id, item_id)
);

-- Colunas de elegibilidade (sold_quantity, catalog_listing, catalog_product_id)
-- tambem sao criadas por init_db: ALTER TABLE nao e idempotente.
--
-- A coluna listings.selected_for_analysis e criada por init_db (migracao
-- idempotente): ALTER TABLE ADD COLUMN falha na segunda execucao, e este
-- arquivo roda a cada abertura do banco.

CREATE INDEX IF NOT EXISTS idx_listings_account ON listings(account_id);
CREATE INDEX IF NOT EXISTS idx_listings_score  ON listings(account_id, computed_score);

-- Payload completo a cada coleta e antes de cada escrita: base do rollback.
CREATE TABLE IF NOT EXISTS listing_snapshots (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    reason      TEXT NOT NULL,       -- collect|pre_apply|post_apply
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_snapshots_listing ON listing_snapshots(listing_id, created_at);

CREATE TABLE IF NOT EXISTS collection_runs (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    mode        TEXT NOT NULL DEFAULT 'full',  -- full|incremental
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    items_read  INTEGER DEFAULT 0,
    errors      INTEGER DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'running', -- running|ok|partial|failed
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    run_id      INTEGER REFERENCES collection_runs(id) ON DELETE SET NULL,
    code        TEXT NOT NULL,
    severity    TEXT NOT NULL,       -- critical|high|medium|low
    field       TEXT,
    message     TEXT,
    current_val TEXT,
    recommended TEXT,
    source      TEXT,                -- native|rule
    found_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_findings_listing ON findings(listing_id, resolved_at);
CREATE INDEX IF NOT EXISTS idx_findings_sev     ON findings(severity, resolved_at);

CREATE TABLE IF NOT EXISTS suggestions (
    id            INTEGER PRIMARY KEY,
    listing_id    INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    finding_id    INTEGER REFERENCES findings(id) ON DELETE SET NULL,
    field         TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    justification TEXT,
    risk          TEXT NOT NULL DEFAULT 'low',  -- low|medium|high
    author        TEXT NOT NULL DEFAULT 'engine', -- engine|llm
    status        TEXT NOT NULL DEFAULT 'proposed', -- proposed|approved|applied|rejected|reverted
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at    TEXT,
    decided_by    TEXT
);

CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status, risk);

-- Consumo de IA, para o teto de gasto. Uma linha por chamada.
CREATE TABLE IF NOT EXISTS ai_usage (
    id           INTEGER PRIMARY KEY,
    quando       TEXT NOT NULL DEFAULT (datetime('now')),
    item_id      TEXT,
    modelo       TEXT NOT NULL,
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    custo_usd    REAL NOT NULL DEFAULT 0,
    resultado    TEXT,        -- ok | erro | recusado
    detalhe      TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_mes ON ai_usage(quando);

-- Contexto enriquecido por anúncio (busca por ISBN + pesquisa web assistida).
-- O agente usa isto para escrever com fato em vez de adjetivo.
CREATE TABLE IF NOT EXISTS listing_context (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    isbn        TEXT,
    contexto    TEXT,        -- fatos confirmados, em texto corrido
    fonte       TEXT,        -- brasilapi | openlibrary | web | manual
    faltando    TEXT,        -- o que ainda não foi confirmado
    criado_em   TEXT NOT NULL DEFAULT (datetime('now')),
    atualizado_em TEXT
);

CREATE INDEX IF NOT EXISTS idx_context_listing ON listing_context(listing_id);

-- Histórico de visitas por anúncio. Guardar por data permite ver tendência:
-- um anúncio que perdeu visita tem problema de exposição (título/posição);
-- um que tem visita e não vende tem problema de página (foto/preço).
CREATE TABLE IF NOT EXISTS listing_visits (
    id         INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    coletado_em TEXT NOT NULL DEFAULT (date('now')),
    total      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(listing_id, coletado_em)
);

CREATE INDEX IF NOT EXISTS idx_visits_listing ON listing_visits(listing_id, coletado_em);




-- Edições feitas pelo OPERADOR no lado verde do editor. É o material de
-- aprendizado: mostra o que ele mudou em relação ao que a IA propôs, e vira
-- referência de estilo nas próximas gerações.
CREATE TABLE IF NOT EXISTS edits (
    id            INTEGER PRIMARY KEY,
    listing_id    INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    suggestion_id INTEGER REFERENCES suggestions(id) ON DELETE SET NULL,
    field         TEXT NOT NULL,          -- description | title | attribute:<ID>
    ia_value      TEXT,                   -- o que a IA propôs
    final_value   TEXT NOT NULL,          -- o que o operador deixou
    -- 1 quando o operador mexeu de fato no texto da IA; 0 quando aprovou como veio
    alterado      INTEGER NOT NULL DEFAULT 1,
    operator      TEXT NOT NULL DEFAULT 'ceo',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_edits_listing ON edits(listing_id, field);
CREATE INDEX IF NOT EXISTS idx_edits_alterado ON edits(alterado, created_at);

CREATE TABLE IF NOT EXISTS applications (
    id             INTEGER PRIMARY KEY,
    suggestion_id  INTEGER NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
    listing_id     INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    payload_sent   TEXT,
    response       TEXT,
    result         TEXT NOT NULL,   -- applied|rejected|divergent|error
    verified_at    TEXT,
    verification   TEXT,            -- diff entre esperado e relido
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY,
    platform    TEXT NOT NULL,
    version     TEXT NOT NULL,
    body        TEXT NOT NULL,     -- JSON das regras
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, version)
);

-- Fila de trabalho: permite retomar coleta interrompida sem perder progresso.
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,     -- collect|refresh_token|apply
    account_id  INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
    payload     TEXT,
    status      TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    run_after   TEXT NOT NULL DEFAULT (datetime('now')),
    last_error  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, run_after);

-- OAuth: state de uso único, para o callback público não aceitar link repetido.
CREATE TABLE IF NOT EXISTS oauth_states (
    state       TEXT PRIMARY KEY,
    platform    TEXT NOT NULL,
    alias       TEXT NOT NULL,
    -- PKCE: o code_verifier nasce junto com o state, é enviado (hasheado) na
    -- URL de autorização e conferido na troca do code. Sem guardá-lo aqui a
    -- troca falha quando o app do ML tem PKCE habilitado.
    code_verifier TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    used_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_alias ON oauth_states(platform, alias);
