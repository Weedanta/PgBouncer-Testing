# PgBouncer on the same Debian base as PostgreSQL. The official postgres image ships
# with the PGDG apt repository, which carries current PgBouncer releases.
ARG POSTGRES_IMAGE
FROM ${POSTGRES_IMAGE}
ARG PGBOUNCER_VERSION_PREFIX
RUN set -eux; \
    apt-get update; \
    ver="$(apt-cache madison pgbouncer | awk '{print $3}' | grep "^${PGBOUNCER_VERSION_PREFIX}" | sort -V | tail -n1)"; \
    if [ -z "$ver" ]; then echo "no pgbouncer ${PGBOUNCER_VERSION_PREFIX}* in apt"; apt-cache madison pgbouncer; exit 1; fi; \
    apt-get install -y --no-install-recommends "pgbouncer=${ver}"; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /var/log/pgbouncer /etc/pgbouncer; \
    chown -R postgres:postgres /var/log/pgbouncer /etc/pgbouncer; \
    pgbouncer --version | tee /pgbouncer-version.txt
USER postgres
EXPOSE 6432
# Single process, foreground (PgBouncer is single-threaded by design).
CMD ["pgbouncer", "/etc/pgbouncer/pgbouncer.ini"]
