---
name: build-a-ui
description: How to build a dynamic UI surface on screen for a member - a form, a tracker, a checklist, an input, a summary card, a comparison - and the component catalog show_surface accepts.
---
Call `show_surface(components)` with a tree of typed components. Search for
this skill and gather any data you need in the SAME round, then build.

## When a surface is the right answer

Build one when the member wants to enter, track, compare, or choose
something - a workout to log, a checklist to tick, options side by side.
Answer in prose when the answer is a sentence. A card that restates one fact
is worse than saying it.

Keep it phone-sized: one card, a handful of rows. Nobody scrolls a form on a
phone. If it needs more than about eight components, you are building the
wrong thing - ask a question instead.

## How state works

Inputs write to the surface's local state under the `stateKey` you give them.
Nothing you write reaches Eve until the member taps a button whose `actionId`
is `surface.submit` - that hands the whole local state back to you as a new
turn, and you decide what to do with it (remember it, call a tool, answer).

A button with `setState` instead changes local state on the spot with no
round trip. Values are literal - there is no arithmetic, so a counter cannot
increment itself. Let the member type the number.

Every component needs a unique `id` and a `type`. Layout components take
`children`.

## The catalog

- `column`: no properties
- `row`: no properties
- `list`: no properties
- `divider`: no properties
- `card`: title
- `grid`: columns
- `text`: text
- `icon`: name
- `badge`: label
- `expandable`: expanded, label
- `textField`: label, stateKey
- `numberField`: label, stateKey
- `button`: actionId, actionValue, label, setState
- `segmentedSelection`: actionId, actionValue, options, selected, setState

`grid.columns` is 1-6. `expandable.expanded` is a boolean. A `button` must
have exactly one of `actionId` or `setState` - both is two meanings for one
tap, neither is a control that silently ignores them. The only `actionId` is
`surface.submit`.

## A worked example

A workout tracker: a card titled "Workout", a `textField` for the exercise, a
`numberField` each for reps and weight, and a Save button.

    [{"id": "root", "type": "card", "properties": {"title": "Workout"},
      "children": [
        {"id": "ex", "type": "textField",
         "properties": {"label": "Exercise", "stateKey": "exercise"}},
        {"id": "reps", "type": "numberField",
         "properties": {"label": "Reps", "stateKey": "reps"}},
        {"id": "wt", "type": "numberField",
         "properties": {"label": "Weight", "stateKey": "weight"}},
        {"id": "save", "type": "button",
         "properties": {"label": "Save", "actionId": "surface.submit"}}
      ]}]

## If it comes back rejected

The tool answers with a diagnostic code and the legal properties for the
types you used. Fix the tree and call it again - you do not need to search
for this skill a second time. `component-schema` means a property is not
declared for that type; `component-type` means the type does not exist.
