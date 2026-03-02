#!/usr/bin/env bash
# Detect duplicate constant definitions across the executor package.
# Exit 1 if duplicates found (blocks commit), 0 otherwise.
#
# Usage:
#   ./scripts/check_duplicate_constants.sh          # scan all
#   ./scripts/check_duplicate_constants.sh file1.py  # scan specific files

set -euo pipefail

SRC_DIR="src/executor"
EXIT_CODE=0

# Patterns: top-level constant assignments that must be defined only once.
# Anchored to start-of-line to avoid matching usages like "if x not in CONST:".
# Format: "regex_pattern|human_label|allowed_file"
RULES=(
  '^\s*VALID_LAYERS\s*[=:]|VALID_LAYERS|constants.py'
  '^\s*LAYER_CODES\s*[=:]|LAYER_CODES|constants.py'
  '^\s*CORE_DOC_MAX_CHARS\s*=|CORE_DOC_MAX_CHARS|constants.py'
  '^\s*SUPPORTING_DOC_MAX_CHARS\s*=|SUPPORTING_DOC_MAX_CHARS|constants.py'
)

for rule in "${RULES[@]}"; do
  IFS='|' read -r pattern label allowed_file <<< "$rule"

  # Find files defining this constant (exclude imports and comments)
  matches=$(grep -rn --include='*.py' -E "$pattern" "$SRC_DIR" \
    | grep -v "^.*:.*import " \
    | grep -v "^.*:#" \
    || true)

  if [ -z "$matches" ]; then
    continue
  fi

  # Count unique files with definitions
  files=$(echo "$matches" | cut -d: -f1 | sort -u)
  file_count=$(echo "$files" | wc -l | tr -d ' ')

  if [ "$file_count" -gt 1 ]; then
    # Check if non-allowed files define it
    offenders=$(echo "$files" | grep -v "$allowed_file" || true)
    if [ -n "$offenders" ]; then
      echo "ERROR: '$label' is defined in multiple files (canonical source: $allowed_file)"
      echo "$matches"
      echo ""
      EXIT_CODE=1
    fi
  fi
done

if [ "$EXIT_CODE" -eq 0 ]; then
  echo "No duplicate constants detected."
fi

exit $EXIT_CODE
