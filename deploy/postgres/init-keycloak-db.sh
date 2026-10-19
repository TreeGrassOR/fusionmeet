#!/bin/sh
# Creates the Keycloak database and role next to the application database.
# Run by the postgres image entrypoint on the first start only (empty data volume).
set -eu

: "${KEYCLOAK_DB_PASSWORD:?KEYCLOAK_DB_PASSWORD is not set}"

# The password goes through a psql variable (:'kc_password'), so it is quoted
# by psql itself.
psql -v ON_ERROR_STOP=1 -v kc_password="$KEYCLOAK_DB_PASSWORD" \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
CREATE ROLE keycloak LOGIN PASSWORD :'kc_password';
CREATE DATABASE keycloak OWNER keycloak;
EOSQL
