CREATE TABLE IF NOT EXISTS purchases (
    session_hash TEXT PRIMARY KEY,
    payment_intent TEXT NOT NULL,
    release TEXT NOT NULL,
    platform TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS revoked_payments (
    payment_intent TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL
);
