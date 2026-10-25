from application_service.completed_journal import company_role_key, parse_completed_markdown

SAMPLE = """
# Rejected

# (Rejected) Faye / Senior Backend Engineer

2026-10-13

* Posting URL: https://freehire.me/jobs/senior-backend-engineer-faye-5fqsirgq
* Operation idempotency key: workload-1

# Lemonade (Rejected)

2026-10-18

* Migrated to completed document

Senior Backend Engineer

08.09.2026
Applied using job-application-kit
"""


def test_same_company_role_is_one_key_across_boards():
    first = parse_completed_markdown(SAMPLE, 'doc-1')
    faye = first[0]
    assert faye['company'] == 'Faye'
    assert faye['role'] == 'Senior Backend Engineer'
    assert faye['urls'][0][0].startswith('https://freehire.me/')
    lemonade = first[1]
    assert lemonade['role'] == 'Senior Backend Engineer'
    assert company_role_key('Faye', 'Senior Backend Engineer') == company_role_key(
        'Faye', 'Senior Backend Engineer')
    other_board = company_role_key('Faye', 'Senior Backend Engineer')
    assert other_board == 'faye / senior backend engineer'


if __name__ == '__main__':
    test_same_company_role_is_one_key_across_boards()
    print('ok')
