"""Consistent, secret-free resource lifecycle messages."""


def creating(entity_type, name):
    print(f'Creating {entity_type} with name {name}...', flush=True)


def completed(entity_type, name, existed=False):
    action = 'Already exists' if existed else 'Created'
    print(f'{action} {entity_type} {name}', flush=True)
