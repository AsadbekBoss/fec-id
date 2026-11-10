-- One-time PostgreSQL initialisation for AI Employee Attendance. Idempotent (safe to re-run).
-- Run as a PostgreSQL superuser, normally via `npm run db:init`, which sets:
--   APP_DB_NAME, APP_DB_USER, APP_DB_PASSWORD (from apps/api/.env), APP_DB_CREATEDB (true/false)
-- Manual use:
--   set APP_DB_NAME=attendance & set APP_DB_USER=attendance & set APP_DB_PASSWORD=... & set APP_DB_CREATEDB=false
--   psql -h localhost -U postgres -f tools/db-init.sql
-- Tables are NOT created here. They are created by Alembic migrations (`npm run db:migrate`).

\set ON_ERROR_STOP on
\getenv app_db APP_DB_NAME
\getenv app_user APP_DB_USER
\getenv app_password APP_DB_PASSWORD
\getenv app_createdb APP_DB_CREATEDB

-- 1. Application role: no superuser, no role creation. CREATEDB only for dev (test databases).
SELECT format('CREATE ROLE %I LOGIN', :'app_user')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEROLE %s PASSWORD %L',
              :'app_user',
              CASE WHEN :'app_createdb' = 'true' THEN 'CREATEDB' ELSE 'NOCREATEDB' END,
              :'app_password') \gexec

-- 2. Database owned by the application role, UTF-8.
SELECT format('CREATE DATABASE %I OWNER %I ENCODING %L TEMPLATE template0',
              :'app_db', :'app_user', 'UTF8')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'app_db') \gexec
SELECT format('ALTER DATABASE %I SET timezone TO %L', :'app_db', 'UTC') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'app_db') \gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'app_db', :'app_user') \gexec

-- 3. Inside the database: extensions (also created by the migration; harmless here) and schema.
\connect :"app_db"
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'app_user') \gexec

\echo 'Database initialised. Next: npm run db:migrate'
