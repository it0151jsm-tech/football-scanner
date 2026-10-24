# Frozen PR6 research reference

These 19 source files are exact byte copies of PR6 commit
`df4b836b7bfade57f009f1ec115ae2905f76ae7d`, also used by PR18. Their SHA-256 values
remain in `../pr6-source-lock.json`. The nested readiness contract is the original
formula/UI contract from the same commit.

They are retained as research evidence so authorized operational scan and storage
fixes do not silently change the model/policy evaluated by an existing backtest.
They are **not** a Streamlit deployment entry point, a second running app, a
database, a dataset or evidence of predictive accuracy. No file here is imported
as an application; the offline adapter compiles only its existing whitelist of
pure functions. It checks all original source bytes before using the reference.

The recovery branch separately tests that the live app retains the 11 protected
formula hashes. Changing a frozen reference requires a new explicit baseline and
reviewed protocol; it must never rewrite a prior performance report.
