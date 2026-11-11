# Annotation guide (precision evaluation)

Files: `reports/eval_sample.csv` (extracted relations) and `reports/eval_kb_sample.csv` (KB-enriched nodes).
Open in Excel (UTF-8 with BOM). Annotator A fills `label_a`, annotator B fills `label_b`, **independently**
(do not look at the other column). Use `1` = correct, `0` = wrong. Then run:

```
python src/evaluate.py score
python src/evaluate.py score --file reports/eval_kb_sample.csv
```

**Current protocol:** `label_a` is pre-filled with an LLM-assisted labelling (Claude; reasons in `note`
as `A: ...`, also in `reports/eval_sample_claude.csv`). One team member reviews and corrects `label_a`.
The other member labels the blind subset `reports/eval_subset_B.csv` (108 rows, no A labels) in
`label_b` / `note_b`, then: `python src/evaluate.py merge-b` copies them into `eval_sample.csv`.
Kappa is computed on that subset. The report states this protocol.

For rows where A and B disagree, discuss; if you agree the triple is correct write `ok` at the start of
`note` (otherwise it counts as wrong). Cohen's kappa is computed on the independent labels.

## eval_sample.csv - a triple is correct (1) only if ALL hold

1. The sentence **explicitly** states the action as happened / happening / officially agreed or pledged
   (not planned, hypothetical, negated, rumoured, or merely requested).
2. The relation type matches the definition below.
3. Direction is right (source does the action to target). Symmetric types (MET_WITH, PHONE_CALL,
   SIGNED_AGREEMENT, UPGRADED_PARTNERSHIP) ignore direction.
4. Both entities are the right real-world entities (check the Wikidata QID if unsure).
   A country may stand for its government / head of state / minister acting officially
   ("Brazil signed ..." or "President Lula signed ..." -> Brazil is fine). It may NOT stand for a
   non-government actor (a trade-union or party delegation, a company, citizens).

| Type | Correct when the sentence says source ... target |
|---|---|
| MET_WITH | held talks / met / had a summit with (in person or online) |
| PHONE_CALL | had a phone / video call with |
| VISITED | made a visit to (target = host country / organization / person) |
| SIGNED_AGREEMENT | signed an agreement, treaty, MoU, deal or joint statement with |
| UPGRADED_PARTNERSHIP | upgraded or established diplomatic relations / (strategic) partnership with |
| MILITARY_AID | provided or pledged weapons / military assistance to |
| ECONOMIC_AID | provided or pledged economic, financial or humanitarian aid / loans to |
| SANCTIONED | imposed sanctions / embargo on |
| EXPELLED_DIPLOMAT | expelled diplomats of, **recalled its ambassador from**, or cut ties with (summoning an ambassador is NOT enough) |
| CONDEMNED | condemned, criticised, accused or threatened |
| SUPPORTED | expressed support for, praised, welcomed or backed (an explicit congratulation of target counts as praise) |

Typical wrong cases: the target is only the topic / location / victim ("Russia supports a plan to end the
war in Ukraine" -> no SUPPORTED to Ukraine); the action is only planned ("will visit"); the entity is a
different person with the same name.

## eval_kb_sample.csv - a KB-enriched node is correct (1) only if BOTH hold

1. The mention shown in `source` (`<- mention '...'`) really refers to this person in the sentence.
2. The structural edge (`relation` with the Wikidata position title -> `target`) is a true fact
   (the position may be past - it is not tied to the sentence date).

## Borderline cases (decided by both annotators when adjudicating A/B disagreements)

- "X spoke with Y" between leaders / ministers in different countries counts as PHONE_CALL (remote talk).
- Recalling an ambassador "for consultations" counts as EXPELLED_DIPLOMAT (recall), as defined above.
- An organization that is only the venue of a summit (e.g. "talks at the G7 summit") is not a MET_WITH target.
- A commitment to expand sanctions in the future is **not** SANCTIONED (planned action).
- An approved arms *sale* is not MILITARY_AID; joining an agreement is not SIGNED_AGREEMENT with the agreement.
- A navy ship / carrier group visit is **not** VISITED; a defence minister's official visit is.
