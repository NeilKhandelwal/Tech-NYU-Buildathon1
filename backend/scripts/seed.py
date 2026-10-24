"""Insert 3 sample cases if the table is empty. Safe to re-run."""
from datetime import datetime, timezone

from sqlalchemy import func, insert, select

from app import codes, db

SAMPLES = [
    ("Maria Lopez", "5551234567", "missed_pickup", "Trash not collected on Elm St Tuesday"),
    ("James Carter", "5559876543", "pothole", "Large pothole on Main St near 4th Ave, hit it twice this week"),
    ("Priya Nair", "5555550123", "streetlight", "Streetlight out at Oak St and Pine Ave, dark corner at night"),
]

def seed() -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.connect() as conn:
        city = db.current_city_id()
        if conn.execute(select(func.count()).select_from(db.cases)
                        .where(db.cases.c.city_id == city)).scalar():
            print("cases table not empty, skipping seed")
            return
        for name, phone, issue_type, description in SAMPLES:
            code = codes.new_code(conn)
            cur = conn.execute(insert(db.cases).values(
                city_id=city, name=name, phone=phone, issue_type=issue_type,
                description=description, lookup_code=code, created_at=ts, updated_at=ts))
            pk = cur.inserted_primary_key[0]
            # seeded cases get a 'created' audit row too, so their History isn't empty
            conn.execute(insert(db.case_events).values(
                city_id=city, case_id=pk, field="created", old_value=None, new_value=db.case_id(pk),
                source="seed", ts=ts))
            # printed so a rehearsal can call in with a code that already exists
            print(f"{db.case_id(pk)} {code}")
        print(f"seeded {len(SAMPLES)} cases")


if __name__ == "__main__":
    db.init_db()
    seed()
