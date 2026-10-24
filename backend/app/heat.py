"""The NYC heat rule, outdoor temperature, heat readings and heat-check calls
(CONTRACT "### The heat rule").

Lane: heat. STUB — the lane replaces this with the real thing, keeping `router` and the
signatures of `summary` and `describe`, which missions.py and outbound.py call.
"""
from fastapi import APIRouter

from app.models import HeatSummary

router = APIRouter()


def summary(conn, mission_pk: int | None) -> HeatSummary:
    return HeatSummary()


def describe(summary: HeatSummary) -> str:
    return "No heat readings have been logged yet."
