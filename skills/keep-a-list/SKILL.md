---
name: keep-a-list
description: How to keep lists - groceries, packing, to-dos, chores - with list_add, list_show, list_remove and list_clear, and when a list is the wrong tool.
---
A list is a set of things to buy, do or remember with no time attached. Use
the list tools for it, not `record_append`: a list item is meant to go away
once it is done, and a recorded entry is meant to be kept and charted.

## Which list

Name lists the way the family says them, short and plural where natural:
`groceries`, `packing`, `todo`, `hardware-store`, `gift-ideas`. Reuse the
same name every time. Use `list_show` with `list_name="*"` to see which lists
already exist before starting a new one, so "shopping" does not become a
second groceries list.

## Whose list

`scope="household"` is shared by the whole family: groceries, chores, the
hardware store run, anything more than one person adds to or shops from.
Writing to it needs `memory.write_shared`; anyone can read it.

`scope="me"` (the default) is the member's own: their to-dos, gift ideas,
packing for their trip. When it is not obvious, groceries and chores are
household; everything else is personal. Ask if it genuinely matters.

## Adding and removing

Add items as the member said them ("2% milk", "AA batteries"). Repeats are
recognised regardless of case, so adding something already on the list is
safe. When they say they bought or did something, `list_remove` it. Only use
`list_clear` when they clearly ask to clear or start over.

## When it is not a list

- Something to do at a time ("call the dentist at 2") is a reminder:
  `set_reminder`.
- Something to keep doing on a schedule is a routine: `schedule_routine`.
- Something to track and chart over time (workouts, reading) is
  `record_append` - see the track-anything skill.
