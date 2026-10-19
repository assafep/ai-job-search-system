"""Persist observed browser trial checkpoints through the data-layer boundary.

Separate from submission receipts: registration and external handoffs must
never promote an application to Applied. No credentials/cookies belong here.
"""

import argparse
import json
from datetime import datetime, timezone

from persistence_api.storage_layout import workload_state_path
from .persistence_client import PersistenceApiClient
from .runtime_config import load


SITES = {'jobify', 'rvc', 'echojobs'}
STAGES = {'needs_login', 'profile_review', 'needs_human_verification'}


def checkpoint_path(site, application_id):
    if site not in SITES:
        raise ValueError('unsupported trial site')
    if not application_id or '/' in application_id or application_id in {'.', '..'}:
        raise ValueError('invalid application ID')
    return workload_state_path('submit-prepared', 'site-trials/{}/{}.json'.format(site, application_id))


def record_checkpoint(persistence, site, application_id, stage, evidence):
    path = checkpoint_path(site, application_id)
    if stage not in STAGES or not evidence.strip():
        raise ValueError('an observed, non-submission checkpoint is required')
    application = persistence.get_application(application_id)
    if not application or application['status'] != 'Prepared':
        raise ValueError('trial checkpoint requires a Prepared application')
    result = {
        'site': site, 'application_id': application_id, 'stage': stage,
        'status': 'Prepared but not submitted', 'submission_confirmed': False,
        'evidence': evidence, 'observed_at': datetime.now(timezone.utc).isoformat(),
        'playbook': 'adapters/browser/playbooks/{}.md'.format(site),
    }
    persistence.store_file(path, json.dumps(result).encode('utf-8'), 'application/json')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', required=True, choices=sorted(SITES))
    parser.add_argument('--application-id', required=True)
    parser.add_argument('--stage', choices=sorted(STAGES))
    parser.add_argument('--evidence', help='Observed non-secret checkpoint evidence')
    args = parser.parse_args(argv)
    if bool(args.stage) != bool(args.evidence):
        parser.error('--stage and --evidence must be supplied together')
    load(require=True)
    persistence = PersistenceApiClient()
    if args.stage:
        result = record_checkpoint(persistence, args.site, args.application_id, args.stage, args.evidence)
    else:
        result = json.loads(persistence.read_file(checkpoint_path(args.site, args.application_id))['content'])
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
