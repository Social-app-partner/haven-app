# Haven Python Simulator v0.3 — Ready for a $100 OpenAI API experiment

**Haven repository:** https://github.com/Social-app-partner/haven-app  
**Operating system:** Windows 10 supported; Python 3.10+, no GPU needed to generate data.  
**Status:** Tested with **fake API transports**, real OpenAI API calls **NOT performed**. The exact model ID, pricing, account API permissions, rate limits and billing must be verified in your own OpenAI account.

This is a modification of Haven Simulator v0.2. It generates fictional social-platform sessions with **hugs, replies, saves, hides and reports** (no like counts, follow counts or watch-time bait). It can rank feeds using the current Haven backend's `POST /api/feed`, or use a standalone approximation. The optional paid mode uses OpenAI only to generate **fictional action decisions** per feed session, **not** to diagnose anyone or train a model on OpenAI servers.

**Synthetic data is NOT real-world evidence of psychological safety, clinical effectiveness or recommendation quality.** Keep independent moderation, real-user consent and expert-led safety validation. No real Haven users' data, private journal notes, personal identities, scraped content or diagnoses are transmitted to OpenAI by this simulator.

## A. Install once (Windows PowerShell)

1. Download/extract this ZIP into a convenient folder, open **PowerShell** in the extracted simulator folder (`haven-simulator`).
2. Install Python 3.10+ if not already installed.
3. Check everything runs offline first:

```powershell
py -3 -m unittest discover -s tests -v
py -3 -m haven_sim simulate --users 10 --posts 40 --sessions-per-user 2 --output output\free_smoke
```

For later paid mode, install the API SDK **now** (installation itself does not call the OpenAI API):

```powershell
py -3 -m pip install -r requirements-openai.txt
```

## B. Preview the $100 experiment WITHOUT spending

```powershell
py -3 -m haven_sim plan-openai `
  --users 1000 --posts 250 --sessions-per-user 10 --feed-size 8 `
  --max-calls 10000 --budget-usd 100 --reserve-usd 5 `
  --price-input 0.10 --price-output 0.50
```

**The $0.10/$0.50 rates above are ONLY the example values from your earlier pricing screenshot**, per 1 million input/output tokens. They may no longer match the model or provider rates. **Verify the exact API model identifier and current standard/cached pricing** in your OpenAI dashboard *before* launching. The planner uses rough token estimates and a deliberately padded reservation; it does **not** call OpenAI, and you do not need an API key for it.

## C. When you deliberately want to start spending

Create a **dedicated API project** in https://platform.openai.com/ and configure usage alerts and a project spending budget/limit where available. **The provider's budget controls may be soft alerts rather than a guaranteed hard cap.** Check your dashboard usage periodically. Never share your API key, paste it into chat, or commit it to GitHub. A ChatGPT subscription does not include API tokens.

In the same PowerShell window, supply your real API credentials:

```powershell
$env:OPENAI_API_KEY = "PASTE_YOUR_KEY_LOCALLY_IN_POWERSHELL"
$env:OPENAI_MODEL = "YOUR_EXACT_AVAILABLE_API_MODEL_ID"
```

Start with a **small pilot**. Update `--price-input` and `--price-output` using the current USD-per-million rates for that exact model:

```powershell
py -3 -m haven_sim openai-run --execute `
  --model $env:OPENAI_MODEL --price-input 0.10 --price-output 0.50 `
  --users 5 --posts 40 --sessions-per-user 2 --feed-size 8 `
  --max-calls 10 --budget-usd 100 --reserve-usd 5 `
  --output output\paid_pilot
```

**Only `openai-run --execute` sends OpenAI requests.** A single OpenAI request decides the fictional actions for all posts in one synthetic feed session. The paid mode is sequential (one request at a time) by default. It does not retry failed requests automatically, since an HTTP timeout can still be billed.

Review `output\paid_pilot\report.json`, `REPORT.md`, and `api_ledger.jsonl`, plus **your actual platform billing**. Check action validity and whether costs match your assumed rates. Be wary of reasoning-token consumption on some models.

Then start a **separate large run** (adjust scale and prices to your validated pilot results):

```powershell
py -3 -m haven_sim openai-run --execute `
  --model $env:OPENAI_MODEL --price-input 0.10 --price-output 0.50 `
  --users 1000 --posts 250 --sessions-per-user 10 --feed-size 8 `
  --max-calls 10000 --budget-usd 100 --reserve-usd 5 `
  --output output\paid_main
```

You can raise `--max-calls` for the **same output folder** after measuring cost. The budget and reserve can also be adjusted, but **the model, pricing, user count, post count, feed size, seed and feed source are locked on resume** to prevent mixing two differently configured experiments. Changing any locked setting requires a **new output directory**.

**Important:** The local controller's target is **$95 estimated committed cost** plus a **$5 unused safety reserve**, under a user-selected $100 nominal budget. It uses reported token usage for successful responses and conservatively holds its estimate for ambiguous failed calls. This is **not an absolute billing cap**: prices might be wrong, model usage may vary, call failures can be billed, and other activity in your API project also costs money. Verify pricing and use independent project-level controls. The $100 is a *maximum planned local budget*, not a required amount to spend; some runs cost much less.

## D. Resume without paying twice

If you stop with Ctrl+C or your laptop restarts, run **the exact same command** with the same `--output` directory. The ledger stores each request reservation before sending it and every completed decision after the reply. Previously completed paid sessions are **replayed locally, never re-requested**. Your API key is not written to disk.

If a network failure/crash occurred after reservation but before a confirmed result, the program **fails closed** instead of repeating a potentially billed request. Inspect your OpenAI usage dashboard. If you accept substituting a clearly marked offline reaction for that uncertain session, rerun with `--skip-failed`. The uncertain request's local cost reservation is **not forgiven**. The output labels these recovered rows `decision_source=heuristic_recovery`, so they can be excluded from AI-only datasets.

**Do not manually delete or modify `api_ledger.jsonl`, `paid_run_config.json`, or your output folder while a paid run is active.** Don't launch two paid processes into the same folder.

## E. Use Haven's REAL recommendation algorithm

In a **separate** PowerShell window, start Haven's Node/TypeScript backend:

```powershell
cd haven-app\server
npm install
npm run dev
```

Then run these from the extracted simulator folder:

```powershell
py -3 -m haven_sim evaluate --api-url http://localhost:4000 --output output\contract
py -3 -m haven_sim plan-openai --feed-source api --users 100 --posts 100 --price-input 0.10 --price-output 0.50
```

To perform a paid run against the actual Haven feed ranking, add `--feed-source api --api-url http://localhost:4000` to your `openai-run --execute` command. **Haven's `POST /api/feed` is called with artificial fixture posts only**. This tool never calls `/api/posts` and does not persist them to the Haven server. The current backend has a 256 KB request-body limit; begin with 100–250 short synthetic posts and test before scaling.

## F. Outputs and training preparation

| Output | What it means |
|---|---|
| `posts.jsonl` / `personas.jsonl` | Fully fictional source fixtures; personas contain preferences and synthetic mood choices, no clinical information. |
| `api_ledger.jsonl` | Append-only request reservations, decisions, actual token counts and estimated USD costs; DO NOT commit to Git. |
| `events.jsonl` | Session-by-session impressions, fictional actions, finite session-end prompts. |
| `training_interactions.csv` | Dataset with `decision_source` (`openai` or `heuristic_recovery`) and the action/position/post. |
| `moderation_queue.jsonl` | Mock reports only; NOT real moderation tickets. |
| `report.json` / `REPORT.md` | Counts, mechanical guardrail audit, usage and budget estimates. |

Convert an existing completed **paid** run into simple sequential recommendation fixtures:

```powershell
py -3 -m haven_sim prepare-training --output output\paid_main
```

Creates `training_sequences.jsonl` and `sequence_stats.json`. By default, only **OpenAI-generated fictional saves** are treated as positive preference examples. **Hugs are intentionally excluded** because they mean *support*, not a ranking reward. Train/validation/test sequences are chronological where enough synthetic saves exist. This step merely **prepares training files; it does not train SASRec, a Transformer or any other model**. Any future Transformer must be separately implemented, trained locally (e.g. on your RTX 3050 4GB), benchmarked against Haven's existing ranker, and evaluated with real volunteers before use.

## G. Safety and limits

- All text is prewritten fictional fixture content. No scraping, medical histories, journals, private profiles or real user logs are read.
- Personal settings such as muting/blocked authors are applied before feeding posts to the ranker; Haven production must enforce those on the server, not only in a simulation.
- Audits check content-warning opt-in, heavy post caps, per-author caps, extra-care card positioning, eligibility and score structure. These are **software constraints, not clinical safety tests**.
- This release intentionally models **finite sessions** instead of infinite scroll, avoids watch-time optimization and never optimizes users' mood or mental-health vulnerabilities.
- Haven's existing keyword matching is a **limited demo**; human moderation and region-appropriate support services are still necessary.
- Paid LLM outputs are validated strictly. Missing IDs, invalid actions, extra content and malformed JSON trigger a stop while preserving possible charges. No silent training labels are invented.

## H. Tests / CI / repository integration

```powershell
py -3 -m unittest discover -s tests -v
```

The tests use fake OpenAI responses and mock Haven HTTP; **they do not spend money**. See `BACKEND_CONTRACT.md` and `.github/workflows/haven-feed-evaluation.yml` for integration. Copy the `haven-simulator/` directory to the Haven repo on a new branch; the existing frontend/backend need not be changed. GitHub previously denied the branch-creation request through the ChatGPT integration, so this package is supplied as a ZIP rather than committed to `main`.
