-- Packets carry verified example sources chosen by the build host (host.py sync --examples N).
-- Run once on an existing database BEFORE deploying the worker that reads it:
--   npx wrangler d1 execute iblis --remote --file worker/migrations/0002_function_context.sql
ALTER TABLE functions ADD COLUMN context TEXT;
