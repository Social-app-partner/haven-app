# Haven backend integration and evaluation contract — confirmed against main

Repository: https://github.com/Social-app-partner/haven-app

## Existing system observed

- `server/src/index.ts` is Node/Express. Routes: GET `/api/health`, GET `/api/moods`, POST `/api/feed`, POST `/api/posts`.
- `server/src/feedRanker.ts` contains a **non-ML**, mood-aware, deterministic feed scoring/reranking function.
- `server/src/types.ts` defines `Post` with `id`, `author`, `avatarColor`, `createdAt`, `moodChip?`, `title`, `body`, `hugs`, `replies`, `contentWarning?`.
- Context: `userMood?`, `consecutiveLowDays?`, `showSensitive?`, `topN?`. The feed route also accepts a `posts` array for ephemeral scoring; it does not persist them.
- Frontend `src/pages/Home.tsx` uses localStorage for user posts and demo seeds; it is **not fully wired** to this ranker yet.
- Mood check-ins and journal entries are local on the frontend. Do **not** put journal text, identifiable health details, or clinical inferences in the recommender dataset.

## Simulation -> backend request

```json
{
  "userMood": "low",
  "consecutiveLowDays": 3,
  "showSensitive": false,
  "topN": 10,
  "posts": [{
    "id": "p00000001", "author": "QuietMember00001", "avatarColor": "#7FA8C9",
    "createdAt": "2026-10-13T00:00:00Z", "title": "A gentle fictional post",
    "body": "A gentle fictional post", "hugs": 0, "replies": 0, "moodChip": "Calm"
  }]
}
```

`POST /api/feed` returns `{context, count, items}`. Each post item has `type: "post"`, post properties, `score`, possible `crisisResource`, while an extra-care card has `type: "extra-care-card"` and `reason`.

The Python simulator builds the request and evaluates only synthetic content. **No real app users or journal notes are ingested**. It never calls `/api/posts`.

## Backend work your teammate may want to prioritize

1. **Server-side post visibility:** user blocks, muted topics/content, removed posts and authorization must be applied *before ranking* by the backend. The Python adapter applies these for its own synthetic tests, but the server API doesn't currently accept or enforce them.
2. **Authenticated persistent store:** avoid treating `POST /api/posts`' current in-memory array as production storage; introduce a DB and authorization rules while minimizing health-related personal information.
3. **Durable report/appeal workflow:** current frontend report/block buttons can emit toast notifications without persistent state or enforced blocks. Implement actual review queues and block enforcement; do not presume the UI toast means a moderator has been notified.
4. **Mental-health safety design:** crisis resource cues need nuanced human review, local resources, and expert input. Do not automatically censor every support-seeking message containing a keyword, or assume keyword scans accurately identify risk.
5. **Privacy and consent:** optional mood selection should be an opt-in field with explicit data minimization, retention and deletion. No diagnoses, private journal entries, or health-related profiles in model features by default.
6. **Research objectives:** test fairness, topic coverage, user-control compliance, and voluntarily reported comfort. Do **not** optimize for hours watched, endless scroll or hug totals.
7. **TypeScript/React adapter:** `Home.tsx`'s post shape differs from the backend (`time` vs `createdAt`, styled mood chip object vs string). Integrate carefully without losing current offline fallback.

## Mechanical evaluation checks

`python -m haven_sim evaluate --api-url http://localhost:4000` performs 8 tests of Haven's live ranker: content warning suppression/opt-in, low-mood heavy cap, per-author cap, care-card 3-day threshold, no unexpected care card, crisis flag with no suppression, and hopeful-over-heavy ordering for a struggling viewer.

Passing the tests is evidence of only those properties **for the canned fixtures**. It is not evidence that posts are actually safe, users feel secure, or the algorithm improves mental health.

## Future training experiment

Only after the evaluation pipeline is solid, consider an experimental lightweight ML ranker and compare its output to this explicit-rule baseline. The planned $100 API usage is for **generating extra artificial data**, not for buying clinical validity. Keep real-world evaluation separate and voluntary.

## v0.3 paid generation note

The simulation package also supports `plan-openai` (network-free) and `openai-run --execute`
(opt-in, sequential and resumable) with a USD budget ledger. `prepare-training` creates
synthetic sequential save-based fixtures, **not a trained ranking model**. Do not turn
synthetic hug totals or inferred mental-health states into recommendation objectives.
No OpenAI credentials or real users are required for the package's unit tests.
