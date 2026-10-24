"""Seed CityLine with older demo cases so the queue looks real (DASHBOARD.md "Demo data").

    HC_DATABASE_URL=<supabase session pooler string> uv run python -m scripts.hc_seed

Idempotent by case_number: a case already there is skipped, so re-running is safe. Content is
placeholder for Valaya to edit. Phones are 555 numbers so a live call never matches a seed.
Building history comes from the same lookup live cases use (2800 Creston is seeded in
app/seed_buildings.json; other addresses query NYC Open Data and may come back not found).
"""
import asyncio
from datetime import timedelta

from app import building, hcdb, llm

SEEDS = [
    {"case_number": "HC-104233", "hours_ago": 26, "status": "called_back",
     "resident_name": "Rosa Martínez", "resident_phone": "5550101001", "language": "es",
     "language_label": "Spanish", "address": "2800 Creston Avenue, Bronx", "unit": "6C",
     "borough": "BRONX", "days_without_heat": 4, "indoor_f": 59, "outdoor_f": 38,
     "is_violation": True, "rule_applied": "Day rule: outside 38F < 55F, inside 59F < 68F",
     "summary_en": "Resident in 6C reports no heat for four days; 59F inside. Two elderly residents in the unit.",
     "transcript_original": "Agent: Hola, soy CityLine, un asistente de IA.\nResident: No tenemos calefacción desde hace cuatro días. Adentro hace 59 grados.",
     "transcript_en": "Agent: Hello, this is CityLine, an AI assistant.\nResident: We haven't had heat for four days. It's 59 degrees inside.",
     "suggested_action": "schedule_inspection", "caseworker_action": "schedule_inspection",
     "caseworker_message_en": "An inspector will visit your apartment on Thursday between 9am and 1pm. Will you be home?",
     "resident_response": "confirmed", "resident_response_note_en": "Yes, I will be home Thursday morning.",
     "events": [("intake_call", "intake_agent", None, None),
                ("submitted", "resident", None, None),
                ("caseworker_action", "caseworker", None, "An inspector will visit your apartment on Thursday between 9am and 1pm. Will you be home?"),
                ("resident_response", "resident", "Sí, estaré en casa el jueves por la mañana.", "Yes, I will be home Thursday morning.")]},
    {"case_number": "HC-218640", "hours_ago": 5, "status": "in_review",
     "resident_name": "Wei Chen", "resident_phone": "5550101002", "language": "zh",
     "language_label": "Chinese", "address": "41-25 Kissena Boulevard, Queens", "unit": "3F",
     "borough": "QUEENS", "days_without_heat": 2, "indoor_f": 63, "outdoor_f": 44,
     "is_violation": True, "rule_applied": "Day rule: outside 44F < 55F, inside 63F < 68F",
     "summary_en": "Heat has been weak for two days; radiators lukewarm, 63F inside.",
     "transcript_original": "Agent: 您好，我是 CityLine 人工智能助手。\nResident: 暖气两天都不太热，屋里只有63度。",
     "transcript_en": "Agent: Hello, this is the CityLine AI assistant.\nResident: The heat hasn't been very warm for two days; it's only 63 degrees inside.",
     "suggested_action": "schedule_inspection",
     "events": [("intake_call", "intake_agent", None, None), ("submitted", "resident", None, None)]},
    {"case_number": "HC-330517", "hours_ago": 3, "status": "callback_requested",
     "resident_name": "Farhana Begum", "resident_phone": "5550101003", "language": "bn",
     "language_label": "Bengali", "address": "1520 Sedgwick Avenue, Bronx", "unit": "2A",
     "borough": "BRONX", "days_without_heat": 5, "indoor_f": 55, "outdoor_f": 36,
     "is_violation": True, "rule_applied": "Night rule (10pm-6am): inside 55F < 62F",
     "summary_en": "No heat for five days; 55F overnight with an infant in the apartment.",
     "transcript_original": "Agent: নমস্কার, আমি CityLine, একটি এআই সহকারী।\nResident: পাঁচ দিন ধরে হিটিং নেই। রাতে ঘরে ৫৫ ডিগ্রি।",
     "transcript_en": "Agent: Hello, I am CityLine, an AI assistant.\nResident: There has been no heating for five days. At night it's 55 degrees in the room.",
     "suggested_action": "emergency_repair", "caseworker_action": "emergency_repair",
     "caseworker_message_en": "Your case has been sent for emergency repair. A contractor may contact you. Is this the best number to reach you?",
     "events": [("intake_call", "intake_agent", None, None), ("submitted", "resident", None, None),
                ("caseworker_action", "caseworker", None, "Your case has been sent for emergency repair. A contractor may contact you. Is this the best number to reach you?")]},
    {"case_number": "HC-447902", "hours_ago": 50, "status": "resolved",
     "resident_name": "James Wilson", "resident_phone": "5550101004", "language": "en",
     "language_label": "English", "address": "340 West 145th Street, Manhattan", "unit": "5D",
     "borough": "MANHATTAN", "days_without_heat": 1, "indoor_f": 64, "outdoor_f": 47,
     "is_violation": True, "rule_applied": "Day rule: outside 47F < 55F, inside 64F < 68F",
     "summary_en": "Boiler outage for one day; landlord reported it fixed and the resident confirmed.",
     "transcript_original": "Agent: Hello, this is CityLine, an AI assistant.\nResident: The boiler went out yesterday, it's 64 in here.",
     "transcript_en": "Agent: Hello, this is CityLine, an AI assistant.\nResident: The boiler went out yesterday, it's 64 in here.",
     "suggested_action": "schedule_inspection", "caseworker_action": "resolved",
     "caseworker_message_en": "Your landlord reports the heat is restored. Is your apartment warm now?",
     "resident_response": "confirmed", "resident_response_note_en": "Yes, it's warm again.",
     "events": [("intake_call", "intake_agent", None, None), ("submitted", "resident", None, None),
                ("caseworker_action", "caseworker", None, "Your landlord reports the heat is restored. Is your apartment warm now?"),
                ("resident_response", "resident", "Yes, it's warm again.", "Yes, it's warm again.")]},
]


async def seed_one(spec: dict) -> str:
    spec = dict(spec)
    number, hours, events = spec.pop("case_number"), spec.pop("hours_ago"), spec.pop("events")
    if hcdb.get_case_by_number(number):
        return f"{number} exists, skipped"
    found = await building.building_lookup(spec["address"], spec.get("borough"))
    case = hcdb.insert_case(**spec, building=found, priority=llm.priority(spec, found))
    at = hcdb.now() - timedelta(hours=hours)
    # insert_case picks its own number and timestamps; seeds need fixed ones
    hcdb.update_case(case["id"], case_number=number, created_at=at)
    for type_, actor, original, english in events:
        hcdb.add_event(case["id"], type_, actor, text_original=original, text_en=english)
    return f"{number} seeded ({found['source']}, priority {llm.priority(spec, found)})"


async def main() -> None:
    for spec in SEEDS:
        print(await seed_one(spec))


if __name__ == "__main__":
    asyncio.run(main())
