import os
from decimal import Decimal
from typing import List, Optional
from fastapi import FastAPI, Depends, Header, HTTPException
from pydantic import BaseModel
import psycopg

DB_CFG = dict(
    host=os.getenv("POSTGRES_HOST", "db"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    dbname=os.getenv("POSTGRES_DB", "activelog"),
    user=os.getenv("POSTGRES_USER", "activelog"),
    password=os.getenv("POSTGRES_PASSWORD", "changeme"),
)
POOL = psycopg.ConnectionPool(f"host={DB_CFG['host']} port={DB_CFG['port']} dbname={DB_CFG['dbname']} user={DB_CFG['user']} password={DB_CFG['password']}")

app = FastAPI(title="ActiveLog API", version="0.1.0")

async def with_conn(org_id: Optional[str] = Header(None, convert_underscores=False)):
    if not org_id:
        raise HTTPException(status_code=400, detail="X-Org-Id header required")
    conn = POOL.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("select set_config('app.org_id', %s, true);", (org_id,))
        yield conn
    finally:
        POOL.putconn(conn)

class OrgIn(BaseModel):
    name: str

class WalletIn(BaseModel):
    owner_type: str  # 'org' | 'user'
    owner_id: str

class CreditIn(BaseModel):
    amount_cc: Decimal
    memo: Optional[str] = None

class PricebookSeedIn(BaseModel):
    version: int = 1
    items: dict  # {"cpu.sec": "0.001", "token.in": "0.00001", ...}

class JobIn(BaseModel):
    owner_wallet: str
    pricebook_version: int
    budget_cc: Decimal

class UsageEvent(BaseModel):
    job_id: str
    sku: str
    quantity: Decimal
    at: Optional[str] = None
    trace_id: Optional[str] = None

@app.post("/orgs")
def create_org(body: OrgIn):
    with psycopg.connect(**DB_CFG) as conn, conn.cursor() as cur:
        cur.execute("insert into orgs (name) values (%s) returning id;", (body.name,))
        org_id = cur.fetchone()[0]
    return {"id": org_id, "name": body.name}

@app.post("/orgs/{org_id}/wallets")
def create_wallet(org_id: str, body: WalletIn, conn=Depends(with_conn)):
    with conn.cursor() as cur:
        cur.execute("""
          insert into wallets (org_id, owner_type, owner_id)
          values (%s,%s,%s) returning id;
        """, (org_id, body.owner_type, body.owner_id))
        wid = cur.fetchone()[0]
    return {"id": wid}

@app.post("/wallets/{wallet_id}/credit")
def credit_wallet(wallet_id: str, body: CreditIn, conn=Depends(with_conn)):
    amt = Decimal(body.amount_cc)
    if amt <= 0:
        raise HTTPException(400, "amount_cc must be positive")
    with conn.cursor() as cur:
        # Find org for wallet and insert ledger entry
        cur.execute("select org_id from wallets where id=%s;", (wallet_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "wallet not found")
        org_id = row[0]
        cur.execute("""
          insert into ledger_entries (org_id, wallet_id, entry_type, amount_cc, ref_type, memo)
          values (%s,%s,'credit',%s,'manual',%s);
        """, (org_id, wallet_id, amt, body.memo or 'credit'))
    conn.commit()
    return {"wallet_id": wallet_id, "credited_cc": str(amt)}

@app.post("/pricebooks/seed")
def seed_pricebook(body: PricebookSeedIn):
    with psycopg.connect(**DB_CFG) as conn, conn.cursor() as cur:
        cur.execute("insert into pricebooks (version) values (%s) returning id;", (body.version,))
        pbid = cur.fetchone()[0]
        for sku, rate in body.items.items():
            cur.execute("""
              insert into pricebook_items (pricebook_id, sku, rate_cc)
              values (%s,%s,%s);
            """, (pbid, sku, Decimal(str(rate))))
        conn.commit()
    return {"pricebook_id": pbid, "version": body.version, "items": body.items}

@app.post("/orgs/{org_id}/jobs")
def create_job(org_id: str, body: JobIn, conn=Depends(with_conn)):
    with conn.cursor() as cur:
        # Create job
        cur.execute("""
          insert into jobs (org_id, owner_wallet, pricebook_version, budget_cc, state)
          values (%s,%s,%s,%s,'running') returning id;
        """, (org_id, body.owner_wallet, body.pricebook_version, body.budget_cc))
        job_id = cur.fetchone()[0]
        # Create escrow and hold ledger entry
        cur.execute("""
          insert into escrows (org_id, job_id, wallet_id, amount_cc, remaining_cc, state)
          values (%s,%s,%s,%s,%s,'held')
          returning id;
        """, (org_id, job_id, body.owner_wallet, body.budget_cc, body.budget_cc))
        escrow_id = cur.fetchone()[0]
        cur.execute("""
          insert into ledger_entries (org_id, wallet_id, entry_type, amount_cc, ref_type, ref_id, memo)
          values (%s,%s,'hold',%s,'escrow',%s,'job escrow');
        """, (org_id, body.owner_wallet, -body.budget_cc, job_id))
        # Initialize job meter
        cur.execute("insert into job_meters (job_id) values (%s);", (job_id,))
    conn.commit()
    return {"job_id": job_id, "escrow_id": escrow_id}

@app.post("/usage_events")
def ingest_usage(events: List[UsageEvent], conn=Depends(with_conn)):
    with conn.cursor() as cur:
        for e in events:
            cur.execute("select org_id, pricebook_version from jobs where id=%s;", (e.job_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(400, f"invalid job_id {e.job_id}")
            org_id = row[0]
            cur.execute("""
              insert into usage_events (org_id, job_id, sku, quantity, at, trace_id)
              values (%s,%s,%s,%s,coalesce(%s, now()),%s);
            """, (org_id, e.job_id, e.sku, e.quantity, e.at, e.trace_id))
    conn.commit()
    return {"accepted": len(events)}

@app.post("/jobs/{job_id}/complete")
def complete_job(job_id: str, conn=Depends(with_conn)):
    with conn.cursor() as cur:
        # Mark settling; worker will release unused escrow
        cur.execute("update jobs set state='settling' where id=%s;", (job_id,))
    conn.commit()
    return {"job_id": job_id, "state": "settling"}

@app.get("/wallets/{wallet_id}/balance")
def wallet_balance(wallet_id: str, conn=Depends(with_conn)):
    with conn.cursor() as cur:
        cur.execute("select balance_cc from wallet_balances where wallet_id=%s;", (wallet_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "wallet not found or no entries")
        return {"wallet_id": wallet_id, "balance_cc": str(row[0])}