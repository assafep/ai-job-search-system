"""Daily workflow: mail the applicant when the Gmail grant is near expiry.

The Application Service stores the grant and builds the renewal links.
This workflow sends the notice. It does not publish environment variables.
"""
import os

from application_service.gmail_grant import send_notice


def execute(client, opener=None):
    from .bulk_updates import item, require_success
    prepared = client.prepare_gmail_grant()
    if prepared.get('status') == 'missing' and not os.environ.get('VERCEL'):
        from provisioning.gmail_login import load_gmail_token
        local = load_gmail_token()
        if local and local.get('refresh_token'):
            grant = {
                'client_id': local['client_id'],
                'client_secret': local['client_secret'],
                'refresh_token': local['refresh_token'],
                'notice_email': local.get('notice_email'),
                'scope': local.get('scope'),
            }
            grant = {key: value for key, value in grant.items() if value is not None}
            require_success(client.bulk_update_items([item('install_gmail_grant', data=grant)]))
            prepared = client.prepare_gmail_grant()
    if prepared.get('status') != 'due':
        return {'workflow': 'renew-gmail-grant', 'status': prepared.get('status', 'current')}
    expiry = prepared.get('refresh_expires_at')
    if expiry is None:
        raise ValueError('Due Gmail notice is missing refresh_expires_at')
    message = prepared.get('message') or {}
    send_notice(prepared['access_token'], message, opener=opener)
    response = client.bulk_update_items([item('acknowledge_gmail_notice', data={'refresh_expires_at': expiry})])
    acknowledged = require_success(response)[0]['result']
    return {
        'workflow': 'renew-gmail-grant',
        'status': 'notice_sent',
        'connect_url': message.get('connect_url'),
        'renew_url': message.get('renew_url'),
        'refresh_expires_at': acknowledged.get('refresh_expires_at'),
    }
