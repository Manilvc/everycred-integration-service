# `app/core/secret_store.py`

> Last updated: 2026-09-29

## Purpose

Keep credentials and user connection inputs **out of the service's
database**. Callers store a JSON object and receive a reference; the
database stores only that reference.

| What | Secret name | DB column holding the reference |
|------|-------------|---------------------------------|
| A client's credentials for a tool | `<prefix>/clients/<client_id>/tools/<tool_code>` | `client_tool_credentials.secret_reference` |
| A user's connection inputs | `<prefix>/clients/<client_id>/users/<user_uuid>/connections/<type_code>` | `user_integration_connections.parameters_secret_reference` |

Names contain ids and codes only, never personal data, because they
appear in the AWS console and CloudTrail.

## Backends

| `SECRET_STORE_BACKEND` | Stores values in | Reference | Use |
|------------------------|------------------|-----------|-----|
| `aws` | AWS Secrets Manager, each secret encrypted with `SECRETS_KMS_KEY_ID` | Secret ARN | Staging and production (**required** in production) |
| `local` (default) | `local_secrets` table, Fernet-encrypted with `CONNECTION_ENCRYPTION_KEYS` | `local:<uuid>` | Local development and tests only |

The service refuses to start with `ENVIRONMENT=production` and the
local backend.

## Public API

`SecretStore` protocol, provided per request by the `SecretStoreDep`
dependency:

- `create(name, value, tags) -> reference` — creates the secret, or
  reuses one with the same name (restoring it if it was deleted within
  its recovery window). Names are deterministic, so saving twice never
  leaves orphans.
- `read(reference) -> dict`
- `replace(reference, value)` — AWS keeps the previous version as
  `AWSPREVIOUS`.
- `delete(reference)` — AWS schedules deletion after
  `SECRET_RECOVERY_WINDOW_DAYS` (7–30); a missing secret is ignored.

Every failure raises `SecretStoreError` (`503 secret_store_unavailable`);
only the AWS error code is logged, never a value.

## Consistency with the database

AWS writes are not part of the database transaction:

- **Create** runs before the database commit. If the commit fails, the
  secret stays but is reused by the next save of the same name.
- **Delete** runs after the database commit, so a row never points at a
  deleted secret. If the AWS call fails, a warning is logged and the
  secret is reused if the same item is saved again.

## AWS setup

1. **KMS key.** Create a symmetric customer-managed key (or use an
   existing one) and note its ARN or alias.
2. **IAM permissions** for the service's role:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "secretsmanager:CreateSecret",
           "secretsmanager:GetSecretValue",
           "secretsmanager:PutSecretValue",
           "secretsmanager:DescribeSecret",
           "secretsmanager:RestoreSecret",
           "secretsmanager:DeleteSecret",
           "secretsmanager:TagResource"
         ],
         "Resource": "arn:aws:secretsmanager:<region>:<account-id>:secret:everycred/integration-service/*"
       },
       {
         "Effect": "Allow",
         "Action": ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"],
         "Resource": "<kms-key-arn>",
         "Condition": {
           "StringEquals": {
             "kms:ViaService": "secretsmanager.<region>.amazonaws.com"
           }
         }
       }
     ]
   }
   ```

   The `kms:ViaService` condition lets the role use the key only through
   Secrets Manager.
3. **Credentials.** The service uses the standard AWS credential chain
   (IAM role on ECS/EC2/EKS, or `AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY` for local testing). No AWS keys go in code.
4. **Settings:**

   ```
   SECRET_STORE_BACKEND=aws
   AWS_REGION=ap-south-1
   SECRETS_KMS_KEY_ID=arn:aws:kms:ap-south-1:<account-id>:key/<key-id>
   SECRETS_NAME_PREFIX=everycred/integration-service
   SECRET_RECOVERY_WINDOW_DAYS=7
   ```

   Use a different `SECRETS_NAME_PREFIX` per environment if several share
   one AWS account.

## Tests

`tests/core/test_secret_store.py` runs the AWS store against moto's
mocked Secrets Manager and KMS (including checking that the configured
KMS key is used) and the local store against SQLite.
