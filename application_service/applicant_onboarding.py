"""Application Service storage boundary used before a live API is provisioned.

Owner credentials create My Drive files; the scoped service account reads and
updates them. Stage agents never receive either storage client.
"""
import json
from .google_drive_artifacts import FOLDER_MIME
from .profile import validate_profile


class ApplicantStorage:
    def __init__(self, owner_store, service_store):
        self.owner = owner_store
        self.service = service_store
        self.applicant_id = None

    def _children(self, store, parent):
        store._forget_metadata()  # User uploads must be visible on every check.
        return store._list_children(parent)

    def _unique(self, store, parent, name):
        matches = [f for f in self._children(store, parent) if f['name'] == name]
        if len(matches) > 1:
            raise ValueError('Multiple {} entries in Drive; resolve the duplicates and retry.'.format(name))
        return matches[0] if matches else None

    def prepare(self):
        folders = {}
        for name in ('Applications', 'Applicant'):
            folder = self._unique(self.owner, self.owner.root_folder_id, name)
            if folder and folder.get('mimeType') != FOLDER_MIME:
                raise ValueError(name + ' exists but is not a folder')
            folders[name] = folder or self.owner._ensure_folder(self.owner.root_folder_id, name)
        # Verify inherited service-account access, not just owner visibility.
        self._children(self.service, folders['Applicant']['id'])
        self.applicant_id = folders['Applicant']['id']
        return folders

    def resumes(self):
        return [f for f in self._children(self.service, self.applicant_id)
                if f.get('mimeType') != FOLDER_MIME and f['name'].lower().endswith('.pdf')]

    def read_resume(self, file):
        # Resolve by ID; a name supplied by the user never becomes a local path.
        return self.service.read(file['id'])

    def profile(self):
        file = self._unique(self.service, self.applicant_id, 'applicant_profile.json')
        if file is None:
            return None
        content = self.service.read(file['id'])
        if not content.strip():
            return None  # Retry a metadata-only file left by an interrupted create.
        return validate_profile(json.loads(content))

    def save_profile(self, profile):
        validate_profile(profile)
        payload = json.dumps(profile, indent=2, ensure_ascii=False).encode()
        file = self._unique(self.owner, self.applicant_id, 'applicant_profile.json')
        if file and file.get('mimeType') == FOLDER_MIME:
            raise ValueError('applicant_profile.json exists as a directory')
        # Empty metadata creation is recoverable: retries find this exact name.
        if file is None:
            file = self.owner._save_in_folder(self.applicant_id, 'applicant_profile.json', payload, 'application/json')
        self.service._replace_content(file['id'], payload, 'application/json')
        if json.loads(self.service.read(file['id'])) != profile:
            raise ValueError('Drive profile verification failed')
        return file
