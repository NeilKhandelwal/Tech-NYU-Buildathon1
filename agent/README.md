# Voice agent (LiveKit Agents, Python)

City services phone line: greets the caller, collects name / phone / issue type / description, files a case via the backend (`BACKEND_URL`, default `http://localhost:8000`) and reads the case ID back. Can also look up a case by phone and add a note.

Scaffolded from [agent-starter-python](https://github.com/livekit-examples/agent-starter-python); models run through LiveKit Inference (no separate provider keys).

```bash
cp .env.example .env   # fill in LiveKit credentials
uv sync
uv run python src/agent.py download-files   # once: turn-detector / VAD models
uv run python src/agent.py console          # talk to it from the terminal
```
