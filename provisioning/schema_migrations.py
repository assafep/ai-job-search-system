"""Apply numbered SQL migrations that the baseline schema does not include.

The application role never runs DDL. This uses the admin connection saved by
host provisioning and records each file in ajss_schema_migrations.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = 'ajss_schema_migrations'


class MigrationError(Exception):
    pass


def migration_files(repo=REPO):
    directory = repo / 'sql' / 'migrations'
    return tuple(sorted(path for path in directory.glob('*.sql') if path.is_file()))


def psql(url, sql):
    completed = subprocess.run(
        ['psql', url, '-v', 'ON_ERROR_STOP=1', '-q', '-t', '-A'],
        input=sql, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or 'psql failed').replace(url, '<database>')
        raise MigrationError(detail.strip().splitlines()[-1][:400])
    return completed.stdout


def apply_migrations(admin_url, app_role, repo=REPO):
    if not admin_url or not app_role:
        raise MigrationError('Database admin credentials are missing. Rerun host provisioning.')
    role = '"' + app_role.replace('"', '""') + '"'
    files = migration_files(repo)
    psql(admin_url, (
        'CREATE TABLE IF NOT EXISTS public.%s ('
        'name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());'
        'REVOKE ALL ON public.%s FROM %s;'
    ) % (LEDGER, LEDGER, role))
    applied = []
    for path in files:
        name = path.name.replace("'", "''")
        existing = psql(admin_url, "SELECT name FROM public.%s WHERE name='%s';" % (LEDGER, name)).strip()
        if existing:
            continue
        grant = (
            'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %s;'
            'GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO %s;'
            'REVOKE ALL ON public.%s FROM %s;'
        ) % (role, role, LEDGER, role)
        psql(admin_url, 'BEGIN;\n' + path.read_text() + '\n' + grant +
             "\nINSERT INTO public.%s(name) VALUES ('%s');\nCOMMIT;" % (LEDGER, name))
        applied.append(path.name)
    return applied
