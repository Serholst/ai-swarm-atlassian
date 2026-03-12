# Agent Profile: Reviewer

The Reviewer evaluates generated work plans for quality, completeness, and consistency.

## Role

Quality gate between work plan generation and story creation.
Can be human or AI — both operate via the same PLAN REVIEW Jira task.

## Responsibilities

- Read the generated work plan from `outputs/{KEY}/{KEY}_work_plan.md`
- Check validation rules (step format, layer tags, acceptance criteria)
- Evaluate story decomposition balance across layers
- Approve or request refinement with specific feedback

## Entry Points

```bash
# Check if plan is ready for review
python execute.py --task PROJ-123   # routes to review handler if status = "Human Plan Review"

# Review via skill
/review-workplan PROJ-123

# Submit refinement feedback
/refine-plan PROJ-123 "Split step 3 into BE and FE. Add monitoring step."
```

## Review Checklist

- [ ] Each step has `**Step N:**`, `**Specification:**`, `**Layer:**`, `**Files:**`, `**Acceptance:**`
- [ ] All layers are valid: `BE / FE / INFRA / DB / QA / DOCS / GEN`
- [ ] Dependencies are logically ordered (no circular refs)
- [ ] Acceptance criteria are verifiable (not "ensure it works")
- [ ] QA steps exist for every business-critical feature
- [ ] INFRA steps exist if the feature requires new infrastructure
- [ ] Story count is reasonable (3–15 for a typical feature)

## Output

Reviewer sets the PLAN REVIEW Jira task to `Done` to trigger story creation.
Or adds a comment with refinement feedback and sets back to `In Progress`.
