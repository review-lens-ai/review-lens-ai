
#!/bin/bash
set -euo pipefail

REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner')

protect_branch() {
  local branch="$1"
  local approvals="$2"

  gh api \
    --method PUT \
    "repos/$REPO/branches/$branch/protection" \
    --input - <<EOF
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": $approvals
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
EOF
}

protect_branch "dev" 1
protect_branch "main" 2
