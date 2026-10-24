"""Reset the Heat Check demo queue: delete every case that isn't a seed (test calls, rehearsal
runs), then re-seed, so the projector shows only the four older cases before a run-through.

    fly ssh console -a heatcheck-nyu -C "python -m scripts.hc_reset"            # dry run
    fly ssh console -a heatcheck-nyu -C "python -m scripts.hc_reset --yes"      # delete

Seeds themselves are deleted and re-created too (--yes), so a rehearsal that changed a seed's
status starts clean.
"""
import asyncio
import sys

from sqlalchemy import delete, select

from app import hcdb
from scripts import hc_seed


def main() -> None:
    with hcdb.engine().connect() as conn:
        rows = conn.execute(select(hcdb.cases.c.id, hcdb.cases.c.case_number,
                                   hcdb.cases.c.resident_name)).all()
        for _, number, name in rows:
            print(f"  {number}  {name}")
        if "--yes" not in sys.argv:
            print(f"dry run: {len(rows)} case(s) would be deleted and the seeds re-created; pass --yes")
            return
        ids = [r.id for r in rows]
        # events first: the delete must work even where the FK has no ON DELETE CASCADE
        conn.execute(delete(hcdb.events).where(hcdb.events.c.case_id.in_(ids)))
        conn.execute(delete(hcdb.cases).where(hcdb.cases.c.id.in_(ids)))
        conn.commit()
        print(f"deleted {len(ids)} case(s)")
    asyncio.run(hc_seed.main())


if __name__ == "__main__":
    main()
