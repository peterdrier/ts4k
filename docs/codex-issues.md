# Codex Architecture and Code Review

Date: February 24, 2026

## Findings (Highest Severity First)

1. **High: state corruption risk under concurrent use.** Most state modules do load-mutate-write JSON with no file locking or atomic rename, so CLI + MCP + background preload can race and corrupt state. This is a production blocker for reliability.  
   References: [src/ts4k/state/sources.py:60](/H:/source/ts4k/src/ts4k/state/sources.py), [src/ts4k/state/contacts.py:45](/H:/source/ts4k/src/ts4k/state/contacts.py), [src/ts4k/state/filters.py:67](/H:/source/ts4k/src/ts4k/state/filters.py), [src/ts4k/state/cache.py:52](/H:/source/ts4k/src/ts4k/state/cache.py), [src/ts4k/state/batch.py:37](/H:/source/ts4k/src/ts4k/state/batch.py), [src/ts4k/state/stats.py:32](/H:/source/ts4k/src/ts4k/state/stats.py), [src/ts4k/state/watermarks.py:26](/H:/source/ts4k/src/ts4k/state/watermarks.py)

2. **High: `preload --cancel` does not actually cancel work.** It marks the job failed but does not stop the spawned process, so work continues in the background while metadata says otherwise.  
   Reference: [src/ts4k/commands.py:989](/H:/source/ts4k/src/ts4k/commands.py)

3. **High: deploy plan and repo reality are out of sync.** Docs describe a bundled Docker/s6/connectors architecture that is not present in this repo structure. This is a major operability/release risk and signals plan drift.  
   Reference: [docs/deploy-plan.md:10](/H:/source/ts4k/docs/deploy-plan.md)

4. **High: secrets handling is unresolved in deployment design.** `.env`/mounted secrets/wizard is still undecided while connector creds are central; this is a security gap, not a detail.  
   Reference: [docs/deploy-plan.md:163](/H:/source/ts4k/docs/deploy-plan.md)

5. **Medium-High: cache update path is computationally wasteful (“long way around”).** `store_message`/`store_header` pattern rewrites full `index.json` per message during preload/list flows, creating O(n^2)-ish disk churn and amplifying race conditions.  
   References: [src/ts4k/commands.py:275](/H:/source/ts4k/src/ts4k/commands.py), [src/ts4k/commands.py:772](/H:/source/ts4k/src/ts4k/commands.py), [src/ts4k/state/cache.py:83](/H:/source/ts4k/src/ts4k/state/cache.py)

6. **Medium: MCP lifecycle ownership is still an open architectural question.** Without clear ownership, restart/failure semantics remain ambiguous.  
   Reference: [docs/rip/phase-4-architecture.md:240](/H:/source/ts4k/docs/rip/phase-4-architecture.md)

7. **Medium: observability is too thin for production ops.** Current docs lean on status outputs; there’s no concrete logging/metrics/alerting/tracing plan.  
   References: [docs/plan-v1.md:106](/H:/source/ts4k/docs/plan-v1.md), [docs/rip/phase-4-architecture.md:199](/H:/source/ts4k/docs/rip/phase-4-architecture.md)

8. **Medium: disk space check can hard-fail on inaccessible paths.** `disk_usage` is called without robust error handling, which can throw raw exceptions in real environments.  
   Reference: [src/ts4k/state/cache.py:297](/H:/source/ts4k/src/ts4k/state/cache.py)

9. **Medium: testing strategy is good locally, but CI enforcement is missing.** No workflow-based gating was found; token-efficiency tests are conditional on `ANTHROPIC_API_KEY`, so key budget guardrails are optional.  
   References: [tests/test_token_efficiency.py:1](/H:/source/ts4k/tests/test_token_efficiency.py), [pyproject.toml:35](/H:/source/ts4k/pyproject.toml)

10. **Low: docs consistency issue on licensing.** README says TBD while package metadata says MIT.  
   References: [README.md:48](/H:/source/ts4k/README.md), [pyproject.toml:6](/H:/source/ts4k/pyproject.toml)

## Illities Scorecard (Frank)

- **Reliability:** C- (concurrency and cancel semantics are serious faults)
- **Security:** C- (credential/secrets model unresolved in deployment path)
- **Performance:** C (avoidable cache rewrite overhead)
- **Scalability:** C- (JSON-file state architecture will struggle as throughput/concurrency rises)
- **Maintainability:** B- (good adapter separation, but state layer debt is mounting)
- **Testability:** B (good unit depth), **CI confidence:** D (no automated gating)
- **Operability/Observability:** C- (status exists, production telemetry plan missing)
- **Architecture coherence:** C (core idea is solid; deployment and runtime ownership are not yet tightly closed)

## What’s Good

1. Adapter abstraction is clean and extensible.  
   Reference: [src/ts4k/adapters/base.py:11](/H:/source/ts4k/src/ts4k/adapters/base.py)
2. Normalization pipeline is thoughtfully implemented and well-covered by tests.  
   References: [src/ts4k/core/normalize.py:1](/H:/source/ts4k/src/ts4k/core/normalize.py), [tests/test_pipeline.py:353](/H:/source/ts4k/tests/test_pipeline.py)

## Test Run Note

I executed `pytest -q` in this environment: **250 passed, 179 errors, 4 skipped, 3 deselected**. The errors were dominated by `PermissionError` around temp directory access (`tmp_path` setup), so this run is not a clean indicator of true functional health.

## Bottom Line

The product direction and core adapter/normalization architecture are good, but this is not production-ready yet. The biggest “long-way-around” debt is the JSON state and cache write strategy under concurrency, plus deployment/docs drift and unresolved secrets/lifecycle ownership. Fix those first; everything else is secondary.
