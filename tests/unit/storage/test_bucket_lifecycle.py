"""
tests/unit/storage/test_bucket_lifecycle.py

``scripts/data/apply_bucket_lifecycle.py`` against moto's in-memory S3: the rules it writes, that rules it does not own are
kept, that a repeat run changes nothing, and what it reports when a bucket stores something other than it was given.
"""

from __future__ import annotations

import boto3
import pytest
from helpers.scripts import load_script
from helpers.storage.s3 import BUCKET, REGION

script = load_script("data", "apply_bucket_lifecycle")


@pytest.fixture
def client(moto_s3):
    c = boto3.client("s3", region_name=REGION)
    yield c
    try:
        c.delete_bucket_lifecycle(Bucket=BUCKET)
    except Exception:  # noqa: BLE001 - nothing was stored
        pass


def stored(client) -> dict[str, dict]:
    return {r["ID"]: r for r in client.get_bucket_lifecycle_configuration(Bucket=BUCKET)["Rules"]}


class TestRules:
    def test_raw_and_annotated_expire_and_nothing_else_does(self):
        rules = script.lifecycle_rules("", 14, 90)
        assert [(r["Filter"]["Prefix"], r["Expiration"]["Days"]) for r in rules] == [("raw/", 14), ("annotated/", 90)]
        assert all(r["Status"] == "Enabled" for r in rules)

    def test_the_key_prefix_leads_both_prefixes(self):
        rules = script.lifecycle_rules("/khadbot/prod/", 14, 90)
        assert [r["Filter"]["Prefix"] for r in rules] == ["khadbot/prod/raw/", "khadbot/prod/annotated/"]

    @pytest.mark.parametrize("days", [0, -1])
    def test_a_expiry_under_one_day_is_refused(self, days):
        with pytest.raises(ValueError, match="at least 1"):
            script.lifecycle_rules("", days, 90)


class TestApply:
    def test_writes_both_rules_and_reads_them_back(self, client):
        ours = script.lifecycle_rules("", 14, 90)
        assert script.apply_rules(client, BUCKET, ours) == []
        rules = stored(client)
        assert rules[script.RAW_RULE_ID]["Expiration"]["Days"] == 14
        assert rules[script.RAW_RULE_ID]["Filter"]["Prefix"] == "raw/"
        assert rules[script.ANNOTATED_RULE_ID]["Expiration"]["Days"] == 90
        assert rules[script.ANNOTATED_RULE_ID]["Filter"]["Prefix"] == "annotated/"

    def test_events_and_records_are_not_covered_by_any_rule(self, client):
        script.apply_rules(client, BUCKET, script.lifecycle_rules("", 14, 90))
        prefixes = [r["Filter"]["Prefix"] for r in stored(client).values()]
        assert not any("events/".startswith(p) or "records/".startswith(p) for p in prefixes if p)

    def test_a_repeat_run_leaves_the_same_two_rules(self, client):
        ours = script.lifecycle_rules("", 14, 90)
        script.apply_rules(client, BUCKET, ours)
        script.apply_rules(client, BUCKET, ours)
        assert sorted(stored(client)) == sorted([script.RAW_RULE_ID, script.ANNOTATED_RULE_ID])

    def test_changed_days_replace_the_old_rule(self, client):
        script.apply_rules(client, BUCKET, script.lifecycle_rules("", 14, 90))
        script.apply_rules(client, BUCKET, script.lifecycle_rules("", 7, 60))
        rules = stored(client)
        assert (
            rules[script.RAW_RULE_ID]["Expiration"]["Days"],
            rules[script.ANNOTATED_RULE_ID]["Expiration"]["Days"],
        ) == (7, 60)

    def test_other_rules_in_the_bucket_are_kept(self, client):
        client.put_bucket_lifecycle_configuration(
            Bucket=BUCKET,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "someone-elses",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "tmp/"},
                        "Expiration": {"Days": 1},
                    }
                ]
            },
        )
        script.apply_rules(client, BUCKET, script.lifecycle_rules("", 14, 90))
        assert set(stored(client)) == {"someone-elses", script.RAW_RULE_ID, script.ANNOTATED_RULE_ID}

    def test_a_dry_run_writes_nothing(self, client):
        assert script.apply_rules(client, BUCKET, script.lifecycle_rules("", 14, 90), dry_run=True) == []
        with pytest.raises(client.exceptions.ClientError):
            client.get_bucket_lifecycle_configuration(Bucket=BUCKET)


class TestProblems:
    def test_a_missing_disabled_or_different_rule_is_reported(self):
        ours = script.lifecycle_rules("", 14, 90)
        stored_rules = [
            {"ID": script.RAW_RULE_ID, "Status": "Disabled", "Filter": {"Prefix": "raw/"}, "Expiration": {"Days": 14}},
        ]
        found = script.problems(stored_rules, ours)
        assert any("raw" in p and "Disabled" in p for p in found)
        assert any("annotated" in p and "not stored" in p for p in found)

    def test_a_wrong_prefix_or_days_is_reported(self):
        ours = script.lifecycle_rules("", 14, 90)
        stored_rules = [
            {"ID": script.RAW_RULE_ID, "Status": "Enabled", "Filter": {"Prefix": "other/"}, "Expiration": {"Days": 14}},
            {
                "ID": script.ANNOTATED_RULE_ID,
                "Status": "Enabled",
                "Filter": {"Prefix": "annotated/"},
                "Expiration": {"Days": 1},
            },
        ]
        found = script.problems(stored_rules, ours)
        assert len(found) == 2

    def test_matching_rules_report_nothing(self):
        ours = script.lifecycle_rules("", 14, 90)
        assert script.problems(ours, ours) == []


class TestMain:
    def test_refuses_without_a_bucket(self, monkeypatch, capsys):
        monkeypatch.setenv("KHADBOT_STORAGE", "local")
        assert script.main([]) == 2
        assert "KHADBOT_STORAGE=s3" in capsys.readouterr().err

    def test_applies_to_the_configured_bucket(self, client, monkeypatch, capsys):
        monkeypatch.setenv("KHADBOT_STORAGE", "s3")
        monkeypatch.setenv("KHADBOT_S3_BUCKET", BUCKET)
        monkeypatch.setenv("KHADBOT_S3_REGION", REGION)
        monkeypatch.delenv("KHADBOT_S3_ENDPOINT", raising=False)
        monkeypatch.delenv("KHADBOT_S3_PREFIX", raising=False)
        assert script.main(["--raw-days", "3"]) == 0
        assert stored(client)[script.RAW_RULE_ID]["Expiration"]["Days"] == 3
        assert "Applied 2" in capsys.readouterr().out
