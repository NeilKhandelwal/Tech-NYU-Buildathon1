"""Missions, the intake agent's tools, the dossier, escalations (CONTRACT "## Tenant advocate").

Lane: missions. STUB — the lane replaces everything below the router with the real thing,
keeping `router` and the signature of `build_dossier`, which outbound.py calls.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app import db, heat
from app.models import Dossier, Mission

router = APIRouter()


def fetch_mission(conn, mission_pk: int | None) -> Mission:
    row = conn.execute(select(db.missions).where(
        db.missions.c.id == mission_pk, db.missions.c.city_id == db.current_city_id())).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="mission not found")
    d = dict(row._mapping)
    d.pop("city_id")
    return Mission(**{**d, "id": db.mission_id(d["id"])})


def build_dossier(conn, mission_pk: int | None) -> Dossier:
    return Dossier(mission=fetch_mission(conn, mission_pk), heat=heat.summary(conn, mission_pk))
