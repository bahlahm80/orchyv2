# AlexJunior Job Helper v1

Local manager/worker system for LinkedIn job discovery, resume-based matching, and human-gated applications. Allows users to focus more on the hiring process rather than searching and submitting application process which is very time consuming.  Job Helper Agent will apply if the current in progresss application is below 5 and no offers has been made. It will apply up to 20 in progress or depends on how many open roles are available. 

## What it does

- **UI** at `http://127.0.0.1:8000`: Job listings, application status, approve/disapprove, manager chat.
- **Manager agent**: ticks workers, keeps the dashboard current, answers chat with live state + resume context.
- **Search worker**: pulls LinkedIn public job cards (guest HTML). If LinkedIn blocks the request, it falls back to sample listings so the loop still runs.
- **Application worker**: when fewer than **5** applications are in progress (`applied` / `screening` / `interviewing`), it queues matched jobs for approval. Approve → record as applied and open the listing. Disapprove → do nothing.

Easy Apply is **not** automated. After approval, Orchy opens the LinkedIn URL and tracks status locally. That is intentional: LinkedIn has no supported apply API for this, and credential scraping is out of scope.

## LLM: Gemini free tier

Default model is `gemini-3.1-flash` via

`POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`

Create a key in [Google AI Studio](https://aistudio.google.com/apikey). Resume text is sent as context on every match and chat call.

## Guardrails

- Resume file is required (`data/resume.md`).
- Conservative Gemini match JSON + heuristic overlap fallback.
- `MATCH_THRESHOLD` (default `0.72`) must pass before a job can be queued.
- Sales/unpaid/crypto-trading roles are instructed to fail matching.
- Apply never happens without an explicit UI (or MCP) approval.
- New approvals stop while 5 applications are already in progress.

## Run locally

```bash
cd jobhelper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# paste GEMINI_API_KEY into .env
# replace data/resume.md with your resume
python -m jobhelper.main
```

Open `http://127.0.0.1:8000`.

Useful env:

| Variable | Meaning |
| --- | --- |
| `JOB_SOURCE=auto` | LinkedIn first, sample fallback |
| `JOB_SOURCE=sample` | Offline demo listings only |
| `JOB_SOURCE=linkedin` | LinkedIn only (fails if blocked) |
| `LINKEDIN_KEYWORDS` | Search query |
| `MAX_IN_PROGRESS` | Approval gate (default 5) |

Default is linkedin.  May get rid sample as the project matures it is for testing purpose only. 

## MCP

Stdio server for other local agents:

```bash
python -m jobhelper.mcp_server
```

Cursor example (`mcp.json`):

```json
{
  "mcpServers": {
    "orchy-jobs": {
      "command": "python",
      "args": ["-m", "jobhelper.mcp_server"],
      "cwd": "."
    }
  }
}
```

Tools: `list_applications`, `pending_approvals`, `approve_application`, `reject_application`, `in_progress_applications`, `agent_log`.

## Layout

```
jobhelper/           manager, workers, Gemini client, LinkedIn source, FastAPI
web/             dashboard + chat
data/resume.md   matching context
tests/           heuristic guardrail check
```
