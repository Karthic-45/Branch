#!/usr/bin/env bash
#
# Provision the AWS side: artifact bucket and the sandboxed evaluation project.
#
#   ./infra/setup_aws.sh
#
# Idempotent -- safe to re-run.
#
# Bedrock model access is NOT provisioned here. AWS retired the standalone
# "Model access" console page in 2026 -- serverless models now enable on first
# invocation instead. Open each model in the Bedrock console's Playground and
# send a test prompt before running this script:
#   - us.anthropic.claude-sonnet-4-6 (reasoning -- "us." required: us-east-1 has
#                                      no in-region option for this model, only
#                                      cross-region profiles. Not Sonnet 5 or
#                                      Opus 4.8 either: both 403 on new/low-usage
#                                      accounts, a documented AWS restriction;
#                                      see .env)
#   - amazon.titan-embed-text-v2:0   (embeddings -- no prefix needed, first-party
#                                      Amazon model with direct on-demand support)
# First-time Anthropic invocations also require a one-time use-case form,
# which the Playground prompts for automatically.

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
BUCKET="${BMEM_S3_BUCKET:-branchable-memory-artifacts}"
PROJECT="${BMEM_CODEBUILD_PROJECT:-branchable-memory-eval}"
ROLE_NAME="${BMEM_CODEBUILD_ROLE:-branchable-memory-codebuild}"

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

echo "==> Account ${ACCOUNT_ID}, region ${REGION}"

# ---------------------------------------------------------------- S3
if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  echo "==> Bucket ${BUCKET} exists"
else
  echo "==> Creating bucket ${BUCKET}"
  if [ "${REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"
  else
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}"
  fi
  aws s3api put-public-access-block --bucket "${BUCKET}" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
fi

# Repository snapshots and test logs are demo artifacts, not archives. Expiring
# them keeps storage bounded the same way row-level TTL does in the database.
echo "==> Setting 30-day expiry on ${BUCKET}"
aws s3api put-bucket-lifecycle-configuration --bucket "${BUCKET}" \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "expire-run-artifacts",
      "Status": "Enabled",
      "Filter": {"Prefix": "runs/"},
      "Expiration": {"Days": 30}
    }]
  }'

# ---------------------------------------------------------------- IAM
if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  echo "==> Role ${ROLE_NAME} exists"
else
  echo "==> Creating role ${ROLE_NAME}"
  aws iam create-role --role-name "${ROLE_NAME}" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "codebuild.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
fi

# The evaluation sandbox runs model-generated code. It gets object access to the
# one prefix that holds run artifacts and its own log group -- nothing else, and
# in particular no Bedrock and no database credentials.
echo "==> Attaching least-privilege policy"
aws iam put-role-policy --role-name "${ROLE_NAME}" \
  --policy-name "${ROLE_NAME}-policy" \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [
      {
        \"Effect\": \"Allow\",
        \"Action\": [\"s3:GetObject\", \"s3:PutObject\"],
        \"Resource\": \"arn:aws:s3:::${BUCKET}/runs/*\"
      },
      {
        \"Effect\": \"Allow\",
        \"Action\": [
          \"logs:CreateLogGroup\", \"logs:CreateLogStream\", \"logs:PutLogEvents\"
        ],
        \"Resource\": \"arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/codebuild/${PROJECT}*\"
      }
    ]
  }"

# ------------------------------------------------------- CodeBuild
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
PROJECT_JSON="{
  \"name\": \"${PROJECT}\",
  \"source\": {\"type\": \"NO_SOURCE\", \"buildspec\": \"$(sed 's/"/\\"/g; s/$/\\n/' infra/buildspec.yml | tr -d '\n')\"},
  \"artifacts\": {\"type\": \"NO_ARTIFACTS\"},
  \"environment\": {
    \"type\": \"LINUX_CONTAINER\",
    \"image\": \"aws/codebuild/standard:7.0\",
    \"computeType\": \"BUILD_GENERAL1_MEDIUM\"
  },
  \"serviceRole\": \"${ROLE_ARN}\",
  \"timeoutInMinutes\": 30
}"

if aws codebuild batch-get-projects --names "${PROJECT}" \
     --query 'projects[0].name' --output text 2>/dev/null | grep -q "${PROJECT}"; then
  echo "==> Updating CodeBuild project ${PROJECT}"
  aws codebuild update-project --cli-input-json "${PROJECT_JSON}" >/dev/null
else
  echo "==> Creating CodeBuild project ${PROJECT}"
  aws codebuild create-project --cli-input-json "${PROJECT_JSON}" >/dev/null
fi

echo
echo "Done. Add to .env:"
echo "  BMEM_S3_BUCKET=${BUCKET}"
echo "  BMEM_CODEBUILD_PROJECT=${PROJECT}"
echo
echo "Still needed: invoke each model once in the Bedrock console Playground"
echo "  Claude Sonnet 4.6 -- pick the 'US' option, not 'Global' (matches"
echo "  BMEM_REASONING_MODEL=us.anthropic.claude-sonnet-4-6)"
echo "  Titan Text Embeddings V2"
