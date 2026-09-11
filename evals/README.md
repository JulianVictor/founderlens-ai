# Evaluation

Retrieval and answer-quality evaluation for the FounderLens pipeline.

- `datasets/` — versioned question sets with their expected supporting evidence.
- Harness code lives in the backend at `backend/app/ai/evaluation/` so it shares
  the pipeline's own types; this directory holds the data and the reports.

Planned metrics: retrieval recall@k and MRR, citation precision (does every
claim map to retrieved evidence?), and the insufficient-evidence abstention rate.
