#!/usr/bin/env bash
#
# Deploy the API + live demo UI to a public HTTPS URL.
#
#   ./infra/deploy_beanstalk.sh
#
# Idempotent -- safe to re-run; a second run ships a new version to the
# existing environment rather than recreating it.
#
# Why Elastic Beanstalk and not App Runner (the original architecture's
# target): App Runner closed to new customers on 2026-04-30 and this AWS
# account postdates that. AWS's own replacement, ECS Express Mode, requires a
# pre-built image in ECR -- no source deploy -- which means Docker, and Docker
# is not installed on the machine this project is developed on. Elastic
# Beanstalk's single-container Docker platform builds the Dockerfile
# server-side: no local Docker, no ECR, and it has been stable and open to new
# accounts for over a decade.
#
# Prerequisites this script does NOT do for you:
#   - infra/setup_aws.sh already run (S3 bucket + CodeBuild project)
#   - Each Bedrock model invoked once in the console Playground (see that
#     script's own output)
#   - .env filled in with a real BMEM_DATABASE_URL and AWS_REGION
# Deploying before those are done still produces a working URL -- the UI loads
# and shows any existing branch tree -- but starting a new migration will fail
# without Bedrock and CodeBuild access.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

command -v aws >/dev/null || { echo "aws CLI not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found -- needed to safely encode environment variables as JSON" >&2; exit 1; }
command -v zip >/dev/null || { echo "zip not found" >&2; exit 1; }
[ -f .env ] || { echo ".env not found -- copy .env.example and fill it in first" >&2; exit 1; }

REGION="${AWS_REGION:-us-east-1}"
APP_NAME="${BMEM_EB_APP:-branchable-memory}"
ENV_NAME="${BMEM_EB_ENV:-branchable-memory-demo}"
INSTANCE_TYPE="${BMEM_EB_INSTANCE_TYPE:-t3.micro}"  # free-tier eligible on a new account
EC2_ROLE="${BMEM_EB_EC2_ROLE:-aws-elasticbeanstalk-ec2-role}"
SERVICE_ROLE="${BMEM_EB_SERVICE_ROLE:-aws-elasticbeanstalk-service-role}"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
echo "==> Account ${ACCOUNT_ID}, region ${REGION}"

# --------------------------------------------------- IAM (EC2 instance role)
# AWS stopped auto-creating this on first use; recent accounts genuinely lack
# it. See: https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/concepts-roles-instance.html
if aws iam get-role --role-name "${EC2_ROLE}" >/dev/null 2>&1; then
  echo "==> EC2 role ${EC2_ROLE} exists"
else
  echo "==> Creating EC2 role ${EC2_ROLE}"
  aws iam create-role --role-name "${EC2_ROLE}" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' >/dev/null
  for policy in AWSElasticBeanstalkWebTier AWSElasticBeanstalkMulticontainerDocker; do
    aws iam attach-role-policy --role-name "${EC2_ROLE}" \
      --policy-arn "arn:aws:iam::aws:policy/${policy}"
  done
  aws iam create-instance-profile --instance-profile-name "${EC2_ROLE}" >/dev/null 2>&1 || true
  aws iam add-role-to-instance-profile --instance-profile-name "${EC2_ROLE}" --role-name "${EC2_ROLE}" 2>/dev/null || true
  echo "==> Waiting for the instance profile to propagate"
  sleep 10
fi

# --------------------------------------------------- IAM (EB service role)
if aws iam get-role --role-name "${SERVICE_ROLE}" >/dev/null 2>&1; then
  echo "==> Service role ${SERVICE_ROLE} exists"
else
  echo "==> Creating service role ${SERVICE_ROLE}"
  aws iam create-role --role-name "${SERVICE_ROLE}" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "elasticbeanstalk.amazonaws.com"},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"sts:ExternalId": "elasticbeanstalk"}}
      }]
    }' >/dev/null
  for policy in AWSElasticBeanstalkEnhancedHealth service-role/AWSElasticBeanstalkService; do
    aws iam attach-role-policy --role-name "${SERVICE_ROLE}" \
      --policy-arn "arn:aws:iam::aws:policy/${policy}"
  done
fi

# --------------------------------------------------- EB application
if aws elasticbeanstalk describe-applications --application-names "${APP_NAME}" \
     --query 'Applications[0].ApplicationName' --output text 2>/dev/null | grep -q "${APP_NAME}"; then
  echo "==> Application ${APP_NAME} exists"
else
  echo "==> Creating application ${APP_NAME}"
  aws elasticbeanstalk create-application --application-name "${APP_NAME}" \
    --description "Version control for what an agent believes -- CockroachDB x AWS Hackathon" >/dev/null
fi

# --------------------------------------------------- source bundle
VERSION_LABEL="v-$(date +%Y%m%d%H%M%S)"
BUNDLE="/tmp/bmem-${VERSION_LABEL}.zip"
echo "==> Bundling source as ${BUNDLE}"
rm -f "${BUNDLE}"
zip -q -r "${BUNDLE}" Dockerfile Dockerrun.aws.json pyproject.toml src/ \
  -x '*__pycache__*' -x '*.pyc'

STORAGE_BUCKET="$(aws elasticbeanstalk create-storage-location --query 'S3Bucket' --output text)"
S3_KEY="${APP_NAME}/${VERSION_LABEL}.zip"
echo "==> Uploading to s3://${STORAGE_BUCKET}/${S3_KEY}"
aws s3 cp "${BUNDLE}" "s3://${STORAGE_BUCKET}/${S3_KEY}" --only-show-errors
rm -f "${BUNDLE}"

echo "==> Registering application version ${VERSION_LABEL}"
aws elasticbeanstalk create-application-version \
  --application-name "${APP_NAME}" \
  --version-label "${VERSION_LABEL}" \
  --source-bundle "S3Bucket=${STORAGE_BUCKET},S3Key=${S3_KEY}" >/dev/null

# --------------------------------------------------- environment variables
# Read from .env rather than the current shell, and named explicitly rather
# than forwarded wholesale -- this is exactly the set bmem.api's import chain
# reads, no more. Never echoed: piped straight from file into jq.
ENV_KEYS=(
  BMEM_DATABASE_URL AWS_REGION BMEM_S3_BUCKET BMEM_CODEBUILD_PROJECT
  BMEM_REASONING_MODEL BMEM_EMBEDDING_MODEL BMEM_EMBEDDING_DIMS
  BMEM_TARGET_REPO BMEM_TARGET_REF BMEM_TEST_SELECTOR BMEM_BRANCH_TOKEN_BUDGET
  # Forwarded the same way as everything else, not special-cased: whichever
  # backends are fake locally (see .env) is exactly what a redeploy should
  # ship. Flip one off in .env and re-run this script once its AWS access
  # actually works -- no separate "prod config" to keep in sync by hand.
  BMEM_FAKE_EMBEDDINGS BMEM_FAKE_PLANNER BMEM_FAKE_CODEBUILD
)
OPTION_SETTINGS="[]"
for key in "${ENV_KEYS[@]}"; do
  value="$(grep -E "^${key}=" .env | head -1 | cut -d= -f2-)"
  [ -n "${value}" ] || continue
  OPTION_SETTINGS="$(jq --arg k "${key}" --arg v "${value}" \
    '. += [{"Namespace":"aws:elasticbeanstalk:application:environment","OptionName":$k,"Value":$v}]' \
    <<<"${OPTION_SETTINGS}")"
done
OPTION_SETTINGS="$(jq --arg profile "${EC2_ROLE}" --arg type "${INSTANCE_TYPE}" \
  '. += [
    {"Namespace":"aws:autoscaling:launchconfiguration","OptionName":"IamInstanceProfile","Value":$profile},
    {"Namespace":"aws:autoscaling:launchconfiguration","OptionName":"InstanceType","Value":$type},
    {"Namespace":"aws:elasticbeanstalk:environment","OptionName":"ServiceRole","Value":"'"${SERVICE_ROLE}"'"}
  ]' <<<"${OPTION_SETTINGS}")"
echo "${OPTION_SETTINGS}" > /tmp/bmem-eb-options.json

# --------------------------------------------------- environment
if aws elasticbeanstalk describe-environments --application-name "${APP_NAME}" \
     --environment-names "${ENV_NAME}" --query 'Environments[0].Status' --output text 2>/dev/null \
     | grep -qE "^(Ready|Updating|Launching)$"; then
  echo "==> Deploying ${VERSION_LABEL} to existing environment ${ENV_NAME}"
  aws elasticbeanstalk update-environment \
    --application-name "${APP_NAME}" --environment-name "${ENV_NAME}" \
    --version-label "${VERSION_LABEL}" \
    --option-settings "file:///tmp/bmem-eb-options.json" >/dev/null
else
  # The most current Docker platform, queried rather than hardcoded -- solution
  # stack names change over time and a stale one just fails create-environment.
  SOLUTION_STACK="$(aws elasticbeanstalk list-available-solution-stacks \
    --query "SolutionStacks[?contains(@, 'running Docker')] | [0]" --output text)"
  echo "==> Creating environment ${ENV_NAME} on: ${SOLUTION_STACK}"
  aws elasticbeanstalk create-environment \
    --application-name "${APP_NAME}" --environment-name "${ENV_NAME}" \
    --solution-stack-name "${SOLUTION_STACK}" \
    --version-label "${VERSION_LABEL}" \
    --tier "Name=WebServer,Type=Standard" \
    --option-settings "file:///tmp/bmem-eb-options.json" \
    --option-settings "Namespace=aws:autoscaling:asg,OptionName=MinSize,Value=1" \
    --option-settings "Namespace=aws:autoscaling:asg,OptionName=MaxSize,Value=1" >/dev/null
fi
rm -f /tmp/bmem-eb-options.json

echo "==> Waiting for the environment to become ready (several minutes)"
aws elasticbeanstalk wait environment-updated --application-name "${APP_NAME}" --environment-name "${ENV_NAME}" || true

URL="$(aws elasticbeanstalk describe-environments --application-name "${APP_NAME}" \
  --environment-names "${ENV_NAME}" --query 'Environments[0].CNAME' --output text)"
STATUS="$(aws elasticbeanstalk describe-environments --application-name "${APP_NAME}" \
  --environment-names "${ENV_NAME}" --query 'Environments[0].Health' --output text)"

echo
echo "==> Environment health: ${STATUS}"
echo "==> Live demo: http://${URL}"
echo "    If Health is not Green, check: aws elasticbeanstalk describe-events --application-name ${APP_NAME} --environment-name ${ENV_NAME} --max-records 20"
