# Orvexa Lead Pitcher

Upload a leads spreadsheet → the app cleans it (dedupes, flags missing/invalid
numbers) → you pick which leads to pitch → it writes a short, non-generic
WhatsApp pitch per lead with **Kimi K3 from NVIDIA** → you read/edit each pitch →
click through to WhatsApp and send it yourself.

Nothing is ever sent automatically — every pitch opens as a pre-filled
WhatsApp chat (`wa.me` link) and you press send. That keeps a human check on
every message and keeps this within WhatsApp's rules (no bulk automated
sending).

## How it works

1. **Upload** — drop in an `.xlsx`, `.xls`, or `.csv` of leads.
2. **Map columns** — tell it which column is the business name, which is the
   phone number, etc. It guesses sensible defaults from your headers.
3. **Clean** — it flags rows with a missing number, an invalid number, or a
   duplicate (same number seen twice). You can fix a row inline and
   re-check it, or download the flagged rows to fix separately.
4. **Generate pitches** — for the leads you select, it calls Kimi K3 from NVIDIA
   with a strict style guide (no "Dear Sir/Madam," no filler, one concrete
   detail about the business, 2–4 sentences, signed as Orvexa Systems) so
   pitches don't read like generic AI outreach.
5. **Review & send** — edit any pitch that sounds off, then hit **Open in
   WhatsApp** to send it from your own number.
6. **Export** — download the final sheet with every pitch and its sent
   status.

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Get an NVIDIA API key from the [NVIDIA API Catalog](https://build.nvidia.com/)
and then either:

- Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and
  paste your key in, **or**
- Just paste the key into the sidebar field when the app is running — it's
  only kept for that session.

Run it:

```bash
streamlit run app.py
```

Try it with `sample_leads.csv` first — it has a duplicate, a missing
number, and a bad number already in it, so you can see the cleaning step
work.

## Deploy: GitHub + Streamlit Community Cloud

1. **Push to GitHub**

   ```bash
   git init
   git add .
   git commit -m "Orvexa lead pitcher"
   git branch -M main
   git remote add origin https://github.com/<your-username>/orvexa-lead-pitcher.git
   git push -u origin main
   ```

   `.streamlit/secrets.toml` is git-ignored on purpose — your real API key
   never gets pushed.

2. **Deploy on Streamlit Cloud**
   - Go to [share.streamlit.io](https://share.streamlit.io) and sign in
     with GitHub.
   - Click **New app**, pick this repo, branch `main`, main file `app.py`.
   - Before (or after) deploying, open **Settings → Secrets** on the app
     and paste in:
     ```toml
    NVIDIA_API_KEY = "nvapi-..."
     ```
   - Deploy. The app picks the key up from secrets automatically, so you
     won't need to paste it into the sidebar each time.

3. Every future `git push` to `main` redeploys the app automatically.

## Notes

- Phone numbers are normalized assuming Pakistan (`PK`) as the default
  region — a bare `0300xxxxxxx` becomes `+92300xxxxxxx`. Numbers already
  in international format are left as-is.
- The style guide and agency info sent to the model are editable in the
  sidebar, so you can retune the pitch voice any time without touching
  code.
- The app uses NVIDIA's OpenAI-compatible chat completions endpoint with
  `moonshotai/kimi-k3`. Keep your NVIDIA API key in Streamlit Secrets or the
  temporary sidebar field; never commit the real key.
