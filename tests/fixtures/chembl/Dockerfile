FROM postgres:16-alpine

RUN apk add --no-cache curl

COPY load-chembl.sh /docker-entrypoint-initdb.d/90-load-chembl.sh
RUN chmod +x /docker-entrypoint-initdb.d/90-load-chembl.sh
