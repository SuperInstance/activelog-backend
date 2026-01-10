-- Extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- Role and DB setup
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'activelog') THEN
    CREATE ROLE activelog WITH LOGIN PASSWORD 'supersecretlocal';
  END IF;
END $$;

-- Optional: create DB if not already running inside it
-- CREATE DATABASE activelog OWNER activelog;

-- Tenancy
CREATE TABLE orgs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'free',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email CITEXT UNIQUE NOT NULL,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE org_memberships (
  org_id uuid REFERENCES orgs(id) ON DELETE CASCADE,
  user_id uuid REFERENCES users(id) ON DELETE CASCADE,
  role TEXT CHECK (role IN ('owner','admin','creator','consumer','guardian')),
  PRIMARY KEY (org_id, user_id)
);

-- Wallets and ledger
CREATE TABLE wallets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid REFERENCES orgs(id) ON DELETE CASCADE,
  owner_type TEXT CHECK (owner_type IN ('org','user')) NOT NULL,
  owner_id uuid NOT NULL,
  balance_cc NUMERIC(18,4) NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ledger_entries (
  id bigserial PRIMARY KEY,
  org_id uuid REFERENCES orgs(id) ON DELETE CASCADE,
  wallet_id uuid REFERENCES wallets(id),
  entry_type TEXT CHECK (entry_type IN ('credit','debit','hold','release','fee')) NOT NULL,
  amount_cc NUMERIC(18,4) NOT NULL,
  ref_type TEXT,
  ref_id uuid,
  memo TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT amount_sign CHECK (
    (entry_type IN ('credit','release') AND amount_cc > 0) OR
    (entry_type IN ('debit','hold','fee') AND amount_cc < 0)
  )
);

-- Pricing and metering
CREATE TABLE pricebooks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version INT NOT NULL UNIQUE,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE pricebook_items (
  pricebook_id uuid REFERENCES pricebooks(id) ON DELETE CASCADE,
  sku TEXT NOT NULL,
  rate_cc NUMERIC(18,6) NOT NULL,
  PRIMARY KEY (pricebook_id, sku)
);

CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid REFERENCES orgs(id) ON DELETE CASCADE,
  owner_wallet uuid REFERENCES wallets(id),
  pricebook_version INT NOT NULL,
  budget_cc NUMERIC(18,4) NOT NULL,
  consumed_cc NUMERIC(18,4) NOT NULL DEFAULT 0,
  state TEXT CHECK (state IN ('draft','running','settling','completed','failed','refunded')) NOT NULL DEFAULT 'running',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE usage_events (
  id bigserial PRIMARY KEY,
  org_id uuid REFERENCES orgs(id) ON DELETE CASCADE,
  job_id uuid REFERENCES jobs(id) ON DELETE CASCADE,
  sku TEXT NOT NULL,
  quantity NUMERIC(18,6) NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  trace_id TEXT
);

CREATE TABLE escrows (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid REFERENCES orgs(id) ON DELETE CASCADE,
  job_id uuid REFERENCES jobs(id) ON DELETE CASCADE,
  wallet_id uuid REFERENCES wallets(id),
  amount_cc NUMERIC(18,4) NOT NULL,
  remaining_cc NUMERIC(18,4) NOT NULL,
  state TEXT CHECK (state IN ('held','settled','released','exhausted')) NOT NULL DEFAULT 'held',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Optional meter offsets
CREATE TABLE job_meters (
  job_id uuid PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  last_event_id bigint NOT NULL DEFAULT 0,
  total_cost_cc NUMERIC(18,4) NOT NULL DEFAULT 0
);

-- RLS: set app.org_id per request/connection
ALTER TABLE wallets ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE escrows ENABLE ROW LEVEL SECURITY;

CREATE POLICY wallets_by_org ON wallets
  USING (org_id::text = current_setting('app.org_id', true));

CREATE POLICY ledger_by_org ON ledger_entries
  USING (org_id::text = current_setting('app.org_id', true));

CREATE POLICY jobs_by_org ON jobs
  USING (org_id::text = current_setting('app.org_id', true));

CREATE POLICY usage_by_org ON usage_events
  USING (org_id::text = current_setting('app.org_id', true));

CREATE POLICY escrows_by_org ON escrows
  USING (org_id::text = current_setting('app.org_id', true));

-- Convenience view for wallet balances
CREATE OR REPLACE VIEW wallet_balances AS
SELECT w.id AS wallet_id, w.org_id,
       COALESCE(SUM(le.amount_cc), 0)::NUMERIC(18,4) AS balance_cc
FROM wallets w
LEFT JOIN ledger_entries le ON le.wallet_id = w.id
GROUP BY w.id, w.org_id;