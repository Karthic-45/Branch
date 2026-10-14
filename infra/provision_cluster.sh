#!/usr/bin/env bash
#
# Provision the CockroachDB Cloud cluster this project runs on.
#
#   ./infra/provision_cluster.sh
#
# Standard tier, not Basic. Basic does not permit `SET CLUSTER SETTING
# feature.vector_index.enabled` (required for the vector index) or the zone
# configuration change that widens the time-travel window.
#
# Install ccloud:  https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started

set -euo pipefail

CLUSTER_NAME="${BMEM_CLUSTER_NAME:-branchable-memory}"
CLOUD="aws"
REGION="${AWS_REGION:-us-east-1}"
SQL_USER="${BMEM_SQL_USER:-bmem}"

command -v ccloud >/dev/null || {
  echo "ccloud not found. See:" >&2
  echo "  https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-get-started" >&2
  exit 1
}

echo "==> Authenticating"
ccloud auth login

# Idempotent: reuse the cluster if a previous run already made it.
if ccloud cluster list --output json 2>/dev/null | grep -q "\"name\": *\"${CLUSTER_NAME}\""; then
  echo "==> Cluster '${CLUSTER_NAME}' already exists, reusing it"
else
  echo "==> Creating Standard cluster '${CLUSTER_NAME}' on ${CLOUD}/${REGION}"
  # Same cloud and region as Bedrock, S3, and CodeBuild: the memory layer sits
  # next to the compute that uses it rather than across the public internet.
  ccloud cluster create standard "${CLUSTER_NAME}" \
    --cloud "${CLOUD}" \
    --region "${REGION}"
fi

echo "==> Creating SQL user '${SQL_USER}'"
ccloud cluster user create "${CLUSTER_NAME}" "${SQL_USER}" || \
  echo "    (user already exists)"

echo
echo "==> Connection string"
echo "Put this in .env as BMEM_DATABASE_URL, with /branchable as the database:"
echo
ccloud cluster sql "${CLUSTER_NAME}" --connection-string
echo
echo "Then:"
echo "  python infra/apply_schema.py --bootstrap   # cluster settings + database"
echo "  python infra/apply_schema.py               # tables and the vector index"
echo "  pytest -m cluster                          # prove it works"
