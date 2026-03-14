| Test Case# | Date UTC | Result | Comments | Evidence File |
|---|---|---|---|---|
| FT-1 | 2026-03-14T04:59:39Z | PASS | Baseline run generated required artifacts and non-empty timeline output. | test_outputs/R4/evidence_FT-1.json |
| FT-2 | 2026-03-14T04:59:40Z | PASS | HESS KPI output included implemented cost and savings fields using a priced baseline reference. | test_outputs/R4/evidence_FT-2.json |
| FT-3 | 2026-03-14T04:59:41Z | PASS | Resolved config captured override values while the base config remained unchanged. | test_outputs/R4/evidence_FT-3.json |
| FT-4 | 2026-03-14T04:59:42Z | PASS | API-triggered simulator run returned ok and referenced generated artifacts. | test_outputs/R4/evidence_FT-4.json |
| UT-1 | 2026-03-14T04:59:42Z | PASS | Control workflow dependencies returned expected structures for default config and recent runs. | test_outputs/R4/evidence_UT-1.json |
| UT-2 | 2026-03-14T04:59:42Z | PASS | Invalid submission cases were rejected through objective API-side checks. | test_outputs/R4/evidence_UT-2.json |
| UT-3 | 2026-03-14T04:59:43Z | PASS | A valid edited run was created through the API path and exposed replay data with KPI summary. | test_outputs/R4/evidence_UT-3.json |
| CT-1 | 2026-03-14T04:59:43Z | PASS | Replay payload exposed the expected top-level contract and matched the requested run ID. | test_outputs/R4/evidence_CT-1.json |
| CT-2 | 2026-03-14T04:59:43Z | PASS | Snapshot payload exposed the expected live-data contract including KPI summary. | test_outputs/R4/evidence_CT-2.json |
| CT-3 | 2026-03-14T04:59:43Z | PASS | Default config and run-list endpoints matched the expected interface types. | test_outputs/R4/evidence_CT-3.json |
| PT-1 | 2026-03-14T04:59:44Z | PASS | API-triggered run completed successfully within the implemented 60 second timeout bound. | test_outputs/R4/evidence_PT-1.json |
