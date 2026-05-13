import duckdb
import os

os.makedirs("/data", exist_ok=True)

con = duckdb.connect()
con.execute("INSTALL tpcds; LOAD tpcds")
con.execute("CALL dsdgen(sf=0.01)")

tables = con.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
).fetchall()

for (name,) in tables:
    con.execute(f"COPY {name} TO '/data/{name}.csv' (HEADER, DELIMITER '|')")
