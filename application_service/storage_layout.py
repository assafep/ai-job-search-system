"""Drive paths for private application data and stage-owned state."""

SYSTEM_DIRECTORY = '.ai-job-search-system'
APPLICANT_DIRECTORY = 'Applicant'
PROFILE_PATH = APPLICANT_DIRECTORY + '/applicant_profile.json'
LEGACY_PROFILE_PATH = SYSTEM_DIRECTORY + '/applicant_profile.json'
STAGE_EXECUTION_PATH = SYSTEM_DIRECTORY + '/stage_execution.json'
WORKLOAD_STATE_DIRECTORY = SYSTEM_DIRECTORY + '/workloads-internal-state'


def workload_state_path(stage, filename):
    if not stage or '/' in stage or stage in {'.', '..'}:
        raise ValueError('invalid stage name')
    from .file_paths import normalize_file_path
    return normalize_file_path('{}/{}/{}'.format(WORKLOAD_STATE_DIRECTORY, stage, filename))
