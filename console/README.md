# Heat Check Caseworker Console (demo)

The government side of Heat Check: the caseworker queue, case detail, and the
"Call back in the resident's language" action. Spec: `DASHBOARD.md` (on `main`).

- **Reads** come straight from Supabase (`cases`, `events`), live over Realtime.
- **Writes** go to the Heat Check API on Fly: `POST /api/cases/{id}/action`, then
  `POST /api/cases/{id}/callback`.
- No login: it's a demo console for a projector.

```
cp .env.example .env.local   # fill in the publishable key
npm install
npm run dev                  # http://localhost:3000
```

Vercel: import the repo with **Root Directory = `console`** and set the three
`NEXT_PUBLIC_*` variables from `.env.example`.
