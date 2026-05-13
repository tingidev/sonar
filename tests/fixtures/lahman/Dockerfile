FROM postgres:16-alpine

RUN apk add --no-cache curl

COPY schema.sql /schema/schema.sql
COPY load-lahman.sh /docker-entrypoint-initdb.d/90-load-lahman.sh
RUN chmod +x /docker-entrypoint-initdb.d/90-load-lahman.sh
