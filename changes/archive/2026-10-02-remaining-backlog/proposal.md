# Remaining backlog

Date: 2026-10-02

## Why

The completed October hardening phase left four actionable investigations/polish
items. The user deferred hands-on acceptance and explicitly requested continuation.

## Scope

ENG-89 fixed profile bakeoff; ENG-90 bounded audio compatibility evidence;
ENG-91 preview tail indicator; ENG-92 image CPU transfer reduction.
Evidence-triggered follow-through: ENG-94 wrapped timeout classification and
ENG-95 one bounded held-out profile decision, including its source-package repair.

## Boundaries and risks

Preserve public node/workflow contracts, portable profile snapshots, full output
history, 64 KiB preview bounds and interruption behavior. No new runtime dependency,
implicit model download, public audio surface or release promotion. Negative
investigation results are useful evidence and must not become unsupported claims.

## Rollback

Focused git reverts on dev; pre-phase candidate a466b6b. Stop only owned test
processes. No stable release or user workflow/state changes.
