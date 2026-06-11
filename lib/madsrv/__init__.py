"""mad-backend daemon command modules (headless — NEVER import tkinter here).

Each *_cmds module registers methods on lib.madsrv.rpc; mad-backend.py imports
them for side effects. Protocol spec: deck-docs/mad-backend-protocol.md.
"""
