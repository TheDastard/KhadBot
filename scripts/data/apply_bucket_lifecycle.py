"""
scripts/data/apply_bucket_lifecycle.py

Applies KhadBot's retention rules to the hosted bucket as S3 lifecycle rules: objects under ``raw/`` expire after
``KHADBOT_RETENTION_RAW_DAYS`` days (default 14) and objects under ``annotated/`` after
``KHADBOT_RETENTION_ANNOTATED_DAYS`` (default 90). ``events/`` and ``records/`` get no rule: events are what every pull
is re-processed from, and unreferenced records are removed by the worker's cleanup job instead.

PutBucketLifecycleConfiguration replaces the bucket's whole configuration, so the script reads the current rules first,
replaces only the two it owns (rule IDs starting ``khadbot-``) and keeps every other rule as it is. It reads the
configuration back afterwards and exits non-zero if a rule is missing or differs, so a provider that accepts the
request but stores something else (or nothing) is noticed. docs/hosting.md says what Cloudflare R2 and SeaweedFS do
with these rules.

Run it with the bucket's settings in the environment (the same ``KHADBOT_S3_*`` and ``AWS_*`` variables the API reads):

    python scripts/data/apply_bucket_lifecycle.py [--dry-run] [--raw-days N] [--annotated-days N]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from khadbot.config import RetentionConfig, StorageBackend, StorageConfig

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

RULE_ID_PREFIX = "khadbot-"
RAW_RULE_ID = RULE_ID_PREFIX + "expire-raw"
ANNOTATED_RULE_ID = RULE_ID_PREFIX + "expire-annotated"


def _expiry_rule(rule_id: str, prefix: str, days: int) -> dict[str, Any]:
    if days < 1:
        raise ValueError(f"{rule_id}: an expiry of {days} days is not valid; S3 lifecycle rules need at least 1")
    return {"ID": rule_id, "Status": "Enabled", "Filter": {"Prefix": prefix}, "Expiration": {"Days": days}}


def lifecycle_rules(key_prefix: str, raw_days: int, annotated_days: int) -> list[dict[str, Any]]:
    """
    The two expiry rules. ``key_prefix`` is ``KHADBOT_S3_PREFIX`` (``""`` for none): the store keeps every object
    under it, so the rules' prefixes start with it.
    """
    base = f"{key_prefix.strip('/')}/" if key_prefix.strip("/") else ""
    return [
        _expiry_rule(RAW_RULE_ID, base + "raw/", raw_days),
        _expiry_rule(ANNOTATED_RULE_ID, base + "annotated/", annotated_days),
    ]


def merge_rules(existing: Sequence[dict[str, Any]], ours: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """``existing`` without any rule this script owns, followed by ``ours``."""
    foreign = [rule for rule in existing if not str(rule.get("ID", "")).startswith(RULE_ID_PREFIX)]
    return [*foreign, *ours]


def problems(stored: Sequence[dict[str, Any]], ours: Sequence[dict[str, Any]]) -> list[str]:
    """What is wrong with the stored rules compared with ``ours``: a missing, disabled or different rule."""
    by_id = {rule.get("ID"): rule for rule in stored}
    found: list[str] = []
    for want in ours:
        got = by_id.get(want["ID"])
        if got is None:
            found.append(f"{want['ID']}: not stored")
            continue
        got_prefix = (got.get("Filter") or {}).get("Prefix", got.get("Prefix"))
        if got.get("Status") != "Enabled":
            found.append(f"{want['ID']}: status is {got.get('Status')!r}")
        elif got_prefix != want["Filter"]["Prefix"]:
            found.append(f"{want['ID']}: prefix is {got_prefix!r}, expected {want['Filter']['Prefix']!r}")
        elif (got.get("Expiration") or {}).get("Days") != want["Expiration"]["Days"]:
            found.append(f"{want['ID']}: expires after {(got.get('Expiration') or {}).get('Days')!r} days")
    return found


# ---------------------------------------------------------------------------
# Bucket
# ---------------------------------------------------------------------------


def make_client(storage: StorageConfig) -> Any:
    """
    A boto3 client for the configured bucket. Checksums are sent only where the API requires one: boto3 otherwise adds
    a CRC checksum to every request, and Cloudflare R2's documentation lists the checksum-algorithm headers as
    unsupported on PutBucketLifecycleConfiguration.
    """
    import boto3
    from botocore.config import Config

    config = Config(
        retries={"mode": "standard", "max_attempts": 5},
        s3={"addressing_style": "path"} if storage.s3_endpoint else {},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )
    return boto3.client(
        "s3", endpoint_url=storage.s3_endpoint or None, region_name=storage.s3_region or None, config=config
    )


def current_rules(client: Any, bucket: str) -> list[dict[str, Any]]:
    """The bucket's lifecycle rules; none if it has no configuration."""
    from botocore.exceptions import ClientError

    try:
        return list(client.get_bucket_lifecycle_configuration(Bucket=bucket).get("Rules", []))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
            return []
        raise


def apply_rules(client: Any, bucket: str, ours: Sequence[dict[str, Any]], *, dry_run: bool = False) -> list[str]:
    """
    Store ``ours`` in the bucket beside its other rules, then read the configuration back.

    Returns:
        The problems found reading it back (empty when every rule is stored as given). A dry run writes nothing and
        returns nothing.
    """
    merged = merge_rules(current_rules(client, bucket), ours)
    if dry_run:
        return []
    client.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": merged})
    return problems(current_rules(client, bucket), ours)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    retention = RetentionConfig()
    parser = argparse.ArgumentParser(description="Apply KhadBot's retention rules to the hosted bucket.")
    parser.add_argument("--raw-days", type=int, default=retention.raw_days, help="Days before raw/ objects expire")
    parser.add_argument(
        "--annotated-days", type=int, default=retention.annotated_days, help="Days before annotated/ objects expire"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the rules that would be applied; change nothing")
    args = parser.parse_args(argv)

    storage = StorageConfig()
    if storage.backend is not StorageBackend.S3 or not storage.s3_bucket:
        print("Set KHADBOT_STORAGE=s3 and KHADBOT_S3_BUCKET: there is no bucket to apply rules to.", file=sys.stderr)
        return 2
    try:
        ours = lifecycle_rules(storage.s3_prefix, args.raw_days, args.annotated_days)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(ours, indent=2))
    client = make_client(storage)
    found = apply_rules(client, storage.s3_bucket, ours, dry_run=args.dry_run)
    if args.dry_run:
        print("Dry run: nothing was changed.")
        return 0
    if found:
        print("The bucket did not store the rules as given:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    print(f"Applied {len(ours)} lifecycle rules to {storage.s3_bucket}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
