"""Provider-independent database onboarding and schema creation."""
from pathlib import Path
import shutil
from .contracts import DatabaseError, DatabaseProvider, validate_database_handoff
from .progress import creating, completed
from . import postgres

PROVIDERS: dict[str, DatabaseProvider] = {}
SCHEMA_FILE = Path(__file__).resolve().parents[2] / 'sql' / 'schema.sql'


def schema_source(schema_file=SCHEMA_FILE):
    try:
        sql = schema_file.read_text()
    except OSError:
        raise DatabaseError('Cannot read schema file: ' + str(schema_file)) from None
    marker = '-- dialect: '
    first_line = sql.splitlines()[0] if sql.splitlines() else ''
    if not first_line.startswith(marker) or not first_line[len(marker):].strip():
        raise DatabaseError(str(schema_file) + ' must declare its SQL dialect on the first line: '
                            '-- dialect: postgresql')
    if not any(line.strip() and not line.lstrip().startswith('--') for line in sql.splitlines()[1:]):
        raise DatabaseError('The table creation script contains no DDL: ' + str(schema_file))
    return first_line[len(marker):].strip(), sql


def preflight(provider):
    if provider not in PROVIDERS:
        raise DatabaseError('Unsupported database provider: ' + provider)
    dialect, _ = schema_source()
    engine = getattr(PROVIDERS[provider], 'ENGINE', None)
    if not isinstance(engine, str) or engine != dialect or dialect != 'postgresql':
        raise DatabaseError(
            'Cannot provision database provider ' + provider + ': schema.sql requires '
            + dialect + '; provider engine is ' + (engine if isinstance(engine, str) else 'undeclared')
            + '. Select a compatible PostgreSQL provider. No provisioning was started.')


def create_tables(handoff, schema_file=SCHEMA_FILE, *, reset=False):
    """Execute provider-independent DDL atomically with the admin connection."""
    validate_database_handoff(handoff)
    dialect, sql = schema_source(schema_file)
    if handoff['common']['engine'] != dialect:
        raise DatabaseError('schema.sql requires ' + dialect + '; database engine is '
                            + handoff['common']['engine'] + '.')
    creating('application schema', str(schema_file.name))
    reset_sql = '''
DO $reset$
DECLARE tables text;
BEGIN
    SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ') INTO tables
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind IN ('r', 'p')
      AND c.relname <> 'ajss_onboarding_probe';
    IF tables IS NOT NULL THEN
        EXECUTE 'DROP TABLE ' || tables || ' CASCADE';
    END IF;
END $reset$;
''' if reset else ''
    try:
        postgres.psql(handoff['common']['connections']['admin']['url'],
                      'BEGIN;\nSELECT pg_advisory_xact_lock(19470922);\n' + reset_sql + sql + '\nCOMMIT;')
    except DatabaseError as exc:
        raise DatabaseError('Could not apply ' + str(schema_file) + ' to '
                            + handoff['common']['engine'] + '. The schema transaction was not committed. '
                            + str(exc)) from None
    completed('application schema', str(schema_file.name))


def provision_database(state, root, command, save, checkpoint, provider='docker-postgres', *, reset=False):
    preflight(provider)
    if not shutil.which('psql'):
        raise DatabaseError('Install the PostgreSQL client (psql) before running database onboarding.')
    saved = state.get('database', {})
    if saved.get('provider') not in (None, 'neon', provider):
        raise DatabaseError('Database provider differs from the saved binding; refusing to retarget it.')
    state['status'] = 'database_initializing'
    checkpoint()
    handoff = PROVIDERS[provider].provision(state, root, command, checkpoint)
    validate_database_handoff(handoff)
    create_tables(handoff, reset=reset)
    # The adapter verifies runtime grants and platform-specific isolation rules.
    PROVIDERS[provider].verify(handoff)
    path = root / '.onboarding' / 'database.json'
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existed = path.exists()
    creating('credential file', '.onboarding/database.json')
    save(path, handoff)
    completed('credential file', '.onboarding/database.json', existed=existed)
    # Remove the obsolete local export, without dropping existing remote roles.
    legacy = root / '.onboarding' / 'mcp-database.json'
    if legacy.exists():
        legacy.unlink()
    state['database'] = handoff
    state['status'] = 'ready'
    checkpoint()
    print('Database, application tables, and app/admin credentials ready.', flush=True)
    return handoff
