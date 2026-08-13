"""Coverage Curve Analysis backend package.

HISTORY (2026-06-05): this file used to contain a module-level Synapse query —
the original notebook code that generated the Historic.xlsx-style export. It
ran (or attempted Entra interactive auth) on EVERY `import backend.*`, slowing
each pipeline run and breaking background runs once the cached token expired.
That code now lives at `scripts/historic_export_query.py`. Keep this file
import-side-effect free.
"""
