"""Clean up future events from application event logs.

For each item in Prepared and Validated queues, remove events that reference
states beyond the current state. Uses delete_last_event API repeatedly,
checking after each deletion.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.application_service_client import ApplicationServiceClient


def get_token():
    cloud_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               '.onboarding', 'cloud.json')
    with open(cloud_path) as f:
        cloud = json.load(f)
    return cloud['runtime']['bearer_token']


# States ordered by progression
STATE_ORDER = [
    "Discovered", "Bookmarked", "Validated", "Prepared",
    "Manual Apply Required", "Applied", "Interviewing", "Offer",
    "Rejected", "Duplicate", "Dead Letters Queue", "Invalidated"
]


def has_future_transition(bullets, current_status):
    """Check if any bullet references a transition to a state beyond current."""
    if current_status not in STATE_ORDER:
        return False
    current_idx = STATE_ORDER.index(current_status)
    future_states = STATE_ORDER[current_idx + 1:]
    for bullet in bullets:
        bullet_str = str(bullet)
        for state in future_states:
            if f' --> {state}' in bullet_str:
                return True
    return False


def event_has_future_transition(event, current_status):
    """Check if an event contains any future transition."""
    bullets = event.get('data', {}).get('bullets', [])
    return has_future_transition(bullets, current_status)


def cleanup_events(client, app_id, current_status, dry_run=True):
    """Remove future events by deleting from the end and re-adding valid ones.
    
    Strategy:
    1. Get all events
    2. Find the rightmost future event index
    3. Save all events after the rightmost future event (these are valid tail events)
    4. Delete from the end until all future events are gone
    5. Re-add the saved valid tail events
    """
    events = client.list_events(app_id)
    if len(events) < 2:
        return 0
    
    # Find all future event indices
    future_indices = [i for i, e in enumerate(events) 
                      if event_has_future_transition(e, current_status)]
    if not future_indices:
        return 0
    
    rightmost_future = max(future_indices)
    
    # Save events after the rightmost future event
    tail_events = events[rightmost_future + 1:]
    
    if dry_run:
        print(f"    Rightmost future event: index {rightmost_future}")
        print(f"    Tail events to preserve: {len(tail_events)}")
        for i in range(rightmost_future, len(events)):
            bullets = events[i].get('data', {}).get('bullets', [])
            action = "DELETE" if i <= rightmost_future else "PRESERVE"
            print(f"    [{action}] Event {i}: {bullets[0][:50] if bullets else '(empty)'}")
        return len(future_indices)
    
    # Delete from the end until we've removed all future events
    # We need to delete: (len(events) - 1) down to rightmost_future
    # That means deleting (len(events) - rightmost_future - 1 + 1) = len(events) - rightmost_future events
    # But first save the tail events
    
    deleted = 0
    max_deletes = 20
    
    # Delete events from the end until only events 0..rightmost_future remain
    # Then delete the rightmost_future event too
    target_count = rightmost_future  # Keep events 0..rightmost_future-1
    
    while deleted < max_deletes:
        current_events = client.list_events(app_id)
        if len(current_events) <= target_count:
            break
        
        try:
            client.delete_last_event(app_id)
            deleted += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"    Error deleting: {e}")
            break
    
    # Re-add tail events
    for tail_event in tail_events:
        try:
            client.append_event(
                app_id,
                tail_event.get('event_type', 'journal_event'),
                data=tail_event.get('data', {})
            )
            time.sleep(0.5)
        except Exception as e:
            print(f"    Error re-adding event: {e}")
    
    return deleted


def main():
    token = get_token()
    client = ApplicationServiceClient(
        base_url=os.environ.get("APPLICATION_SERVICE_URL", "http://localhost:8080"),
        auth_token=token,
        timeout=60
    )

    # Items to check: Prepared + Validated
    queues = ["Prepared", "Validated"]
    all_items = []
    for queue in queues:
        apps = client.list_applications(status=queue)
        all_items.extend(apps)

    print(f"Checking {len(all_items)} items for future events...")
    
    items_with_future = []
    for app in all_items:
        app_id = app['application_id']
        status = app['status']
        events = client.list_events(app_id)
        
        future_count = sum(1 for e in events if event_has_future_transition(e, status))
        if future_count > 0:
            company = app.get('company', 'Unknown')
            role = app.get('role', 'Unknown')
            print(f"  {company} - {role} ({status}): {future_count} future events")
            items_with_future.append((app, future_count, events))
    
    print(f"\n{len(items_with_future)} items have future events")
    
    # First dry run
    print("\n" + "=" * 70)
    print("DRY RUN ANALYSIS")
    print("=" * 70)
    
    for app, future_count, events in items_with_future:
        app_id = app['application_id']
        company = app.get('company', 'Unknown')
        role = app.get('role', 'Unknown')
        status = app['status']
        
        print(f"\n{company} - {role} ({status})")
        print(f"  Total events: {len(events)}")
        
        for i, event in enumerate(events):
            is_future = event_has_future_transition(event, status)
            bullets = event.get('data', {}).get('bullets', [])
            marker = "FUTURE" if is_future else "OK"
            first_bullet = bullets[0][:50] if bullets else "(empty)"
            print(f"  [{marker}] Event {i}: {first_bullet}")
        
        cleanup_events(client, app_id, status, dry_run=True)
    
    # Actual cleanup
    print(f"\n{'=' * 70}")
    print("CLEANING UP")
    print("=" * 70)
    
    total_cleaned = 0
    for app, future_count, events in items_with_future:
        app_id = app['application_id']
        company = app.get('company', 'Unknown')
        role = app.get('role', 'Unknown')
        status = app['status']
        
        print(f"\n{company} - {role} ({status})")
        cleaned = cleanup_events(client, app_id, status, dry_run=False)
        print(f"  Deleted {cleaned} events")
        total_cleaned += cleaned
    
    print(f"\n{'=' * 70}")
    print(f"Total events cleaned: {total_cleaned}")


if __name__ == '__main__':
    main()
