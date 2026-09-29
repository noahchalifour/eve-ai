---
name: build-a-widget
description: How to save a live widget (weather, a light, a speaker, a calendar strip, a health tile) with save_widget - when to use a preset, how to write a template, and what makes a widget good on a phone.
---
A widget is a small card the member keeps on their Widgets screen. It refreshes itself; you are never asked
again. So save one when the member wants to KEEP looking at or controlling something. A one-off question gets
a sentence.

## Prefer a preset

`weather`, `entity`, `glance`, `media` and `chart` cover most requests and look consistent. Use them unless
the member asked for a combination or layout no preset has. Look up entity ids with the home specialist first;
never guess one.

## Writing a template

Declare what you read under `sources` with short aliases, then build the tree from the normal catalog.

- Bind with `$data.<alias>.<field>`; `$data.widget.title` is the widget's own title.
- Show a list with a `list` whose properties are `repeat` (the list to walk), `limit`, and `empty` (what to
  say when there is nothing, e.g. "Nothing scheduled"). Inside it, use `$item.<key>`.
- An action is `actionId` plus `actionValue` = the entity it acts on, and that entity must be one of this
  widget's sources. If a card does ONE obvious thing (toggle this light), put the action on the card itself so
  the whole card is the button, and put no buttons inside it. If there are several controls, use buttons.
- Icons: info, alert, check, sun, moon, cloud, cloud-sun, rain, snow, storm, fog, wind, thermometer, droplets,
  lightbulb, lightbulb-off, power, lock, unlock, fan, plug, home, music, play, pause, skip-forward, skip-back,
  volume-down, volume-up, calendar, heart. Prefer the `icon` field a source gives you over picking one.

## What makes a good widget

- One glance: the headline value first, big; detail second. Five or fewer rows.
- Words over colour: "Locked", not a red dot.
- Button labels are verbs: "Next", "Louder", not "›".
- Title it by what it shows ("Kitchen light"), not by what it is ("Entity widget").
