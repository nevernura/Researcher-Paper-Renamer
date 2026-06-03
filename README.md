# Research Paper Renamer

Link to web-app: https://researcher-paper-renamer.streamlit.app

Renames academic PDFs to **`Year_TitleInCamelCase.pdf`** (e.g. `2017_AttentionIsAllYouNeed.pdf`).

It reads the first page of each PDF and figures out the title and year with a
hybrid pipeline:

1. **DOI → CrossRef** — if a DOI is on the page, it fetches the authoritative
   title and year from the free CrossRef API (no key, no cost).
2. **Gemini (optional)** — if there's no DOI and you've supplied a free Gemini
   key, it asks the model to read the page text and return the title + year.
3. **Best guess** — with no DOI and no key, it falls back to a simple heuristic
   you can correct in the review table.

Nothing is renamed until you review (and edit) the proposed names.

## Two modes

| Mode | What it does | Where it works |
|------|--------------|----------------|
| **Upload files** | Upload PDFs → review → download renamed files (single or ZIP) | Anywhere, including the hosted app |
| **Local folder** | Point at a folder → review → rename in place *or* copy to a new folder, with undo | Only when you run the app on your own computer |

A hosted web app can't reach files on your disk (browsers sandbox uploads), so
**in-place renaming is a local-mode feature only**. The hosted link always
works by upload/download.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

To make the app default to local-folder mode (so you can rename in place):

```bash
PDF_RENAMER_LOCAL=1 streamlit run app.py        # macOS / Linux
set PDF_RENAMER_LOCAL=1 && streamlit run app.py # Windows (cmd)
```

You can always switch modes from the sidebar regardless.

## Deploy the hosted version (free)

1. Push `app.py`, `core.py`, and `requirements.txt` to a GitHub repo.
2. Go to share.streamlit.io, connect the repo, and pick `app.py`.
3. That's it — you get a public URL to share.

The hosted app works **with no key** (CrossRef only). Each user can optionally
paste their own free Gemini key in the sidebar to enable the fallback for
papers without a DOI. Keys are held for the session only and never stored or
logged.

If you'd rather bake in one shared Gemini key for a private group instead of
asking users for theirs, add it to Streamlit secrets and read it in `app.py`
(replace the sidebar key field with `st.secrets["GEMINI_KEY"]`).

## Get a free Gemini key (optional)

Sign in at Google AI Studio and create an API key — the free tier needs no
credit card. It's only used as a fallback when a paper has no DOI, so many
libraries barely touch it. Free-tier rate limits apply, so very large batches
may need to be processed in chunks.

## Notes & edge cases

- **Scanned / image-only PDFs** have no extractable text and are flagged
  "No text found — enter manually." (OCR could be added later.)
- **Filename rules** (max title words, max length) are adjustable in the
  sidebar. Illegal characters are stripped automatically and name collisions
  get a numeric suffix like `(2)`.
- **Undo** (local mode): each run writes a small `.pdf_renamer_log.json` in the
  target folder; the "Undo last run" button reverses an in-place rename or
  deletes the copies it made.
- The Gemini model name is set near the top of `core.py` (`GEMINI_MODEL`);
  update it if Google changes the current free-tier model.

## Files

- `app.py` — Streamlit UI (both modes)
- `core.py` — extraction + filename logic, no Streamlit dependency (unit-testable)
- `requirements.txt` — dependencies
