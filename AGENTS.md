# accountability-reviews

Read the root `../AGENTS.md` first for workspace-wide rules.

Weekly review of each person against their seat on the Ninety accountability chart. Graham's idea
(2026-11-27): for each accountability, find the number that measures it, grade it against its goal,
put everyone in one central place, and surface data that would help the person improve.

**Public on GitHub Pages with first names only** (Graham's choice 2026-11-27, after first choosing private):
https://ghnatiw-ship-it.github.io/accountability-reviews/ (noindex; repo is public). Never put a last name in
`seats.json`, the ledger, a report or a commit. Never email or message the output to anyone. Not scheduled yet;
`index.html` = the latest week and only goes live when committed and pushed.

## Run
```
python3 review.py                  # last completed Mon-Sun week
python3 review.py --week 2026-11-16
```
Reads the Labour 2026 sheets through `gws`, so **never run it inside an agent sandbox** (root rule 10).

## Files
- `seats.json`: one entry per person: seat, venue, accountabilities (copied from Ninety), and the
  `measure` each maps to (`null` = nothing connected). Add a person here, no code change.
- `review.py`: measures + grading + HTML. Thresholds are constants at the top (first pass, Graham to tune).
- `data/ledger.csv`: the central record, one row per person x accountability x week (re-runs replace the week).
- `reports/accountability-review-<monday>.html`: the readable review.

## Measures (v1, the four GMs + Cathedral manager)
| Accountability | Measure | Source |
|---|---|---|
| Labour / Personnel | week labour % vs goal (`eos_goals`) | Labour 2026 sheets (`labour_source`) |
| Income Statement | POS net sales vs same week last year (364 days); prior 4-week average when there is no last year | POS mirror (`pos_source`) |
| Processes | share of trading nights with a manager logbook entry | NocoDB (`logbook_context`) |
| Guest Experience | nights with a guest issue logged: context only, not graded | NocoDB |
| LMA, Health & Safety, Scorecard, Andy's extra roles | no data | |

Known approximations: weekly labour % = sum of (daily sheet % x sheet sales) / sheet sales, so it leaves out
the fixed salaried hours the combined labour sheet adds on closed days; a venue's number is shared by
everyone accountable for it. Eloise labour goal is 20% here (`eos_goals`) but the Eloise sheet's Goal row
says 24%: open question for Graham.

## Chart source
Ninety > Accountability Chart (app.ninety.io/chart/primary), read through signed-in Mac Chrome. About 75
seats; several people hold more than one seat (review per person, not per seat). Next groups: venue
managers (operations, personnel, bar, kitchen), then directors.
