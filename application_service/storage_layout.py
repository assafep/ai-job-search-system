"""Drive paths for private application data and workflow-owned state."""

SYSTEM_DIRECTORY = '.ai-job-search-system'
APPLICANT_DIRECTORY = 'Applicant'
PROFILE_PATH = APPLICANT_DIRECTORY + '/applicant_profile.json'
LEGACY_PROFILE_PATH = SYSTEM_DIRECTORY + '/applicant_profile.json'
WORKLOAD_STATE_DIRECTORY = SYSTEM_DIRECTORY + '/workloads-internal-state'


def workload_state_path(workflow, filename):
    if not workflow or '/' in workflow or workflow in {'.', '..'}:
        raise ValueError('invalid workflow name')
    from .file_paths import normalize_file_path
    return normalize_file_path('{}/{}/{}'.format(WORKLOAD_STATE_DIRECTORY, workflow, filename))
