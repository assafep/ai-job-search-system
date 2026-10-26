from services.workload_runner import main


def test_discovery_fixture_is_accepted(tmp_path, capsys):
    fixture = tmp_path / "fixture.json"
    fixture.write_text('{"job_postings": [{"company": "Acme", "role": "Backend", "posting_url": "https://example.test/job"}]}')

    assert main([
        "discover",
        "--fixture", str(fixture),
    ]) == 0
    assert "stage" in capsys.readouterr().out
