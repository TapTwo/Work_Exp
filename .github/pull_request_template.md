## Change

- **CHG / RITM:**
- **Reason:**
- **Risk result (AlgoSec / review):**
- **Deployment scope (device groups / template stacks):**

## Test flows

| Flow | Expect |
| --- | --- |
| `src -> dst:port (from -> to)` | allow / deny |

## Rollback note

How this change is backed out if post-checks fail (git revert + redeploy, or Panorama config version N):

## Checklist

- [ ] CI green (lint, render)
- [ ] Rendered `panorama.set` diff reviewed, not just the YAML
- [ ] Reviewer is not the author
- [ ] Temporary rules carry an expiry
