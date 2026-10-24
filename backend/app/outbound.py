"""Outbound calls to the landlord, the caller agent's tools, tenant questions, commitments,
and the report-back call (CONTRACT "## Tenant advocate"). app/monitor.py (live relay) and
app/translate.py (subtitles) belong to the same lane.

Lane: outbound. STUB — the lane replaces this, keeping `router`.
"""
from fastapi import APIRouter

router = APIRouter()
