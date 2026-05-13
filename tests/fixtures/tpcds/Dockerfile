FROM python:3.12-slim AS generator

RUN pip install --no-cache-dir duckdb

COPY generate.py /generate.py
RUN python3 /generate.py

FROM postgres:16-alpine

COPY --from=generator /data/ /data/
COPY tpcds.sql /schema/tpcds.sql
COPY load-tpcds.sh /docker-entrypoint-initdb.d/90-load-tpcds.sh
RUN chmod +x /docker-entrypoint-initdb.d/90-load-tpcds.sh
