"""
Creates Cognito test users for PoC/development.
Idempotent – safe to re-run.

Usage:
    uv run python scripts/create_test_users.py

DEV ONLY – these accounts exist solely for local testing.
"""

import sys

import boto3

REGION = "eu-central-1"
POOL_NAME = "iam-gateway-dev-user-pool"
CLIENT_NAME = "iam-gateway-dev-cli-client"

# password satisfies: 8+ chars, upper, lower, numbers (symbols not required)
TEST_PASSWORD = "DevTest1234"

TEST_USERS = [
    {
        "username": "alice@test.local",
        "group": "dept_engineering_cl_2",
        "label": "Engineering / Restricted (cl=2)",
    },
    {
        "username": "bob@test.local",
        "group": "dept_legal_cl_1",
        "label": "Legal / Classified (cl=1)",
    },
    {
        "username": "eve@test.local",
        "group": "dept_security_cl_4",
        "label": "Security / Top Secret (cl=4)",
    },
]


def get_pool_id(idp) -> str:
    paginator = idp.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"] == POOL_NAME:
                return pool["Id"]
    print(f"ERROR: User pool '{POOL_NAME}' not found. Run terraform apply first.")
    sys.exit(1)


def create_or_update_user(idp, pool_id: str, username: str, group: str, label: str) -> None:
    try:
        idp.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            UserAttributes=[
                {"Name": "email", "Value": username},
                {"Name": "email_verified", "Value": "true"},
            ],
            MessageAction="SUPPRESS",
        )
        print(f"  [created]  {username}  –  {label}")
    except idp.exceptions.UsernameExistsException:
        print(f"  [exists]   {username}  –  {label}")

    idp.admin_set_user_password(
        UserPoolId=pool_id,
        Username=username,
        Password=TEST_PASSWORD,
        Permanent=True,
    )

    # add to group (no-op if already a member)
    idp.admin_add_user_to_group(
        UserPoolId=pool_id,
        Username=username,
        GroupName=group,
    )
    print(f"             → group: {group}")


def main() -> None:
    idp = boto3.client("cognito-idp", region_name=REGION)
    pool_id = get_pool_id(idp)
    print(f"Pool: {pool_id}\n")

    for user in TEST_USERS:
        create_or_update_user(idp, pool_id, **user)

    print("\nDone.")


if __name__ == "__main__":
    main()
