# Proposal: feature-frontier

Date: 2026-10-02

> Do not put secrets in this folder. Use 1Password.

## Why

The completed feature review selected seven useful gaps. The maintainer now
authorizes their implementation, end-to-end verification and Linear delivery.

## Scope

ENG-99 result/graph interop; ENG-100 router presets; ENG-101 messages; ENG-102
request budgets; ENG-103 per-image captions; ENG-104 profile authoring; ENG-105
experimental short-clip ASR. The source report and issue acceptance criteria are
the bounded contracts, with implementation decisions recorded in design.md.

## Non-goals

No parked frontier, new runtime/dependency/model download, bundled profile,
master promotion, release tagging or publication. ENG-88 remains independent.

## Risks

Saved positional widget compatibility, stale UI actions, exact model/epoch
ownership, group cleanup, counting cancellation, INI acquisition semantics and
audio bounds require focused proof. Unit success is not native acceptance.

## Rollback

Focused feature commits can be reverted on dev. Baseline: 3945082. Keep existing
models and runtimes; disposable validation user/output/preset files are owned by
this phase and must not replace user data.
