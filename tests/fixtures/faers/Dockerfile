FROM postgres:16-alpine

RUN apk add --no-cache curl unzip

COPY load-faers.sh /docker-entrypoint-initdb.d/90-load-faers.sh
RUN chmod +x /docker-entrypoint-initdb.d/90-load-faers.sh
