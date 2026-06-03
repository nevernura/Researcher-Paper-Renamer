"""
app.py — PDF Renamer (Streamlit)

Renames academic PDFs to  Year_TitleInCamelCase.pdf  using a hybrid pipeline:
DOI -> CrossRef (free, no key)  ->  Gemini free tier (optional key)  ->  guess.

Two modes, same code:
  * Hosted / Upload mode  : upload PDFs -> review -> download (single or ZIP).
  * Local folder mode     : point at a folder -> review -> rename in place OR
                            copy renamed to a new folder, with an undo log.

Run locally:   streamlit run app.py        (set PDF_RENAMER_LOCAL=1 to default
                                             the UI to local-folder mode)
"""

import io
import os
import json
import shutil
import zipfile
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

import core

st.set_page_config(page_title="PDF Renamer", page_icon="📄", layout="wide")

LOG_NAME = ".pdf_renamer_log.json"   # written into the working/destination folder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def local_default() -> bool:
    """Default to local-folder mode only when explicitly flagged."""
    return os.environ.get("PDF_RENAMER_LOCAL", "").strip() == "1"


def status_for(source: str) -> str:
    return {
        "crossref": "Found via CrossRef",
        "gemini": "Found via Gemini",
        "guess": "Guess — please check",
        "no_text": "No text found — enter manually",
        "none": "Nothing found — enter manually",
    }.get(source, source)


def analyze(sources):
    """
    sources: list of (key, display_name, pdf_input) where pdf_input is bytes
    (upload) or a filesystem path string (local).
    Returns a list of row dicts ready for the editor.
    """
    rows = []
    gem_key = st.session_state.get("gemini_key", "")
    email = st.session_state.get("contact_email", "")
    prog = st.progress(0.0, text="Reading PDFs…")
    for i, (key, name, pdf_input) in enumerate(sources, start=1):
        try:
            text = core.first_page_text(pdf_input)
        except Exception as e:
            text = ""
            st.warning(f"Could not read {name}: {e}")
        meta = core.extract_metadata(text, gemini_key=gem_key, contact_email=email)
        rows.append({
            "Include": meta["source"] not in ("no_text",),
            "Original": name,
            "Title": meta["title"],
            "Year": meta["year"],
            "Status": status_for(meta["source"]),
            "_key": key,
        })
        prog.progress(i / len(sources), text=f"Analyzed {i}/{len(sources)}")
    prog.empty()
    return rows


def compute_new_names(df: pd.DataFrame, max_words: int, max_len: int):
    """Return a list of proposed filenames aligned to df rows (collision-safe)."""
    used = set()
    names = []
    for _, r in df.iterrows():
        if not r["Include"]:
            names.append("")
            continue
        base = core.build_basename(r["Year"], r["Title"], max_words, max_len)
        names.append(core.dedupe_name(base, used))
    return names


# ---------------------------------------------------------------------------
# Sidebar — settings shared by both modes
# ---------------------------------------------------------------------------
st.sidebar.header("Settings")

mode = st.sidebar.radio(
    "Mode",
    ["Upload files", "Local folder"],
    index=1 if local_default() else 0,
    help="Upload works anywhere (including the hosted app). "
         "Local folder lets you rename files in place and only works when you "
         "run the app on your own machine.",
)

st.sidebar.text_input(
    "Contact email (optional)",
    key="contact_email",
    help="Sent to CrossRef in a polite User-Agent. Not required, but courteous.",
)

st.sidebar.text_input(
    "Gemini API key (optional)",
    key="gemini_key",
    type="password",
    help="Free key from Google AI Studio. Only used as a fallback when a paper "
         "has no DOI. Held for this session only — never stored or logged.",
)

with st.sidebar.expander("Filename rules"):
    max_words = st.number_input("Max title words", 1, 30,
                                core.DEFAULT_MAX_TITLE_WORDS)
    max_len = st.number_input("Max filename length", 20, 240,
                              core.DEFAULT_MAX_FILENAME_LEN)

st.sidebar.caption(
    "Pipeline: DOI → CrossRef (free) → Gemini (if key) → best-guess. "
    "Format: Year_TitleInCamelCase.pdf"
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("📄 PDF Renamer")
st.caption("Rename academic papers to `Year_TitleInCamelCase.pdf`.")

# =========================== UPLOAD MODE ===================================
if mode == "Upload files":
    uploads = st.file_uploader(
        "Drop PDFs here", type=["pdf"], accept_multiple_files=True
    )

    if uploads and st.button("Analyze", type="primary"):
        sources = [(u.name, u.name, u.getvalue()) for u in uploads]
        # cache raw bytes so we can build downloads later without re-reading
        st.session_state["upload_bytes"] = {u.name: u.getvalue() for u in uploads}
        st.session_state["rows"] = analyze(sources)

    if st.session_state.get("rows") and mode == "Upload files":
        st.subheader("Review & edit")
        df = pd.DataFrame(st.session_state["rows"])
        edited = st.data_editor(
            df, hide_index=True, use_container_width=True,
            disabled=["Original", "Status", "_key"],
            column_config={
                "Include": st.column_config.CheckboxColumn(width="small"),
                "Title": st.column_config.TextColumn(width="large"),
                "Year": st.column_config.TextColumn(width="small"),
                "_key": None,
            },
        )
        new_names = compute_new_names(edited, max_words, max_len)
        preview = pd.DataFrame({
            "Original": edited["Original"],
            "→ New name": new_names,
        })[edited["Include"].values]
        st.dataframe(preview, hide_index=True, use_container_width=True)

        if st.button("Build renamed files", type="primary"):
            byte_map = st.session_state.get("upload_bytes", {})
            picked = [(edited.iloc[i]["Original"], new_names[i])
                      for i in range(len(edited)) if edited.iloc[i]["Include"]
                      and new_names[i]]
            if not picked:
                st.warning("Nothing selected.")
            elif len(picked) == 1:
                orig, new = picked[0]
                st.download_button("Download " + new, data=byte_map[orig],
                                   file_name=new, mime="application/pdf")
            else:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for orig, new in picked:
                        zf.writestr(new, byte_map[orig])
                buf.seek(0)
                st.download_button("Download ZIP (%d files)" % len(picked),
                                   data=buf, file_name="renamed_pdfs.zip",
                                   mime="application/zip")

# =========================== LOCAL FOLDER MODE =============================
else:
    st.info("Local mode renames files on this computer. It only works when you "
            "run the app yourself — the hosted version can't reach your disk.")
    folder = st.text_input("Folder containing PDFs",
                           value=st.session_state.get("folder", ""))

    if folder and st.button("Scan folder", type="primary"):
        p = Path(folder).expanduser()
        if not p.is_dir():
            st.error("That folder doesn't exist.")
        else:
            st.session_state["folder"] = str(p)
            pdfs = sorted(p.glob("*.pdf"))
            if not pdfs:
                st.warning("No PDFs found in that folder.")
            else:
                sources = [(str(f), f.name, str(f)) for f in pdfs]
                st.session_state["rows"] = analyze(sources)

    if st.session_state.get("rows") and mode == "Local folder":
        st.subheader("Review & edit")
        df = pd.DataFrame(st.session_state["rows"])
        edited = st.data_editor(
            df, hide_index=True, use_container_width=True,
            disabled=["Original", "Status", "_key"],
            column_config={
                "Include": st.column_config.CheckboxColumn(width="small"),
                "Title": st.column_config.TextColumn(width="large"),
                "Year": st.column_config.TextColumn(width="small"),
                "_key": None,
            },
        )
        new_names = compute_new_names(edited, max_words, max_len)
        preview = pd.DataFrame({
            "Original": edited["Original"], "→ New name": new_names,
        })[edited["Include"].values]
        st.dataframe(preview, hide_index=True, use_container_width=True)

        col1, col2 = st.columns(2)
        action = col1.radio("When applying",
                            ["Copy to a new folder (safe)", "Rename in place"])
        dest = ""
        if action.startswith("Copy"):
            dest = col2.text_input("Destination folder",
                                   value=str(Path(st.session_state["folder"]) /
                                             "renamed"))

        if st.button("Apply", type="primary"):
            folder_p = Path(st.session_state["folder"])
            mapping = []   # (old_path, new_path) for the log/undo
            errors = []
            in_place = action == "Rename in place"
            dest_p = folder_p if in_place else Path(dest).expanduser()
            if not in_place:
                dest_p.mkdir(parents=True, exist_ok=True)

            for i in range(len(edited)):
                if not edited.iloc[i]["Include"] or not new_names[i]:
                    continue
                old = Path(edited.iloc[i]["_key"])   # full path (local mode)
                new = dest_p / new_names[i]
                try:
                    if in_place:
                        old.rename(new)
                    else:
                        shutil.copy2(old, new)
                    mapping.append([str(old), str(new), in_place])
                except Exception as e:
                    errors.append(f"{old.name}: {e}")

            # write a log so the run can be undone
            log_path = dest_p / LOG_NAME
            log = {"time": datetime.now().isoformat(),
                   "in_place": in_place, "items": mapping}
            try:
                log_path.write_text(json.dumps(log, indent=2))
            except Exception:
                pass
            st.session_state["last_log"] = str(log_path)

            st.success(f"Done — {len(mapping)} file(s) "
                       f"{'renamed' if in_place else 'copied'}.")
            if errors:
                st.error("Some files failed:\n- " + "\n- ".join(errors))
            st.session_state.pop("rows", None)

    # Undo
    last_log = st.session_state.get("last_log")
    if last_log and Path(last_log).exists():
        st.divider()
        if st.button(f"Undo last run ({Path(last_log).name})"):
            log = json.loads(Path(last_log).read_text())
            undone, fails = 0, []
            for old, new, was_in_place in reversed(log["items"]):
                try:
                    if was_in_place:
                        Path(new).rename(old)        # reverse the rename
                    else:
                        Path(new).unlink(missing_ok=True)  # delete the copy
                    undone += 1
                except Exception as e:
                    fails.append(f"{new}: {e}")
            Path(last_log).unlink(missing_ok=True)
            st.session_state.pop("last_log", None)
            st.success(f"Undid {undone} change(s).")
            if fails:
                st.error("Could not undo:\n- " + "\n- ".join(fails))
