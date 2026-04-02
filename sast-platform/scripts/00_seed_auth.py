#!/usr/bin/env python3
"""
00_seed_auth.py — Pre-populate the StudentAuth DynamoDB table.
Jingsi Zhang | CS6620 Group 9

Creates one api_key per student_id. Keys are random 32-char hex strings.
Run this once per environment to set up course participants.

Usage:
    python 00_seed_auth.py --table StudentAuth --region us-east-1

    # Add specific students from a file (one student_id per line):
    python 00_seed_auth.py --table StudentAuth --students students.txt

    # Add a single student interactively:
    python 00_seed_auth.py --table StudentAuth --add-student zhang.jings

Output: prints each student_id and their generated api_key.
Share api_keys with students out-of-band (e.g. course portal / email).
"""

import argparse
import secrets
import sys

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError


def generate_key() -> str:
    return secrets.token_hex(16)  # 32 hex chars


def _find_existing_key(table, student_id: str) -> str | None:
    """
    Scan the (small) auth table for any existing api_key that maps to student_id.
    Returns the api_key string if found, otherwise None.
    """
    resp = table.scan(
        FilterExpression=Attr("student_id").eq(student_id),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0]["api_key"] if items else None


def seed_student(table, student_id: str) -> str | None:
    """
    Write a student → api_key entry only if that student does not yet have one.

    Checks for an existing entry by scanning on student_id (safe — the auth
    table is small). If already seeded, returns None so the caller knows to
    skip. If not found, generates a fresh key and inserts it; the
    ConditionExpression guards against the negligible chance of a random
    api_key hash collision.

    Returns the new api_key, or None if the student was already present.
    """
    if _find_existing_key(table, student_id) is not None:
        return None  # already seeded — leave existing key intact

    key = generate_key()
    try:
        table.put_item(
            Item={"api_key": key, "student_id": student_id},
            ConditionExpression="attribute_not_exists(api_key)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        # Astronomically unlikely api_key collision — treat as skip
        return None
    return key


def main():
    parser = argparse.ArgumentParser(description="Seed StudentAuth DynamoDB table")
    parser.add_argument("--table",       default="StudentAuth",  help="Auth table name")
    parser.add_argument("--region",      default="us-east-1",    help="AWS region")
    parser.add_argument("--students",    metavar="FILE",          help="File with one student_id per line")
    parser.add_argument("--add-student", metavar="STUDENT_ID",   help="Add a single student")
    args = parser.parse_args()

    ddb   = boto3.resource("dynamodb", region_name=args.region)
    table = ddb.Table(args.table)

    student_ids = []

    if args.add_student:
        student_ids.append(args.add_student.strip())

    if args.students:
        with open(args.students) as f:
            student_ids.extend(line.strip() for line in f if line.strip())

    if not student_ids:
        # Default: seed the three team members for demo purposes
        student_ids = [
            "zhang.jings",
            "sunnythreethree",
            "beibei-ui",
        ]
        print("No students specified — seeding default demo accounts.\n")

    print(f"{'student_id':<30}  {'api_key'}")
    print("-" * 65)

    seeded = 0
    skipped = 0
    for sid in student_ids:
        try:
            key = seed_student(table, sid)
            if key is None:
                print(f"{sid:<30}  (already exists — skipped)")
                skipped += 1
            else:
                print(f"{sid:<30}  {key}")
                seeded += 1
        except ClientError as e:
            print(f"ERROR seeding {sid}: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"\nSeeded {seeded} new student(s), skipped {skipped} existing into table '{args.table}'.")
    print("Share each api_key with the corresponding student — treat it like a password.")


if __name__ == "__main__":
    main()
