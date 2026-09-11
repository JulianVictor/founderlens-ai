# Architecture Decision Records

One file per decision that is expensive to reverse or surprising to a newcomer.
An ADR records the decision *and the reasoning available at the time*, so that a
later reader can tell whether the reasons still hold.

ADRs are immutable once accepted. A decision that changes gets a new record that
supersedes the old one; the old record stays, marked superseded. Editing history
to look prescient defeats the purpose.

| # | Decision | Status | Date |
| --- | --- | --- | --- |
| [001](001-neo4j-for-vector-and-graph-retrieval.md) | Neo4j for both vector and graph retrieval | Accepted | 2026-09-11 |

## Format

Title, then: Status, Date, Context, Decision, Rationale, Alternatives
considered, Consequences (positive / negative and accepted / neutral), and
Revisit when.

The last two sections carry most of the value. "Consequences" is where the costs
we knowingly accepted are written down, and "Revisit when" states the conditions
that should reopen the question — so that revisiting is triggered by evidence
rather than by whoever is most annoyed by the decision that week.
