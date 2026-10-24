"""The demo reset: a rehearsal's leftovers must never reach the projector."""
import asyncio
import sys

from app import hcdb
from scripts import hc_reset, hc_seed


def test_reset_leaves_exactly_the_seeds(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_DATABASE_URL", f"sqlite:///{tmp_path / 'r.db'}")
    hcdb.reset_engine()
    hcdb.metadata.create_all(hcdb.engine())

    async def no_lookup(address, borough=None):
        return {"found": False, "source": "seed"}

    monkeypatch.setattr(hc_seed.building, "building_lookup", no_lookup)
    asyncio.run(hc_seed.main())
    test = hcdb.insert_case(resident_name="TEST", resident_phone="5555550100", language="en",
                            address="1 Main St")
    hcdb.add_event(test["id"], "intake_call", "intake_agent")
    hcdb.add_event(None, "call_started", "intake_agent", payload={"conversation_id": "c1"})
    monkeypatch.setattr(sys, "argv", ["hc_reset"])
    hc_reset.main()  # dry run deletes nothing
    assert len(hcdb.list_cases()) == len(hc_seed.SEEDS) + 1
    monkeypatch.setattr(sys, "argv", ["hc_reset", "--yes"])
    hc_reset.main()
    numbers = sorted(c["case_number"] for c in hcdb.list_cases())
    assert numbers == sorted(s["case_number"] for s in hc_seed.SEEDS)
    assert hcdb.list_events(test["id"]) == []
    assert hcdb.conversation_events("c1", ("call_started",)) == []  # the orphan call row too
    hcdb.reset_engine()
