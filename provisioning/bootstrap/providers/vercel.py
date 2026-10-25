#!/usr/bin/env python3
"""Provision a Vercel Hobby project and export a local deployment/runtime handoff."""
import getpass
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from ..contracts import SetupError, HostingResult
from .vercel_neon import cli_failure_detail
from ..handoffs import save
from ..progress import creating, completed

ROOT = Path(__file__).resolve().parents[1]
# Use only a real provider-issued affiliate URL, with the program's permission.
# Empty means no referral. Do not invent a ?ref= parameter.
AFFILIATE_URL = ''
API = 'https://api.vercel.com'
PROJECT_PREFIX = 'ajss'
DEFAULT_PROJECT_NAME = PROJECT_PREFIX


def cli(auth_dir, *args):
    executable = shutil.which('vercel')
    if executable:
        command = [executable]
    elif shutil.which('npx'):
        command = ['npx', '--yes', 'vercel@latest']
    else:
        raise SetupError('Browser login needs Node.js/npm (or an installed Vercel CLI). '
                         'Install Node.js LTS, or use --auth token.')
    return command + list(args) + ['--global-config', str(auth_dir)]


def signup():
    url = AFFILIATE_URL or 'https://vercel.com/signup'
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise SetupError('AFFILIATE_URL must use HTTPS.')
    if AFFILIATE_URL:
        print('Affiliate signup link: the maintainer may receive a benefit.', file=sys.stderr)
    print('Create or sign into your Vercel account: ' + url, file=sys.stderr, flush=True)
    print('Choose Continue with Google or GitHub on the SIGNUP page. Complete account setup '
          'on the free Hobby plan. If you already have an account, sign in instead.',
          file=sys.stderr, flush=True)
    try:
        webbrowser.open(url)
    except (webbrowser.Error, OSError):
        print('Open the signup URL above manually in your browser.', file=sys.stderr)
    print('After account setup finishes and you reach the Vercel dashboard, '
          'press Enter here to authorize the CLI (Ctrl+C to stop): ',
          file=sys.stderr, end='', flush=True)
    try:
        input()
    except EOFError:
        raise SetupError('Signup requires browser interaction. Complete https://vercel.com/signup, '
                         'then rerun with --existing-account in an interactive terminal.') from None


def credentials(state, auth_dir, login=False, existing_account=False):
    if state['authentication']['method'] == 'token':
        return state['authentication']['access_token']
    auth_file = auth_dir / 'auth.json'
    if login or not auth_file.exists():
        # CLI device authorization logs in existing accounts; it does not register them.
        # Check prerequisites before opening signup and asking the user to finish it.
        command = cli(auth_dir, 'login')
        if not (existing_account or auth_file.exists() or
                state['authentication'].get('access_token') or state.get('team_id')):
            signup()
        print('Authorize the CLI in the browser using the account you just created or signed into.',
              file=sys.stderr, flush=True)
        print('If you see account_not_found, finish https://vercel.com/signup first, then '
              'return to the CLI verification URL. Google login alone does not create the account.',
              file=sys.stderr, flush=True)
        try:
            subprocess.run(command, check=True, stdout=sys.stderr)
        except subprocess.CalledProcessError:
            raise SetupError('CLI authorization did not complete. If the browser reported '
                             'account_not_found, complete https://vercel.com/signup and rerun '
                             './onboard.sh --existing-account. Saved progress is retained.') from None
    # Let the official CLI refresh its own expiring OAuth credentials.
    try:
        subprocess.run(cli(auth_dir, 'whoami'), check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as exc:
        raise SetupError('Cannot verify the saved Vercel login: ' + cli_failure_detail(exc.stderr or '')) from None
    auth = json.loads(auth_file.read_text())
    state['authentication']['access_token'] = auth['token']
    state['authentication']['expires_at'] = auth.get('expiresAt')
    state['authentication']['cli_auth_file'] = str(auth_file)
    return auth['token']


class Client:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, body=None, team=None):
        if team:
            path += ('&' if '?' in path else '?') + urllib.parse.urlencode({'teamId': team})
        request = urllib.request.Request(API + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + self.token,
                     'Content-Type': 'application/json', 'User-Agent': 'auto-signup-vercel/1'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Don't echo response bodies: provider errors can include submitted secrets.
            exc.close()
            raise SetupError(f'Vercel returned HTTP {exc.code} for {method} '
                             f'{path.split("?")[0]}. Check account permissions/quotas in the dashboard. '
                             'Saved progress is retained; rerun to resume.') from None


def hobby_team(client):
    teams = []
    path = '/v2/teams?limit=100'
    while path:
        page = client.request('GET', path)
        teams.extend(t for t in page['teams'] if t.get('billing', {}).get('plan') == 'hobby')
        cursor = page.get('pagination', {}).get('next')
        path = '/v2/teams?' + urllib.parse.urlencode({'limit': 100, 'until': cursor}) if cursor else None
    if len(teams) != 1:
        raise SetupError('Could not select exactly one Hobby team. Finish free Hobby account setup '
                         'in the dashboard, or pass --team TEAM_ID to select a Hobby team.')
    return teams[0]


def matching_project(client, team_id, saved_name, requested_project=None):
    if requested_project:
        project = client.request('GET', '/v9/projects/' + urllib.parse.quote(requested_project, safe=''), team=team_id)
        if not project['name'].startswith(PROJECT_PREFIX):
            raise SetupError('--project must select a project starting with ' + PROJECT_PREFIX)
        return project
    matches = []
    path = '/v10/projects?' + urllib.parse.urlencode({'search': PROJECT_PREFIX, 'limit': 100})
    while path:
        page = client.request('GET', path, team=team_id)
        matches.extend(p for p in page['projects'] if p['name'].startswith(PROJECT_PREFIX))
        cursor = page.get('pagination', {}).get('next')
        path = '/v10/projects?' + urllib.parse.urlencode(
            {'search': PROJECT_PREFIX, 'limit': 100, 'until': cursor}) if cursor else None
    exact = next((p for p in matches if p['name'] == saved_name), None)
    if exact:
        return exact
    if len(matches) > 1:
        names = ', '.join(sorted(p['name'] for p in matches))
        raise SetupError('Multiple projects match the prefix: ' + names +
                         '. Rerun with --project NAME_OR_ID to select one; no project was created.')
    return matches[0] if matches else None


def recover_project(state, project, client, checkpoint):
    """Rebuild local credentials and deployment references without rotating secrets."""
    team_id, project_id = state['team_id'], state['project_id']
    production = project.get('targets', {}).get('production') or {}
    if production.get('id'):
        state['deployment_id'] = production['id']
        state['verify_bootstrap'] = False
    elif not state.get('deployment_id'):
        page = client.request('GET', '/v6/deployments?' + urllib.parse.urlencode(
            {'projectId': project_id, 'target': 'production', 'limit': 1}), team=team_id)
        deployments = page['deployments']
        if deployments:
            state['deployment_id'] = deployments[0]['uid']
            state['verify_bootstrap'] = False
    environment = client.request('GET', '/v10/projects/' + project_id + '/env', team=team_id)
    creating('environment variable', 'AJSS_API_TOKEN')
    def production_variables(key):
        return [v for v in environment['envs'] if v['key'] == key
                and 'production' in v.get('target', []) and not v.get('gitBranch')
                and not v.get('customEnvironmentIds')]

    variables = production_variables('AJSS_API_TOKEN')
    legacy = False
    if not variables:
        # Preserve the token used by existing deployments while migrating the
        # onboarding contract. Never rename or delete the old deployment's key.
        for legacy_key in ('AJSS_APPLICATION_SERVICE_TOKEN', 'APPLICATION_SERVICE_TOKEN'):
            variables = production_variables(legacy_key)
            if variables:
                legacy = True
                break
    if len(variables) > 1:
        raise SetupError('Multiple production AJSS_API_TOKEN variables exist; resolve them in Vercel.')
    if variables:
        variable = client.request('GET', '/v1/projects/' + project_id + '/env/' +
                                  urllib.parse.quote(variables[0]['id'], safe=''), team=team_id)
        value = variable.get('value')
        if not isinstance(value, str) or not value or variable.get('decrypted') is not True:
            raise SetupError('The existing runtime token cannot be decrypted. Grant environment-variable '
                             'read access or recover the token separately; it has not been replaced.')
        state['runtime']['bearer_token'] = value
        if legacy:
            client.request('POST', '/v10/projects/' + project_id + '/env', {
                'key': 'AJSS_API_TOKEN', 'value': value,
                'type': 'encrypted', 'target': ['production']}, team=team_id)
        completed('environment variable', 'AJSS_API_TOKEN', existed=True)
    elif state.get('deployment_id'):
        # A deployed application may use a different authentication contract.
        raise SetupError('The existing project has a production deployment but no recoverable '
                         'AJSS_API_TOKEN. Its deployment and credentials have not been changed.')
    else:
        client.request('POST', '/v10/projects/' + project_id + '/env', {
            'key': 'AJSS_API_TOKEN', 'value': state['runtime']['bearer_token'],
            'type': 'encrypted', 'target': ['production']}, team=team_id)
        completed('environment variable', 'AJSS_API_TOKEN')
    state['runtime']['token_environment_variable'] = 'AJSS_API_TOKEN'
    # Refresh cloud readiness below, even if an old local handoff said ready.
    state['status'] = 'initializing'
    checkpoint()


def provision(state, client, checkpoint, requested_team=None, requested_project=None):
    if not state.get('team_id'):
        team = client.request('GET', '/v2/teams/' + urllib.parse.quote(requested_team, safe='')) if requested_team else hobby_team(client)
        if team.get('billing', {}).get('plan') != 'hobby':
            raise SetupError('Only Hobby teams are supported. No paid plan will be provisioned.')
        state['team_id'] = team['id']
        checkpoint()
    team_id = state['team_id']
    # Revalidate on every run; never silently continue if the team changed plans.
    team = client.request('GET', '/v2/teams/' + urllib.parse.quote(team_id, safe=''))
    if team.get('billing', {}).get('plan') != 'hobby':
        raise SetupError('The saved team is no longer on Hobby; stopped before provisioning.')
    if not state.get('project_id'):
        existing = matching_project(client, team_id, state['project_name'], requested_project)
        creating('project', existing['name'] if existing else state['project_name'])
        project = existing or client.request('POST', '/v11/projects', {
            'name': state['project_name'], 'framework': None,
            'environmentVariables': [{'key': 'AJSS_API_TOKEN',
                'value': state['runtime']['bearer_token'], 'type': 'encrypted',
                'target': ['production']}]}, team=team_id)
        state['project_id'] = project['id']
        state['project_name'] = project['name']
        checkpoint()
        completed('project', state['project_name'], existed=bool(existing))
        if existing:
            project = client.request('GET', '/v9/projects/' + state['project_id'], team=team_id)
            recover_project(state, project, client, checkpoint)
        else:
            creating('environment variable', 'AJSS_API_TOKEN')
            completed('environment variable', 'AJSS_API_TOKEN')
    else:
        project = client.request('GET', '/v9/projects/' + state['project_id'], team=team_id)
        if requested_project and requested_project not in (project['id'], project['name']):
            raise SetupError('--project differs from the saved project; refusing to retarget it.')
        creating('project', project['name'])
        completed('project', project['name'], existed=True)
        recover_project(state, project, client, checkpoint)
    creating('production deployment', state['project_name'])
    deployment_existed = bool(state.get('deployment_id'))
    if not state.get('deployment_id'):
        # Only the generated bootstrap project is affected. Application auth stays on.
        client.request('PATCH', '/v9/projects/' + state['project_id'], {'ssoProtection': None}, team=team_id)
        files = [{'file': str(p.relative_to(ROOT / 'starter')), 'data': p.read_text(), 'encoding': 'utf-8'}
                 for p in sorted((ROOT / 'starter').rglob('*')) if p.is_file()]
        deployment = client.request('POST', '/v13/deployments', {
            'name': state['project_name'], 'project': state['project_id'],
            'target': 'production', 'files': files,
            'projectSettings': {'framework': None}}, team=team_id)
        state['deployment_id'] = deployment['id']
        state['verify_bootstrap'] = True
        checkpoint()
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        deployment = client.request('GET', '/v13/deployments/' + state['deployment_id'], team=team_id)
        status = deployment.get('readyState') or deployment.get('status')
        if status in ('ERROR', 'CANCELED'):
            raise SetupError('Starter deployment failed. Inspect its build logs in Vercel. '
                             'Saved deployment ID is retained for diagnosis.')
        if status == 'READY':
            completed('production deployment', state['project_name'], existed=deployment_existed)
            # Query the assigned production domain rather than guessing it.
            domains = client.request('GET', '/v9/projects/' + state['project_id'] + '/domains', team=team_id)
            candidates = [d['name'] for d in domains['domains']
                          if not d.get('gitBranch') and d['name'].endswith('.vercel.app')]
            if not candidates:
                raise SetupError('No production vercel.app domain is assigned yet; rerun shortly.')
            state['runtime']['base_url'] = 'https://' + candidates[0]
            state['deployment_url'] = 'https://' + deployment['url']
            checkpoint()
            if state.get('verify_bootstrap'):
                verify_runtime(state['runtime'])
                state['runtime']['verification'] = 'bootstrap_verified'
            else:
                state['runtime']['verification'] = 'recovered_existing_deployment_not_probed'
            state['status'] = 'ready'
            checkpoint()
            return
        time.sleep(3)
    raise SetupError('Deployment is still pending after 10 minutes. Rerun to resume waiting.')


def verify_runtime(runtime):
    request = urllib.request.Request(runtime['base_url'] + '/api',
        headers={'Authorization': 'Bearer ' + runtime['bearer_token']})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if json.load(response).get('status') != 'bootstrap_ready':
                raise SetupError('The public API did not return the expected bootstrap response.')
    except urllib.error.HTTPError as exc:
        exc.close()
        raise SetupError(f'Authenticated public API verification returned HTTP {exc.code}; '
                         'check the deployment and application token.') from None
    try:
        urllib.request.urlopen(runtime['base_url'] + '/api', timeout=45).close()
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code == 401:
            return
        raise SetupError(f'Public API verification returned HTTP {exc.code}; rerun after checking deployment.') from None
    raise SetupError('The starter API unexpectedly allowed an unauthenticated request.')


def provision_hosting(args):
    if not args.credentials:
        # Token mode also uses the CLI for Marketplace provisioning and Secret env vars.
        cli(ROOT / '.onboarding' / 'vercel-auth', '--version')
    os.umask(0o077)
    directory = ROOT / '.onboarding'
    if directory.is_symlink():
        raise SetupError('.onboarding must not be a symbolic link.')
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    path = directory / 'cloud.json'
    handoff_existed = path.exists()
    if not args.credentials:
        creating('credential file', '.onboarding/cloud.json')
    auth_dir = directory / 'vercel-auth'
    if path.exists():
        state = json.loads(path.read_text())
        if state.get('provider') != 'vercel':
            raise SetupError('The saved hosting provider is not Vercel; refusing to retarget it.')
        if args.auth and args.auth != state['authentication']['method']:
            raise SetupError('Authentication mode is already saved. Reuse it to resume this setup.')
        if args.team and state.get('team_id') and args.team != state['team_id']:
            raise SetupError('--team differs from the saved team; refusing to retarget this project.')
    elif args.credentials:
        raise SetupError('Run onboarding first.')
    else:
        method = args.auth
        if not method:
            choice = input('Authentication: [1] browser signup then CLI login (default), [2] existing Vercel token in shell: ').strip()
            if choice not in ('', '1', '2'):
                raise SetupError('Select 1 or 2, or use --auth browser|token.')
            method = 'token' if choice == '2' else 'browser'
        state = {'schema_version': 1, 'provider': 'vercel', 'status': 'initializing',
                 'application_ready': False, 'project_name': DEFAULT_PROJECT_NAME,
                 'authentication': {'method': method},
                 'runtime': {'base_url': None, 'bearer_token': secrets.token_urlsafe(32),
                             'token_environment_variable': 'AJSS_API_TOKEN'},
                 'management_api_url': API,
                 'credentials_command': [str(ROOT / 'onboard.sh'), '--credentials']}
        if method == 'token':
            token = os.environ.get('VERCEL_TOKEN') or getpass.getpass('Vercel API token (hidden; never enter your IDP password): ')
            if not token.strip():
                raise SetupError('An API token is required.')
            state['authentication']['access_token'] = token.strip()
        save(path, state)
    if not args.credentials:
        completed('credential file', '.onboarding/cloud.json', existed=handoff_existed)
    token = credentials(state, auth_dir, existing_account=args.existing_account)
    save(path, state)
    if args.credentials:
        # This mode is explicitly machine-readable; stdout contains secrets.
        print(json.dumps({'VERCEL_TOKEN': token, 'VERCEL_ORG_ID': state.get('team_id'),
                          'VERCEL_PROJECT_ID': state.get('project_id'),
                          'expires_at': state['authentication'].get('expires_at')}))
        return
    provision(state, Client(token), lambda: save(path, state), args.team, args.project)
    handoff = {
        'schema_version': 1, 'provider': 'vercel',
        'common': {'base_url': state['runtime']['base_url'],
                   'bearer_token': state['runtime']['bearer_token']},
        'vendor': {'project_id': state['project_id'], 'team_id': state['team_id'],
                   'deployment_url': state['deployment_url'],
                   'management_api_url': state['management_api_url'],
                   'authentication': state['authentication']},
    }
    return HostingResult(handoff, state, lambda *args: cli(auth_dir, *args),
                         lambda: save(path, state))
