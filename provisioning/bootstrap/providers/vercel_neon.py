"""Vercel Marketplace / Neon implementation of the database provider contract."""
import json
import os
import subprocess
import re
from ..contracts import DatabaseError, database_handoff
from ..postgres import DATABASE_NAME, configure_roles, verify_role
from ..progress import creating, completed

TEMP_PREFIX = 'AJSS_BOOTSTRAP_'
BINDING_KEY = 'AJSS_DATABASE_RESOURCE_ID'
ENGINE = 'postgresql'

def cli_failure_detail(output):
    """Classify diagnostics without echoing potentially secret CLI output."""
    if 'Invalid number of arguments' in output:
        return 'The command arguments are incompatible with the installed Vercel CLI.'
    if 'unexpected option' in output.lower() or 'unknown option' in output.lower():
        return 'The installed Vercel CLI rejected an unsupported option.'
    if 'billing plan' in output.lower() and any(word in output.lower() for word in ('invalid', 'not found', 'unknown')):
        return 'Neon rejected the billing plan ID; refresh the available plans with integration add neon --help.'
    if any(value in output for value in ('ENOTFOUND', 'EAI_AGAIN', 'fetch failed', 'getaddrinfo')):
        return 'Vercel could not be reached; check DNS/network access.'
    if any(value in output.lower() for value in ('token expired', 'invalid token', 'not authorized', 'unauthorized')):
        return 'Vercel authorization failed; check the saved login and account permissions.'
    return 'Check the installed CLI version, account permissions, and Marketplace terms.'


def parse_env(text):
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise DatabaseError('Could not parse the CLI environment export.') from None
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key] = value
    return values


class Vercel:
    def __init__(self, state, workspace, command):
        self.state, self.workspace, self.command = state, workspace, command

    def run(self, *args, value=None, progress=False):
        env = dict(os.environ)
        # Use the verified on-disk link and explicit scope. Environment-based
        # links can suppress project.json creation or override the workspace.
        for key in ('VERCEL_ORG_ID', 'VERCEL_PROJECT_ID', 'NOW_ORG_ID', 'NOW_PROJECT_ID'):
            env.pop(key, None)
        env['VERCEL_TOKEN'] = self.state['authentication']['access_token']
        try:
            result = subprocess.run(self.command(*args, '--scope', self.state['team_id']),
                                    cwd=self.workspace, env=env, input=value, text=True,
                                    stdout=subprocess.PIPE,
                                    stderr=None if progress else subprocess.PIPE)
        except FileNotFoundError:
            raise DatabaseError('Cannot start the Vercel CLI: its launcher or the database setup '
                                'workspace is missing. Check Node.js/npm or the installed CLI, then rerun.') from None
        if result.returncode and not ('--help' in args and result.returncode == 2):
            raise DatabaseError('Vercel CLI failed during ' + ' '.join(args[:3]) +
                                ': ' + cli_failure_detail((result.stderr or '') + '\n' + (result.stdout or '')) +
                                ' Progress is retained. Raw output is withheld because it may contain credentials.')
        return result.stdout

    def resources(self):
        return json.loads(self.run('integration', 'list', '--all', '--json'))['resources']

    def production_config_value(self, key):
        """Read a Config through the decrypt endpoint, never compare ciphertext.

        CLI env ls returns encrypted Config values without their environment IDs.
        Resolve the ID through the same management API used by hosting recovery.
        This method is for the non-secret resource marker, not runtime Secrets.
        """
        from .vercel import Client
        client = Client(self.state['authentication']['access_token'])
        path = '/v10/projects/' + self.state['project_id'] + '/env'
        result = client.request('GET', path, team=self.state['team_id'])
        matches = [v for v in result['envs'] if v['key'] == key
                   and 'production' in v.get('target', [])
                   and not v.get('gitBranch') and not v.get('customEnvironmentIds')]
        if len(matches) != 1 or not matches[0].get('id'):
            raise DatabaseError('Cannot resolve the production database binding marker.')
        variable = matches[0]
        if variable.get('type') == 'plain':
            return variable.get('value')
        if variable.get('type') != 'encrypted':
            raise DatabaseError('The database binding marker must be a readable Config variable.')
        from urllib.parse import quote
        value = client.request('GET', '/v1/projects/' + self.state['project_id']
                               + '/env/' + quote(variable['id'], safe=''),
                               team=self.state['team_id'])
        if value.get('decrypted') is not True or not isinstance(value.get('value'), str):
            raise DatabaseError('Cannot decrypt the database binding marker; no binding was changed.')
        return value['value']

    def link(self):
        link_file = self.workspace / '.vercel' / 'project.json'
        existed = link_file.is_file()
        creating('project link', self.state['project_name'])
        self.run('link', '--yes', '--project', self.state['project_id'])
        try:
            linked = json.loads((self.workspace / '.vercel' / 'project.json').read_text())
        except FileNotFoundError:
            raise DatabaseError('Vercel linking did not create '
                                '.onboarding/database-setup/.vercel/project.json. '
                                'Database provisioning has not started. Update the Vercel CLI and rerun; '
                                'saved cloud resources will be reused.') from None
        except json.JSONDecodeError:
            raise DatabaseError('The database workspace\'s .vercel/project.json contains invalid JSON. '
                                'Database provisioning has not started.') from None
        if not isinstance(linked, dict) or (linked.get('projectId'), linked.get('orgId')) != (self.state['project_id'], self.state['team_id']):
            raise DatabaseError('Vercel CLI linked a different project/team; stopped before provisioning.')
        self.run('project', 'inspect', '--non-interactive')
        completed('project link', self.state['project_name'], existed=existed)


def select_resource(resources):
    matches = [resource for resource in resources if resource['name'] == DATABASE_NAME]
    if len(matches) > 1:
        raise DatabaseError('Multiple Marketplace resources have the database name; resolve the ambiguity first.')
    if matches and 'neon' not in (matches[0].get('product') or '').lower():
        raise DatabaseError('The database resource name is already used by a non-Neon resource.')
    return matches[0] if matches else None


def free_plan(vc):
    """Discover the provider's plan ID; the display name 'Free' is not its ID."""
    help_text = vc.run('integration', 'add', 'neon', '--help')
    help_text = re.sub(r'\x1b\[[0-9;]*m', '', help_text)
    section = help_text.partition('Available billing plans for ')[2].split('Usage:', 1)[0]
    plans = re.findall(r'^\s+([\w-]+)\s+Free\s+\(Free\)\s*$', section, re.MULTILINE)
    if len(plans) != 1:
        raise DatabaseError('Could not identify exactly one Neon Free billing plan from '
                            'integration add neon --help. No resource was created.')
    return plans[0]


def owner_connection(vc, resource):
    """Temporarily bind owner credentials to Development, never to Production.

    Detach before publishing restricted app URLs. Retry discovers a binding left
    by an interrupted process instead of creating another resource.
    """
    name = vc.state['project_name']
    connected = name in (resource.get('projects') or [])
    other_projects = [p for p in (resource.get('projects') or []) if p != name]
    if other_projects:
        raise DatabaseError('The named database resource is connected to other Vercel projects; '
                            'refusing to repurpose it.')
    if connected:
        variables = json.loads(vc.run('env', 'ls', '--environment', 'development', '--json'))['envs']
        if not any(v['key'].startswith(TEMP_PREFIX) for v in variables):
            raise DatabaseError('The resource already has an unmanaged project connection. '
                                'Remove that owner-credential connection in Vercel before rerunning.')
    env_file = vc.workspace / 'owner.env.local'
    try:
        creating('temporary database connection', DATABASE_NAME)
        if not connected:
            vc.run('integration', 'resource', 'connect', DATABASE_NAME, name,
                   '--environment', 'development', '--prefix', TEMP_PREFIX, '--yes')
        completed('temporary database connection', DATABASE_NAME, existed=connected)
        vc.run('env', 'pull', str(env_file), '--environment=development', '--yes')
        values = parse_env(env_file.read_text())
        keys = (TEMP_PREFIX + 'DATABASE_URL_UNPOOLED', TEMP_PREFIX + 'POSTGRES_URL_NON_POOLING',
                TEMP_PREFIX + 'DATABASE_URL')
        url = next((values[k] for k in keys if values.get(k)), None)
        if not url:
            raise DatabaseError('The temporary Neon connection did not provide an owner URL. '
                                'No database credentials were published.')
        return url
    finally:
        # This also cleans up a connection left by a previous interrupted setup.
        # Never run a deployment while this temporary connection is present.
        try:
            resources = vc.resources()
            current = select_resource(resources)
            if current and name in (current.get('projects') or []):
                vc.run('integration', 'resource', 'disconnect', DATABASE_NAME, name, '--yes')
        finally:
            if env_file.exists():
                env_file.unlink()


def publish_url(vc, key, value, environment):
    # These are write-only Secrets in Production, recoverable only through the
    # owner SQL path above. Do not weaken them to readable Config variables.
    creating('environment variable', key)
    variables = json.loads(vc.run('env', 'ls', '--environment', environment, '--json'))
    variables = variables.get('envs', variables.get('environmentVariables', []))
    existing = [v for v in variables if v['key'] == key and not v.get('gitBranch')]
    if len(existing) > 1:
        raise DatabaseError('Ambiguous ' + key + ' variables; resolve them before continuing.')
    if existing:
        # Recoverable marker does not prove a write-only secret's value. Reapply
        # the exact same persisted password; never rotate the underlying role.
        if existing[0].get('type') != 'sensitive' and existing[0].get('visibility') != 'secret':
            raise DatabaseError(key + ' already exists as a readable Config variable. '
                                'Convert it to a Secret in Vercel before rerunning.')
        vc.run('env', 'update', key, '--environment', environment, '--yes', '--sensitive', value=value)
    else:
        vc.run('env', 'add', key, '--environment', environment, '--sensitive', '--yes', value=value)
    completed('environment variable', key, existed=bool(existing))


def check_binding(vc, resource):
    variables = json.loads(vc.run('env', 'ls', '--environment', 'production', '--json'))['envs']
    markers = [v for v in variables if v['key'] == BINDING_KEY and not v.get('gitBranch')]
    if len(markers) > 1:
        raise DatabaseError('The project is already bound to a different database resource.')
    marker_value = markers[0].get('value') if markers else None
    if markers and markers[0].get('type') == 'encrypted':
        marker_value = vc.production_config_value(BINDING_KEY)
    if markers and marker_value != resource['id']:
        raise DatabaseError('The project is already bound to a different database resource.')
    if not markers and any(v['key'] == 'AJSS_DATABASE_URL' for v in variables):
        raise DatabaseError('AJSS_DATABASE_URL already exists without this script\'s database binding. '
                            'It has not been overwritten; reconcile the existing connection first.')
    return bool(markers)


def provision(state, root, command, checkpoint):
    workspace = root / '.onboarding' / 'database-setup'
    workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    vc = Vercel(state, workspace, command)
    state['status'] = 'database_initializing'
    checkpoint()
    vc.link()
    # Source of truth for flags is the installed CLI, not a guessed API surface.
    vc.run('integration', 'resource', 'connect', '--help')
    vc.run('integration', 'resource', 'inspect', '--help')
    resource = select_resource(vc.resources())
    resource_existed = resource is not None
    if resource is None:
        vc.run('integration', 'guide', 'neon')
        plan_id = free_plan(vc)
        print('If prompted, accept the Neon Marketplace terms in the browser.', flush=True)
    creating('database resource', DATABASE_NAME)
    if resource is None:
        try:
            vc.run('integration', 'add', 'neon', '--name', DATABASE_NAME,
                   '--plan', plan_id, '--metadata', 'region=iad1',
                   '--no-connect', '--json', progress=True)
        except DatabaseError:
            # It may have succeeded remotely before a transport/skill-install failure.
            resource = select_resource(vc.resources())
            if resource is None:
                raise
            resource_existed = True
        resource = select_resource(vc.resources())
    if resource is None:
        raise DatabaseError('Neon provisioning is not visible yet; rerun to rediscover it.')
    completed('database resource', DATABASE_NAME, existed=resource_existed)
    if resource.get('claim_status') == 'sandbox':
        raise DatabaseError('The Neon resource is temporary. Claim it in Vercel before continuing.')
    details = json.loads(vc.run('integration', 'resource', 'inspect', DATABASE_NAME, '--json'))['resource']
    plan = details.get('billingPlan') or {}
    if plan.get('id') != 'free' and (plan.get('name') or '').lower() != 'free':
        raise DatabaseError('The Neon resource is not confirmed to be on the Free plan; no upgrade was requested.')
    bound = check_binding(vc, resource)
    # No owner connection is kept attached to the application project: reconnect
    # would reinject privileged URLs. Restricted env vars are the durable binding.
    owner_url = owner_connection(vc, resource)
    urls = configure_roles(owner_url, state['project_id'], isolation_exempt_owner='cloud_admin')
    app_url = urls['app']
    creating('environment variable', BINDING_KEY)
    if not bound:
        vc.run('env', 'add', BINDING_KEY, '--environment', 'production', '--no-sensitive', '--yes', value=resource['id'])
    completed('environment variable', BINDING_KEY, existed=bound)
    publish_url(vc, 'AJSS_DATABASE_URL', app_url, 'production')
    variables = json.loads(vc.run('env', 'ls', '--environment', 'production', '--json'))['envs']
    if not any(v['key'] == 'AJSS_DATABASE_URL' and
               (v.get('type') == 'sensitive' or v.get('visibility') == 'secret') for v in variables):
        raise DatabaseError('The production AJSS_DATABASE_URL Secret was not verified in Vercel.')
    path = workspace / 'project.env.local'
    existed = path.exists()
    creating('credential file', '.onboarding/database-setup/project.env.local')
    vc.run('env', 'pull', str(path), '--environment=production', '--yes')
    completed('credential file', '.onboarding/database-setup/project.env.local', existed=existed)
    return database_handoff(
        provider='vercel-neon', name=DATABASE_NAME, schema='public',
        app_url=app_url, admin_url=urls['admin'],
        vendor={'resource_id': resource['id'], 'project_id': state['project_id'],
                'team_id': state['team_id'], 'billing_plan': plan,
                'application_environment_variable': 'AJSS_DATABASE_URL'})


def verify(handoff):
    connections = handoff['common']['connections']
    # Neon's platform-owned postgres database has grants the tenant cannot revoke.
    verify_role(connections['app']['url'], connections['app']['role'],
                connections['admin']['role'], database=handoff['common']['database'],
                isolation_exempt_owner='cloud_admin')
