#!/bin/sh
# Exports your current AWS session (aws login, SSO or a profile) for the agents' Bedrock calls.
# Only Bedrock uses it; everything else talks to LocalStack.
#
#   AWS_PROFILE=management ./aws-credentials.sh           # export once
#   AWS_PROFILE=management ./aws-credentials.sh --watch   # keep it fresh (re-export every 30 min)
#
# The agents read local/.aws-agent/credentials on every turn, so a refresh needs no restart.
set -e
cd "$(dirname "$0")"
mkdir -p .aws-agent

export_once() {
    tmp=$(mktemp .aws-agent/credentials.XXXXXX)
    aws configure export-credentials --format env-no-export ${AWS_PROFILE:+--profile "$AWS_PROFILE"} \
      | awk -F= 'BEGIN { print "[default]" }
                 $1=="AWS_ACCESS_KEY_ID"     { print "aws_access_key_id=" substr($0, index($0,"=")+1) }
                 $1=="AWS_SECRET_ACCESS_KEY" { print "aws_secret_access_key=" substr($0, index($0,"=")+1) }
                 $1=="AWS_SESSION_TOKEN"     { print "aws_session_token=" substr($0, index($0,"=")+1) }' > "$tmp"
    chmod 644 "$tmp"   # read by the unprivileged hermes user in the containers
    mv "$tmp" .aws-agent/credentials
    echo "$(date '+%H:%M:%S') exported credentials for $(aws sts get-caller-identity ${AWS_PROFILE:+--profile "$AWS_PROFILE"} --query Arn --output text)"
}

export_once
if [ "$1" = "--watch" ]; then
    while sleep 1800; do export_once || echo "export failed: run 'aws sso login${AWS_PROFILE:+ --profile $AWS_PROFILE}'"; done
fi
