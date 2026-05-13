FROM postgres:16-alpine

RUN apk add --no-cache wget

COPY load-adventureworks.sh /docker-entrypoint-initdb.d/90-load-adventureworks.sh
RUN chmod +x /docker-entrypoint-initdb.d/90-load-adventureworks.sh
