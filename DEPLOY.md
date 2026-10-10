# Putting MUSE on a public URL

Everything needed is in the repository. What is left is a hosting account,
which has to be yours — it is tied to a billing identity and an email.

## The short path (Render, free)

1. Push this branch to GitHub.
2. Go to **render.com** → sign in with GitHub → **New → Blueprint**.
3. Pick the `muse-ai-coding-assistant` repository and the branch you pushed.
   Render reads `render.yaml` and fills everything in by itself.
4. It will ask for the two values marked `sync: false`:
   - `MISTRAL_API_KEY`
   - `GROQ_API_KEY`
5. **Create**. First build takes roughly four minutes.

The URL you get is public. Anyone with the link can open it — no login.

Check `https://<your-url>/api/health` first. It reports `"llm": "live"` when
the keys loaded, and `"llm": "offline-stub"` when they did not. If it says
offline-stub, the keys did not reach the container, and every AI reply on the
site will be canned text.

## What boots with it

`start.sh` runs before the server starts:

- `python -m app.seed` — the course, eight students, both subjects, all the
  notes and exercises. Additive, so it never destroys work.
- `python -m app.demo` — a cohort's worth of finished work, so the instructor
  dashboard has something real to aggregate instead of opening empty. No LLM
  calls; every string in it is written down.

## Three things to know before you share the link

**The free plan sleeps.** After about fifteen minutes with no traffic the
container shuts down, and the next visitor waits roughly fifty seconds for it
to wake. Open the link yourself a minute before anyone else does.

**The disk is wiped on every restart.** SQLite lives inside the container, so
a sleep, a redeploy or a crash resets the database to its seeded state. For a
demo that is fine — it self-heals and is never empty. For anything where real
students do real work, point `DATABASE_URL` at a hosted Postgres instead.

**There is no authentication.** Anyone with the link can choose any student
from the dropdown, read their whole tutoring transcript, and open the teacher
dashboard. The seeded data is synthetic, but the student names are real
people's. And every visitor who talks to Ollie spends your API quota.

If that last point matters, the options are, cheapest first:

- Deploy with `USE_FAKE_LLM=1` and no keys. Nothing can be spent. The cost is
  that every AI reply is visibly canned, which a judge who clicks around will
  notice.
- Add a shared passcode in front of the whole app — about twenty lines of
  middleware, and you hand the passcode to the judges.
- Leave it open and rotate both keys the moment the hackathon ends.

## Other hosts

The `Dockerfile` is plain and portable, so Railway, Fly.io and Hugging Face
Spaces all work from the same image. Only `render.yaml` is Render-specific.
Every host passes the port in as `$PORT`, which `start.sh` already reads.

## Running the container locally first

Worth doing once before you trust it in front of judges:

    docker build -t muse .
    docker run --rm -p 8000:8000 --env-file .env muse

Then open http://localhost:8000. What you see there is exactly what the public
URL will serve.
