-- Extend the ingestion pipeline with the two stages that make a document
-- retrievable: EMBED produces vectors, INDEX writes nodes and vectors into
-- Neo4j.
--
-- Appended after 'persist' so the enum's declaration order still matches
-- execution order, which is what IngestionStage.order relies on.
--
-- Postgres allows ALTER TYPE ... ADD VALUE inside a transaction since 12, but
-- the new value cannot be used in that same transaction. Nothing here uses
-- them, so this is safe as a single migration; the first row carrying one is
-- written by the application afterwards.

alter type public.ingestion_stage add value if not exists 'embed' after 'persist';
alter type public.ingestion_stage add value if not exists 'index' after 'embed';
