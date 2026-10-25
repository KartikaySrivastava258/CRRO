"""Durable payment-recovery ledger: cases, decisions, approvals, action
attempts, provider events, reconciliation and append-only audit.

Framework-free (plain DB-API) so the same code runs on SQLite (tests, offline
demo) and PostgreSQL (docker-compose / CI). See docs/ARCHITECTURE.md.
"""
