-- Non-versioned additive index: schema28 keeps owning the validated marker chain.
-- Existing model-call rows remain immutable.
CREATE INDEX IF NOT EXISTS ix_model_calls_run_task_key ON model_calls(run_id,task_key);
