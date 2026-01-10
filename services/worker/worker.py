import os
from psycopg import connect

DB_CFG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "activelog"),
    "user": os.getenv("DB_USER", "activelog"),
    "password": os.getenv("DB_PASSWORD", "supersecretlocal")
}

def main():
    conninfo = f"host={DB_CFG['host']} port={DB_CFG['port']} dbname={DB_CFG['dbname']} user={DB_CFG['user']} password={DB_CFG['password']}"
    with connect(conninfo) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM orgs;")
            count = cur.fetchone()[0]
            print(f"Org count: {count}")

if __name__ == "__main__":
    main()