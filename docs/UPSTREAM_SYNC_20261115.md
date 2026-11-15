# Upstream backend integration

Only upxldxls/MOA2_test_BE is writable. MOAAPP/MOA_2ND_BE remains read-only.

Merged upstream main 04a68ac588aaa1ce66039a98705c47728144b094, preserving FastAPI learning/delivery and private-Blob state. Original optional gateway authentication, request quotas and EC2 deployment scripts are included as source; no original server was deployed or reconfigured.

## Changes

- Swagger trade-card input supports the latest UI's known virtual ETF codes and fractional quantities, retaining validation.
- Swagger includes learning subscription deletion as well as generation and delivery endpoints.
- Existing optional paid request quota classification includes card explanation and answer explanation.
- Fixed SQLite initialization on concurrent first-home quiz/history/preferences requests. Connections now close deterministically after success or failed CAS transactions, rather than relying on garbage collection.
- Financial observation remains 2026-11-04, synthetic S01-S20. No dataset or model selection was changed.

## Verification

142 backend tests passed on 2026-11-15, including the real LightGBM artifact train/validation/test separation test. Added a regression for three simultaneous first-home requests against empty local storage. Tests use isolated state and no live paid model or Discord destination. Frontend integration report is docs/UPSTREAM_SYNC_20261115.md in the test FE repository.

Deployment credentials and service/model destinations are unchanged. New main-source gateway settings remain optional unless explicitly configured. Do not execute imported EC2 deployment scripts against original infrastructure. Provider failures must remain visible as failures or explicitly labeled rule-based results.
