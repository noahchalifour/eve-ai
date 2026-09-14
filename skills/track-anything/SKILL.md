---
name: track-anything
description: How to record things a member wants kept over time - workouts, reading, chores, habits, moods - and how to keep them consistent enough to chart later.
---
Use `record_append` for anything the member wants kept and looked at later.
There is no per-domain tool and there never will be: a workout set, a book
finished and a chore done are all the same call with a different
`collection`.

## Choosing a collection name

A stable dotted name, singular, for the kind of entry: `workout.set`,
`reading.session`, `chore.done`, `mood.check`. Reuse the exact name every
time. `workout.set` and `workouts` are two different collections, and a chart
reading one will silently miss everything in the other.

Before inventing a name, use `record_query` on the name you are about to use.
If entries come back, you already have the right name. If the member has
tracked something similar before, reuse that collection rather than starting
a parallel one.

## Keeping the payload chartable

`record_append` tells you which fields the collection has used so far. Match
them. A chart sums or counts a numeric field by name, so `weight` in one
entry and `load` in the next produces a chart that undercounts without
reporting anything wrong.

Put numbers in as numbers, not strings. One entry is one event: three sets of
an exercise is three appends, not one entry with a list inside it, because a
list cannot be aggregated over a date range.

Set `occurred_at` when the member is recording something from earlier ("I ran
yesterday"). Omit it for now.

## When to record at all

Record when the member is logging something that accumulates - a set, a
session, a completion. Answer in prose when they are asking a question. A
recorded entry nobody will ever chart is noise in a collection somebody else
will chart later.

If several entries arrive in one sentence ("I did 3x5 at 225"), append each
one, and say how many you recorded.