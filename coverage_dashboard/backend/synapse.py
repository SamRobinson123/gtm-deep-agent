"""Synapse connection helper.

Reads `SYNAPSE_CONN_STR` from .env (project root) and opens a pyodbc connection
that pandas.read_sql can consume.
"""

import os

import pyodbc
from dotenv import load_dotenv

load_dotenv()


def connect():
    """Open a pyodbc connection to Synapse."""
    conn_str = os.environ.get("SYNAPSE_CONN_STR")
    if not conn_str:
        raise RuntimeError(
            "SYNAPSE_CONN_STR is not set. Copy .env.example to .env and fill it in."
        )
    return pyodbc.connect(conn_str)
