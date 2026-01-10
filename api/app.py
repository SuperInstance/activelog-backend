from fastapi import FastAPI
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool
import os

app = FastAPI()

# Load DB config from env
DB_CFG = {
    "host": os.getenv("DB_HOST", "activelog-db"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "activelog"),
    "user": os.getenv("DB_USER", "activelog"),
    "password": os.getenv("DB_PASSWORD", "supersecretlocal")
}

POOL = ConnectionPool(conninfo=f"host={DB_CFG['host']} port={DB_CFG['port']} dbname={DB_CFG['dbname']} user={DB_CFG['user']} password={DB_CFG['password']}")

@app.get("/health")
def health():
    return JSONResponse(content={"status": "ok"})