"""Audit Prepared and Manual Apply Required queues for missing submission packages.

This script:
1. Lists all items in "Prepared" and "Manual Apply Required" queues.
2. For each item, checks the event log for a bullet containing "Application package:"
   (the marker that the prepare-validated workload produced a submission package).
3. For items missing such an event, checks if a submission package PDF already exists
   in Google Drive (via list_artifacts).
4. For items where a package exists, injects an "application_package_prepared" event
   referencing the package.
5. For items where no package exists, moves them back to Validated via Dead Letters Queue
   (since Prepared -> Validated is not a direct transition).
6. For all items reviewed, cleans up the event log by removing events that reference
   states beyond the current state (e.g., Applied -> Dead Letters Queue when current
   state is Prepared).
"""

import json
import os
import sys
import hashlib
import time
from datetime import date

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.application_service_client import ApplicationServiceClient


def get_token():
    """Load the bearer token from the cloud config."""
    cloud_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               '.onboarding', 'cloud.json')
    with open(cloud_path) as f:
        cloud = json.load(f)
    return cloud['runtime']['bearer_token']


def has_package_link_in_events(events):
    """Check if any event bullet contains an Application package link.

    Events are all 'journal_event' type. The package link appears in a bullet
    like 'Application package: https://drive.google.com/file/d/.../view'.
    """
    for event in events:
        bullets = event.get('data', {}).get('bullets', [])
        for bullet in bullets:
            if 'Application package:' in str(bullet) or 'Application PDF:' in str(bullet):
                return True, event
    return False, None


def find_package_in_artifacts(artifacts):
    """Check if any artifact is a PDF submission package."""
    for artifact in artifacts:
        name = artifact.get('name', '').lower()
        mime = artifact.get('mime_type', '')
        if mime == 'application/pdf' and ('cover' in name or 'application' in name or
                                           'package' in name or 'resume' in name):
            return artifact
    return None


def count_future_events(events, current_status):
    """Count events that reference states beyond the current state.

    For Prepared items, future states are: Applied, Interviewing, Offer,
    Rejected, Dead Letters Queue.
    """
    state_order = [
        "Discovered", "Bookmarked", "Validated", "Prepared",
        "Manual Apply Required", "Applied", "Interviewing", "Offer",
        "Rejected", "Duplicate", "Dead Letters Queue", "Invalidated"
    ]

    if current_status not in state_order:
        return 0

    current_idx = state_order.index(current_status)
    future_count = 0

    for event in events:
        bullets = event.get('data', {}).get('bullets', [])
        for bullet in bullets:
            bullet_str = str(bullet)
            for state in state_order[current_idx + 1:]:
                if f' --> {state}' in bullet_str:
                    future_count += 1
                    break

    return future_count


def delete_future_events(client, app_id, events, current_status):
    """Delete events from the end until no future events remain.

    The API only supports delete_last_event, so we repeatedly delete from the end
    until all future events are removed.
    """
    state_order = [
        "Discovered", "Bookmarked", "Validated", "Prepared",
        "Manual Apply Required", "Applied", "Interviewing", "Offer",
        "Rejected", "Duplicate", "Dead Letters Queue", "Invalidated"
    ]

    if current_status not in state_order:
        return 0

    current_idx = state_order.index(current_status)
    deleted = 0

    while True:
        events = client.list_events(app_id)
        if not events:
            break

        # Check if the last event has future transitions
        last_event = events[-1]
        bullets = last_event.get('data', {}).get('bullets', [])
        has_future = False
        for bullet in bullets:
            bullet_str = str(bullet)
            for state in state_order[current_idx + 1:]:
                if f' --> {state}' in bullet_str:
                    has_future = True
                    break
            if has_future:
                break

        if not has_future:
            break

        try:
            client.delete_last_event(app_id)
            deleted += 1
            time.sleep(0.5)  # Rate limiting
        except Exception as e:
            print(f"    Error deleting event: {e}")
            break

    return deleted


def main():
    token = get_token()
    client = ApplicationServiceClient(
        base_url=os.environ.get("APPLICATION_SERVICE_URL", "http://localhost:8080"),
        auth_token=token,
        timeout=60
    )

    queues = ["Prepared", "Manual Apply Required"]
    all_items = []

    print("=" * 70)
    print("AUDITING QUEUES: Prepared, Manual Apply Required")
    print("=" * 70)

    # Step 1: Collect all items from both queues
    for queue in queues:
        print(f"\n--- Fetching {queue} queue ---")
        apps = client.list_applications(status=queue)
        print(f"  Found {len(apps)} items in {queue}")
        all_items.extend(apps)

    print(f"\n{'=' * 70}")
    print(f"TOTAL ITEMS TO AUDIT: {len(all_items)}")
    print(f"{'=' * 70}")

    # Step 2: Audit each item
    results = {
        'has_package_event': [],
        'missing_event_but_package_exists': [],
        'missing_event_and_no_package': [],
        'error': [],
    }

    for idx, app in enumerate(all_items, 1):
        app_id = app['application_id']
        company = app.get('company', 'Unknown')
        role = app.get('role', 'Unknown')
        status = app['status']

        print(f"\n[{idx}/{len(all_items)}] {company} - {role} ({status})")
        print(f"  ID: {app_id}")

        try:
            events = client.list_events(app_id)
            print(f"  Events: {len(events)}")

            has_event, _ = has_package_link_in_events(events)
            if has_event:
                print(f"  ✓ Has package link in events")
                results['has_package_event'].append(app)
                continue

            print(f"  ✗ No package link in events")

            artifacts = client.list_artifacts(app_id)
            pdfs = [a for a in artifacts if a.get('mime_type') == 'application/pdf']
            print(f"  PDF artifacts: {len(pdfs)}")
            for art in pdfs:
                print(f"    - {art.get('name', 'N/A')}")

            package = find_package_in_artifacts(artifacts)
            if package:
                print(f"  ✓ Found existing package: {package.get('name')}")
                results['missing_event_but_package_exists'].append({
                    'app': app,
                    'package': package,
                    'events': events,
                })
            else:
                print(f"  ✗ No submission package found in Drive")
                results['missing_event_and_no_package'].append(app)

        except Exception as e:
            print(f"  ERROR: {e}")
            results['error'].append({'app': app, 'error': str(e)})

    # Step 3: Report findings
    print(f"\n{'=' * 70}")
    print("AUDIT RESULTS SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Already have package event: {len(results['has_package_event'])}")
    print(f"  Missing event, package exists: {len(results['missing_event_but_package_exists'])}")
    print(f"  Missing event, no package: {len(results['missing_event_and_no_package'])}")
    print(f"  Errors: {len(results['error'])}")

    # Step 4: Inject events for items with existing packages
    print(f"\n{'=' * 70}")
    print("STEP 4: INJECTING MISSING EVENTS")
    print(f"{'=' * 70}")

    injected = 0
    for item in results['missing_event_but_package_exists']:
        app = item['app']
        package = item['package']
        app_id = app['application_id']
        company = app.get('company', 'Unknown')
        role = app.get('role', 'Unknown')

        print(f"\n  {company} - {role}")

        drive_file_id = package.get('drive_file_id') or ''
        artifact_ref = f"https://drive.google.com/file/d/{drive_file_id}/view" if drive_file_id else ''

        event_data = {
            'source': 'application agent',
            'bullets': [
                'Application package created successfully.',
                f'Application package: {artifact_ref}' if artifact_ref else f'Application PDF: {package.get("name", "")}'
            ]
        }
        if artifact_ref:
            event_data['artifact_reference'] = artifact_ref

        idempotency_key = 'audit-inject-' + hashlib.sha256(
            (app_id + '-package-prepared').encode('utf-8')
        ).hexdigest()[:24]

        try:
            result = client.append_event(
                app_id,
                'application_package_prepared',
                data=event_data,
                idempotency_key=idempotency_key
            )
            print(f"    ✓ Event injected: {result.get('event_id', 'N/A')}")
            injected += 1
        except Exception as e:
            print(f"    ✗ Failed: {e}")

    print(f"\n  Total events injected: {injected}")

    # Step 5: Move items without packages back to Validated
    # Path: Prepared -> Dead Letters Queue -> Validated
    print(f"\n{'=' * 70}")
    print("STEP 5: MOVING ITEMS WITHOUT PACKAGES TO VALIDATED")
    print(f"{'=' * 70}")

    moved = 0
    for app in results['missing_event_and_no_package']:
        app_id = app['application_id']
        company = app.get('company', 'Unknown')
        role = app.get('role', 'Unknown')
        status = app['status']

        print(f"\n  {company} - {role} ({status})")

        if status == 'Validated':
            print(f"    Already in Validated, skipping")
            continue

        # Prepared -> Dead Letters Queue -> Validated
        try:
            print(f"    Moving to Dead Letters Queue...")
            client.transition(
                app_id,
                'Dead Letters Queue',
                data={'source': 'audit - no submission package found, returning to Validated'}
            )
            time.sleep(0.5)
            print(f"    Moving from Dead Letters Queue to Validated...")
            client.transition(
                app_id,
                'Validated',
                data={'source': 'audit - returned from DLQ'}
            )
            time.sleep(0.5)
            print(f"    ✓ Moved to Validated")
            moved += 1
        except Exception as e:
            print(f"    ✗ Failed: {e}")

    print(f"\n  Total items moved to Validated: {moved}")

    # Step 6: Clean up future events for all reviewed items
    print(f"\n{'=' * 70}")
    print("STEP 6: CLEANING UP FUTURE EVENTS")
    print(f"{'=' * 70}")

    total_cleaned = 0
    for app in all_items:
        app_id = app['application_id']
        company = app.get('company', 'Unknown')
        role = app.get('role', 'Unknown')
        status = app['status']

        try:
            events = client.list_events(app_id)
            future_count = count_future_events(events, status)
            if future_count > 0:
                print(f"\n  {company} - {role}: {future_count} future events to clean")
                cleaned = delete_future_events(client, app_id, events, status)
                print(f"    Deleted {cleaned} events from end")
                total_cleaned += cleaned
        except Exception as e:
            print(f"  {company} - {role}: Error: {e}")

    print(f"\n  Total future events cleaned: {total_cleaned}")

    # Final summary
    print(f"\n{'=' * 70}")
    print("FINAL SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Items audited: {len(all_items)}")
    print(f"  Already had package event: {len(results['has_package_event'])}")
    print(f"  Events injected: {injected}")
    print(f"  Items moved to Validated: {moved}")
    print(f"  Future events cleaned: {total_cleaned}")
    print(f"  Errors: {len(results['error'])}")


if __name__ == '__main__':
    main()
