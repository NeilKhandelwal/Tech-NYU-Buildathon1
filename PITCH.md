# PITCH.md — story, stats, demo, Q&A

Owner: Valaya. Total time: 2 minutes, plus Q&A.

## One-liner
"311 that speaks your language, and a caseworker console that talks back."

## Problem (use these exact sourced numbers)
- Heat and hot water were NYC's #1 311 complaint in Feb 2026: about 59K reports, up from about 41K a year earlier. That winter, nearly 20 people died from cold outdoors and 7 in private homes. (PIX11)
- 234,474 heat and hot water complaints from Dec 1 2025 to Feb 3 2026, up 11% year over year. One Bronx building logged 1,223. (RentHop)
- HPD said it resolved 99% of heat issues, then admitted many complaints were closed without an inspection. Over 450K serious (Class C) violations are open, and more than 70% have been open for over a year. (NY Focus)
- The people hit hardest are elderly and non-English-speaking tenants. They are the least able to navigate 311, follow up, or understand a callback in English.
- The law is clear: from 6am to 10pm the inside must be at least 68°F when it's below 55°F outside, and at least 62°F at night. Fines run $250–$1,000 per day. (NYC HPD)

## Solution (one slide, two sides)
- **Resident side:**
  - Call one number and speak any language.
  - The AI takes the complaint, checks it against the heat law live, reads it back, and only submits with your yes.
  - You get a text in your language.
- **City side:**
  - The case lands on a caseworker console already translated, checked for a violation, and paired with the building's history.
  - The caseworker decides and clicks once.
  - The resident gets a call back in their own language.
- **Humans in the loop on both sides:** the AI never acts without the resident's yes or the caseworker's click.

## Demo script (2:00)
| Time | Who | What |
|---|---|---|
| 0:00–0:20 | Valaya | Hook: the stats above, in two sentences. "Now imagine you're 78, you speak Hindi, and your apartment is 61 degrees." |
| 0:20–0:55 | Romir (resident, phone on speaker) | Calls in Hindi. The agent answers in Hindi, explains it's likely a violation, reads it back, Romir says yes. The case pops into the console live. |
| 0:55–1:25 | Valaya (caseworker) | Opens the case: English translation, violation card, building history (repeat offender). Picks "Schedule inspection" and clicks "Call back in Hindi." |
| 1:25–1:45 | Romir | The phone rings. The agent speaks Hindi with the inspection time; Romir says he'll be home. The console shows "confirmed" and the Hindi text arrives. |
| 1:45–2:00 | Valaya | Close: "Same pipeline works for hot water, mold, lockouts, and any city agency. Every resident heard in their language, every action approved by a human." |

If anything fails live, say "here's the recording" and switch to the backup video. Don't debug on stage.

## Slides (3)
1. **Problem:** the 3 stats and one photo-free visual (big numbers).
2. **How it works:** the two-sided flow diagram (resident → agent → console → caseworker → callback), with the two human checkpoints highlighted.
3. **What's next:** more complaint types, real agency integrations, landlord outreach, whole-building filing. Tech: ElevenLabs Agents, Twilio, Supabase, Next.js, Grok.

## Likely judge questions
- **"How is this different from EffiGov?"**
  - EffiGov sells AI agents that answer for the city.
  - We bridge both sides: the resident's language goes all the way through, to the caseworker and back.
  - Every action is human-approved on both ends.
- **"What if the AI mistranslates?"**
  - The caseworker always sees the original transcript next to the English.
  - The callback only says what the caseworker approved.
  - The resident confirms the details before anything is submitted.
- **"Privacy and immigration fears?"**
  - We only ask for name, address and phone.
  - The agent never asks about immigration status and says so when asked.
- **"Why voice, not an app?"**
  - Elderly and ESL residents call; they don't download apps.
  - Voice in your own language is the product.
- **"Is this connected to real 311?"** Not yet. This is a demo console, and integration is next. We'd pilot with a council member's office or a tenant organization first.
- **"Business model?"** City or agency SaaS per resident served, plus tenant organizations and nonprofits as early partners.

## MLH submission (3:30)
- Name: Heat Check
- Description: two-sided multilingual voice line for NYC no-heat complaints (copy the one-liner plus the solution bullets)
- Technologies: ElevenLabs Agents, Twilio, Next.js, Supabase, Vercel, NYC Open Data, Open-Meteo, Grok (if used)
- Links: repo, Vercel URL, backup video
- Tracks:
  - Best Use of ElevenLabs
  - SpaceX/Grok, if Grok was used
  - Snowflake, only if the heat map shipped

## Sources
- https://pix11.com/news/local-news/heat-hot-water-complaints-plague-nyc-amid-historic-winter/amp/
- https://www.renthop.com/research/heat-complaints-2026/
- https://nysfocus.com/2026/06/22/nyc-hpd-housing-violations-unresolved-complaints
- https://www.nyc.gov/site/hpd/services-and-information/heat-and-hot-water-information.page
