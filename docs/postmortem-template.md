# Postmortem: <short title>

Date: YYYY-MM-DD. Status: draft / reviewed. Author: <name>. Blameless: describe what the system
and the process allowed, not who made the mistake.

## Summary

Two or three sentences. What broke, for how long, how it was found, how it ended.

## Impact

- Who or what was affected (routes, users, jobs).
- SLO numbers, from the metrics, not from memory: error ratio at the worst point, share of the
  30 day budget spent, whether the latency SLO was hit too.

## Timeline (UTC)

| Time | Event |
|---|---|
| hh:mm:ss | Change or trigger |
| hh:mm:ss | First bad requests (from the data, not from when someone noticed) |
| hh:mm:ss | Alert pending, alert firing, notification delivered |
| hh:mm:ss | First human action |
| hh:mm:ss | Mitigation applied |
| hh:mm:ss | Back within SLO, alert resolved |

## Detection

Which alert fired, and how long after the first bad request. Did a burn-rate window help or get in
the way? Did the alert point at the right place (labels, annotations, links)?

## Root cause

What actually failed, and the chain that led there. Name the evidence: the query or the log lines
that show it. Separate the trigger (what started it) from the cause (why it was able to hurt).

## What went well, what did not, where we were lucky

Short lists.

## Action items

| Action | Type (prevent / detect / mitigate) | Owner | Ticket / due |
|---|---|---|---|
| | | | |

An item with no owner is a wish. Prefer a few items that will be done.

## Appendix

Queries, log excerpts and graphs used above.
