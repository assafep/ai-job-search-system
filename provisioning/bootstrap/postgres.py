"""PostgreSQL role setup, SQL execution, and permission verification."""
import json
import os
import re
import secrets
import ssl
import subprocess
import urllib.parse
from .progress import creating, completed
from .contracts import DatabaseError

DATABASE_NAME = 'ajss-db'
APP_ROLE = 'ajss_app'


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def connection_url(url, database, role=None, password=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname:
        raise DatabaseError('Marketplace did not supply a valid Postgres URL.')
    if role is None:
        userinfo = parsed.netloc.rsplit('@', 1)[0]
    else:
        userinfo = urllib.parse.quote(role, safe='') + ':' + urllib.parse.quote(password, safe='')
    host = parsed.hostname
    if ':' in host:
        host = '[' + host + ']'
    if parsed.port:
        host += ':' + str(parsed.port)
    return urllib.parse.urlunsplit((parsed.scheme, userinfo + '@' + host,
                                   '/' + urllib.parse.quote(database, safe=''),
                                   'sslmode=require', ''))


def psql(url, sql, expected_error=None):
    """Keep passwords and SQL off argv, shell history, and error output."""
    parsed = urllib.parse.urlsplit(url)
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    env.update(PGHOST=parsed.hostname or '', PGPORT=str(parsed.port or 5432),
               PGUSER=urllib.parse.unquote(parsed.username or ''),
               PGPASSWORD=urllib.parse.unquote(parsed.password or ''),
               PGDATABASE=urllib.parse.unquote(parsed.path.lstrip('/')),
               PGCONNECT_TIMEOUT='30', PGSSLMODE='verify-full')
    ca = ssl.get_default_verify_paths().cafile
    if not ca:
        raise DatabaseError('A system CA certificate bundle is required for verified database TLS.')
    env['PGSSLROOTCERT'] = ca
    result = subprocess.run(['psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1'],
                            input='\\set VERBOSITY sqlstate\n' + sql, text=True, env=env, capture_output=True)
    if expected_error:
        if result.returncode and ('ERROR:  ' + expected_error in result.stderr or
                                  'ERROR: ' + expected_error in result.stderr):
            return ''
        raise DatabaseError('A forbidden database operation did not return the expected permission denial.')
    if result.returncode:
        # psql errors can contain SQL literals, passwords, or connection strings.
        match = re.search(r'ERROR:\s+([0-9A-Z]{5})\b', result.stderr)
        code = ' (SQLSTATE ' + match.group(1) + ')' if match else ''
        raise DatabaseError('Database SQL/permission verification failed' + code + '. No SQL or credentials '
                            'were printed. Check database availability and owner privileges, then rerun.')
    return result.stdout.strip()


def initialize_sql(project_id):
    """Transactionally persist passwords with roles so lost local state is harmless."""
    app_password = secrets.token_urlsafe(40)
    return f"""
BEGIN;
SELECT pg_advisory_xact_lock(19470922);
SELECT json_build_object('entity_statuses', json_build_array(
    json_build_object('type', 'schema', 'name', 'ajss_bootstrap', 'existed',
        EXISTS (SELECT FROM pg_namespace WHERE nspname='ajss_bootstrap')),
    json_build_object('type', 'table', 'name', 'ajss_bootstrap.credentials', 'existed',
        to_regclass('ajss_bootstrap.credentials') IS NOT NULL),
    json_build_object('type', 'database role', 'name', '{APP_ROLE}', 'existed',
        EXISTS (SELECT FROM pg_roles WHERE rolname='{APP_ROLE}')),
    json_build_object('type', 'table', 'name', 'public.ajss_onboarding_probe', 'existed',
        to_regclass('public.ajss_onboarding_probe') IS NOT NULL)
));
CREATE SCHEMA IF NOT EXISTS ajss_bootstrap;
REVOKE ALL ON SCHEMA ajss_bootstrap FROM PUBLIC;
CREATE TABLE IF NOT EXISTS ajss_bootstrap.credentials (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    project_id text NOT NULL, app_password text NOT NULL
);
REVOKE ALL ON ALL TABLES IN SCHEMA ajss_bootstrap FROM PUBLIC;
-- Earlier onboarding versions required an MCP password column. Keep existing
-- records intact, but allow new app-only records if that legacy column exists.
DO $legacy$
BEGIN
    IF EXISTS (SELECT FROM information_schema.columns WHERE table_schema='ajss_bootstrap'
               AND table_name='credentials' AND column_name='mcp_password') THEN
        ALTER TABLE ajss_bootstrap.credentials ALTER COLUMN mcp_password SET DEFAULT '';
    END IF;
END $legacy$;
INSERT INTO ajss_bootstrap.credentials (singleton, project_id, app_password) VALUES
    (true, {literal(project_id)}, {literal(app_password)})
    ON CONFLICT (singleton) DO NOTHING;
DO $setup$
DECLARE credentials ajss_bootstrap.credentials%ROWTYPE;
BEGIN
    SELECT * INTO STRICT credentials FROM ajss_bootstrap.credentials;
    IF credentials.project_id <> {literal(project_id)} THEN
        RAISE EXCEPTION 'Database belongs to a different Vercel project';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        EXECUTE format('CREATE ROLE %I LOGIN NOINHERIT NOCREATEDB NOCREATEROLE PASSWORD %L',
                       '{APP_ROLE}', credentials.app_password);
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname IN ('{APP_ROLE}')
               AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
       OR EXISTS (SELECT FROM pg_auth_members WHERE member IN
                  (SELECT oid FROM pg_roles WHERE rolname IN ('{APP_ROLE}')))
       OR EXISTS (SELECT FROM pg_database WHERE datname = current_database() AND datdba IN
                  (SELECT oid FROM pg_roles WHERE rolname IN ('{APP_ROLE}'))) THEN
        RAISE EXCEPTION 'Existing runtime role is privileged; refusing to reuse it';
    END IF;
END $setup$;
-- Lock every other database this owner administers.
DO $isolate$
DECLARE other name;
BEGIN
    FOR other IN
        SELECT datname FROM pg_database
        WHERE NOT datistemplate AND datallowconn AND datname <> current_database()
          AND datdba = (SELECT oid FROM pg_roles WHERE rolname = current_user)
    LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC, %I',
                       other, '{APP_ROLE}');
    END LOOP;
END
$isolate$;
REVOKE ALL ON DATABASE {identifier(DATABASE_NAME)} FROM PUBLIC;
GRANT CONNECT ON DATABASE {identifier(DATABASE_NAME)} TO {APP_ROLE};
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO {APP_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE};
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, {APP_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLES TO {APP_ROLE};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT
    ON SEQUENCES TO {APP_ROLE};
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
CREATE TABLE IF NOT EXISTS public.ajss_onboarding_probe (id text PRIMARY KEY, value text NOT NULL);
GRANT SELECT, INSERT, UPDATE, DELETE ON public.ajss_onboarding_probe TO {APP_ROLE};
COMMIT;
SELECT row_to_json(c) FROM ajss_bootstrap.credentials c;
"""


def verify_role(url, role, owner, database=DATABASE_NAME, isolation_exempt_owner=None):
    """Test DML with rollback; inspect effective permissions without attempting DROP."""
    sql = f"""
SELECT json_build_object('role', current_user, 'database', current_database(),
 'restricted', NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls),
 'can_create', has_database_privilege(current_user, current_database(), 'CREATE'),
 'can_temp', has_database_privilege(current_user, current_database(), 'TEMP'),
 'can_create_schema_objects', has_schema_privilege(current_user, 'public', 'CREATE'),
 'can_read_credentials', has_schema_privilege(current_user, 'ajss_bootstrap', 'USAGE'),
 'owns_database', EXISTS (SELECT FROM pg_database WHERE datname=current_database() AND datdba=r.oid),
 'memberships', EXISTS (SELECT FROM pg_auth_members WHERE member=r.oid),
 'other_database_access', EXISTS (
    SELECT FROM pg_database d
    WHERE NOT d.datistemplate AND d.datallowconn AND d.datname <> current_database()
      AND has_database_privilege(current_user, d.oid, 'CONNECT')
      AND pg_get_userbyid(d.datdba) IS DISTINCT FROM {literal(isolation_exempt_owner) if isolation_exempt_owner else 'NULL'}),
 'owns_objects', EXISTS (SELECT FROM pg_class WHERE relowner=r.oid)
                 OR EXISTS (SELECT FROM pg_namespace WHERE nspowner=r.oid)
                 OR EXISTS (SELECT FROM pg_proc WHERE proowner=r.oid))
FROM pg_roles r WHERE rolname=current_user;
BEGIN;
INSERT INTO public.ajss_onboarding_probe VALUES ('{role}', 'inserted')
ON CONFLICT (id) DO UPDATE SET value=excluded.value;
UPDATE public.ajss_onboarding_probe SET value='updated' WHERE id='{role}';
SELECT value FROM public.ajss_onboarding_probe WHERE id='{role}';
DELETE FROM public.ajss_onboarding_probe WHERE id='{role}';
ROLLBACK;
"""
    lines = psql(url, sql).splitlines()
    result = json.loads(next(line for line in lines if line.startswith('{')))
    denied = ('can_create', 'can_temp', 'can_create_schema_objects', 'can_read_credentials',
              'owns_database', 'memberships', 'other_database_access', 'owns_objects')
    failed = [key for key in denied if result.get(key)]
    if not result.get('restricted'):
        failed.append('not_restricted')
    if result.get('role') != role or result.get('database') != database or role == owner:
        failed.append('wrong_role_or_database')
    if 'updated' not in lines:
        failed.append('dml')
    if failed:
        raise DatabaseError('A runtime database role failed the least-privilege verification: '
                            + ', '.join(failed) + '.')
    for forbidden in (
            'CREATE TABLE public.ajss_forbidden_probe (id integer)',
            'DROP TABLE public.ajss_onboarding_probe',
            'TRUNCATE public.ajss_onboarding_probe',
            'SELECT * FROM ajss_bootstrap.credentials',
            'SET ROLE ' + identifier(owner)):
        psql(url, 'BEGIN;\n' + forbidden + ';\nROLLBACK;', expected_error='42501')


def configure_roles(owner_url, project_id, isolation_exempt_owner=None):
    source = urllib.parse.urlsplit(owner_url)
    source_database = urllib.parse.unquote(source.path.lstrip('/'))
    # A dedicated Marketplace resource is used. Prevent PUBLIC access to its
    # original default database before granting only the named application's DB.
    sql = f"""
SELECT pg_advisory_lock(19470922);
SELECT json_build_object('database_existed',
    EXISTS (SELECT FROM pg_database WHERE datname={literal(DATABASE_NAME)}));
SELECT format('CREATE DATABASE %I', {literal(DATABASE_NAME)})
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname={literal(DATABASE_NAME)})
\\gexec
REVOKE CONNECT ON DATABASE {identifier(source_database)} FROM PUBLIC;
"""
    creating('database', DATABASE_NAME)
    result = psql(owner_url, sql)
    database_status = json.loads(next(line for line in result.splitlines() if line.startswith('{')))
    completed('database', DATABASE_NAME, existed=database_status['database_existed'])
    target = connection_url(owner_url, DATABASE_NAME)
    creating('schema', 'ajss_bootstrap')
    output = psql(target, initialize_sql(project_id))
    statuses = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
    for index, status in enumerate(next(item['entity_statuses'] for item in statuses if 'entity_statuses' in item)):
        if index:
            creating(status['type'], status['name'])
        completed(status['type'], status['name'], existed=status['existed'])
    credentials = json.loads(next(line for line in reversed(output.splitlines()) if line.startswith('{')))
    app_url = connection_url(owner_url, DATABASE_NAME, APP_ROLE, credentials['app_password'])
    verify_role(app_url, APP_ROLE, urllib.parse.unquote(source.username or ''),
                isolation_exempt_owner=isolation_exempt_owner)
    return {'app': app_url, 'admin': target}
