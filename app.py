"""
Orvexa Lead Pitcher
--------------------
Upload a leads spreadsheet -> clean it (dedupe, missing/invalid numbers) ->
generate a short, non-generic WhatsApp pitch per lead with Gemma 4 31B IT ->
review/edit each pitch -> open it in WhatsApp and send it yourself.

No message is ever sent automatically. Every pitch is generated as a
click-to-chat (wa.me) link that opens WhatsApp with the text pre-filled;
you press send. That keeps this compliant with WhatsApp's rules against
automated bulk messaging, and keeps a human check on every pitch before
it goes out.
"""

import io
import re
import time
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

try:
    import phonenumbers
    HAS_PHONENUMBERS = True
except ImportError:
    HAS_PHONENUMBERS = False


# --------------------------------------------------------------------------
# Config / defaults
# --------------------------------------------------------------------------

st.set_page_config(page_title="Orvexa Lead Pitcher", layout="wide")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_ID = "google/gemma-4-31b-it"
DEFAULT_REGION = "PK"

DEFAULT_AGENCY_INFO = """Agency name: Orvexa Systems
Services offered: website development, web app development, Shopify store development, AI automation, SEO & website maintenance, Excel/data cleaning, logo & banner design.
Contact to include only if it fits naturally: WhatsApp +923398867672, orvexasystems.site"""

DEFAULT_STYLE_GUIDE = """You write cold outreach messages that will be sent on WhatsApp. Write like a real person texting, not a marketing email.

Hard rules:
- 2 to 4 short sentences total, nothing more.
- No greetings like "Dear", "Hi there", or "I hope this message finds you well".
- No emojis unless the business itself is casual or youth-facing (e.g. a gym, a cafe).
- Reference ONE specific, concrete thing about THIS business from the details given (its name, what it does, whether it has a website) - never a generic compliment that could apply to any business.
- Propose ONE relevant idea for their type of business, not a list of every service we offer.
- Sign off as "Orvexa Systems" or "we" - never a personal first name.
- Ban these words/phrases entirely: "digital presence", "take your business to the next level", "in today's world", "unlock your potential", "elevate", "seamless", "game-changer", "unleash".
- Output ONLY the message text. No preamble, no quotation marks, no "Here's a pitch:"."""

REQUIRED_FIELDS = {
    "name": ["business", "company", "shop", "name"],
    "phone": ["whatsapp", "phone", "mobile", "contact number", "number", "cell"],
}
OPTIONAL_FIELDS = {
    "industry": ["industry", "category", "type", "niche", "sector"],
    "notes": ["note", "detail", "description", "about", "comment"],
    "website": ["website", "site", "url"],
    "person": ["contact person", "owner", "person", "contact name"],
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def guess_column(columns, keywords):
    for kw in keywords:
        for col in columns:
            if kw in col.lower():
                return col
    return None


def normalize_phone(raw):
    """Return (e164_number_or_None, error_or_None)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or str(raw).strip() == "":
        return None, "missing phone"

    text = str(raw).strip()
    # Excel loves turning phone numbers into floats like 923001234567.0
    text = re.sub(r"\.0$", "", text)
    digits_plus = re.sub(r"[^\d+]", "", text)

    if HAS_PHONENUMBERS:
        try:
            parsed = phonenumbers.parse(digits_plus, DEFAULT_REGION)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164), None
            return None, "invalid phone"
        except Exception:
            return None, "invalid phone"

    # Fallback if the phonenumbers package isn't installed
    digits = re.sub(r"\D", "", digits_plus)
    if digits.startswith("0092"):
        digits = digits[2:]
    if digits.startswith("92") and len(digits) == 12:
        return "+" + digits, None
    if digits.startswith("0") and len(digits) == 11:
        return "+92" + digits[1:], None
    if len(digits) == 10 and digits.startswith("3"):
        return "+92" + digits, None
    if len(digits) >= 11:
        return "+" + digits, None
    return None, "invalid phone"


def build_leads_table(raw_df, mapping, dedupe_by_name):
    """Turn the raw uploaded sheet into a cleaned lead list using the column mapping."""
    seen_phones, seen_names = set(), set()
    rows = []

    for idx, row in raw_df.iterrows():
        def get(field):
            col = mapping.get(field)
            if not col or col == "— none —":
                return ""
            val = row.get(col, "")
            return "" if pd.isna(val) else str(val).strip()

        name = get("name")
        clean_phone, phone_err = normalize_phone(row.get(mapping.get("phone")) if mapping.get("phone") else None)

        issue = None
        if not name:
            issue = "missing name"
        elif phone_err:
            issue = phone_err
        elif clean_phone in seen_phones:
            issue = "duplicate phone"
        elif dedupe_by_name and name.lower() in seen_names:
            issue = "duplicate name"

        if not issue:
            seen_phones.add(clean_phone)
            seen_names.add(name.lower())

        rows.append({
            "lead_id": f"row{idx}",
            "name": name,
            "phone_raw": get("phone"),
            "clean_phone": clean_phone or "",
            "industry": get("industry"),
            "notes": get("notes"),
            "website": get("website"),
            "person": get("person"),
            "issue": issue,
            "include": issue is None,
            "pitch": "",
            "status": "not sent",
        })

    return pd.DataFrame(rows)


def build_prompt(lead):
    details = (
        f"Business name: {lead['name']}\n"
        f"Industry/category: {lead['industry'] or 'unknown'}\n"
        f"Extra notes: {lead['notes'] or 'none'}\n"
        f"Website: {lead['website'] or 'none found - they likely have no website'}\n"
        f"Contact person: {lead['person'] or 'unknown, address the business directly'}"
    )
    return f"Lead details:\n{details}\n\nWrite the WhatsApp pitch message now."


def clean_pitch_text(text):
    text = text.strip()
    text = re.sub(r'^(sure|okay|ok|here\'?s|here is|got it)[^:]{0,40}:\s*', '', text, flags=re.I)
    text = text.strip('"\u201c\u201d\' ')
    return text.strip()


def call_gemma(api_key, system_prompt, user_prompt, temperature):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": 220,
    }
    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    return clean_pitch_text(data["choices"][0]["message"]["content"])


def regenerate_pitch_callback(lead_id, ix):
    """on_click callback for the per-lead Regenerate button.

    Callbacks run BEFORE the script body re-renders, so updating
    session_state here (unlike doing it inline further down the script,
    after the text_area for this same row has already rendered this run)
    actually takes effect on the next render.
    """
    key = f"pitch_text_{lead_id}"
    api_key_val = st.session_state.get("api_key_input", "")
    if not api_key_val:
        st.session_state[f"error_{lead_id}"] = "Add your OpenRouter API key in the sidebar first."
        return
    try:
        cdf = st.session_state.clean_df
        lead_row = cdf.loc[ix]
        user_prompt = build_prompt(lead_row)
        system_prompt = f"{st.session_state.get('style_guide_input', DEFAULT_STYLE_GUIDE)}\n\n{st.session_state.get('agency_info_input', DEFAULT_AGENCY_INFO)}"
        new_pitch = call_gemma(api_key_val, system_prompt, user_prompt, st.session_state.get("temperature_slider", 0.9))
        cdf.loc[ix, "pitch"] = new_pitch
        st.session_state.clean_df = cdf
        st.session_state[key] = new_pitch
        st.session_state[f"error_{lead_id}"] = ""
    except Exception as e:
        st.session_state[f"error_{lead_id}"] = str(e)


def wa_link(clean_phone, text):
    number = clean_phone.lstrip("+")
    return f"https://wa.me/{number}?text={quote(text)}"


def _to_xlsx_bytes(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="leads")
    return buf.getvalue()


def _get_secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


# --------------------------------------------------------------------------
# Sidebar - settings
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("Settings")

    api_key = st.text_input(
        "OpenRouter API key",
        value=_get_secret("OPENROUTER_API_KEY"),
        type="password",
        help="Get one at openrouter.ai. Stored only for this session.",
        key="api_key_input",
    )

    st.caption(f"Model: `{MODEL_ID}` via OpenRouter")

    temperature = st.slider("Pitch creativity", 0.3, 1.2, 0.9, 0.1, key="temperature_slider")

    dedupe_by_name = st.checkbox(
        "Also treat same business name as a duplicate",
        value=False,
        help="Turn on if the same business sometimes appears under two different phone numbers.",
    )

    with st.expander("Agency info given to the model"):
        agency_info = st.text_area("Agency info", DEFAULT_AGENCY_INFO, height=140, label_visibility="collapsed", key="agency_info_input")

    with st.expander("Pitch style rules given to the model"):
        style_guide = st.text_area("Style guide", DEFAULT_STYLE_GUIDE, height=280, label_visibility="collapsed", key="style_guide_input")


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

for key, default in [
    ("raw_df", None),
    ("clean_df", None),
    ("issues_df", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# --------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------

st.title("Orvexa Lead Pitcher")
st.caption("Upload leads → clean the list → generate a pitch per lead → open it in WhatsApp and send it yourself.")

# ---- Step 1: upload ----
uploaded = st.file_uploader("Upload your leads spreadsheet", type=["xlsx", "xls", "csv"])

if uploaded is not None and st.session_state.raw_df is None:
    try:
        if uploaded.name.lower().endswith(".csv"):
            df = pd.read_csv(uploaded, dtype=str, keep_default_na=False)
        else:
            df = pd.read_excel(uploaded, dtype=str, engine="openpyxl")
        df.columns = [str(c).strip() for c in df.columns]
        st.session_state.raw_df = df
    except Exception as e:
        st.error(f"Couldn't read that file: {e}")

if uploaded is None and st.session_state.raw_df is not None:
    # user removed the file - reset everything
    for key in ("raw_df", "clean_df", "issues_df"):
        st.session_state[key] = None

# ---- Step 2: column mapping ----
if st.session_state.raw_df is not None and st.session_state.clean_df is None:
    raw_df = st.session_state.raw_df
    st.success(f"Loaded {len(raw_df)} rows. Now match your columns.")
    columns = list(raw_df.columns)

    with st.form("mapping_form"):
        st.subheader("Map your columns")
        c1, c2 = st.columns(2)
        def opt_index(field_key):
            guess = guess_column(columns, OPTIONAL_FIELDS[field_key])
            options = ["— none —"] + columns
            return options.index(guess) if guess else 0

        with c1:
            name_col = st.selectbox("Business name", columns, index=columns.index(guess_column(columns, REQUIRED_FIELDS["name"])) if guess_column(columns, REQUIRED_FIELDS["name"]) else 0)
            phone_col = st.selectbox("Phone / WhatsApp number", columns, index=columns.index(guess_column(columns, REQUIRED_FIELDS["phone"])) if guess_column(columns, REQUIRED_FIELDS["phone"]) else 0)
            industry_col = st.selectbox("Industry / category (optional)", ["— none —"] + columns, index=opt_index("industry"))
        with c2:
            notes_col = st.selectbox("Notes / extra info (optional)", ["— none —"] + columns, index=opt_index("notes"))
            website_col = st.selectbox("Website (optional)", ["— none —"] + columns, index=opt_index("website"))
            person_col = st.selectbox("Contact person (optional)", ["— none —"] + columns, index=opt_index("person"))

        submitted = st.form_submit_button("Clean this list", type="primary")

    if submitted:
        mapping = {
            "name": name_col, "phone": phone_col, "industry": industry_col,
            "notes": notes_col, "website": website_col, "person": person_col,
        }
        table = build_leads_table(raw_df, mapping, dedupe_by_name)
        st.session_state.clean_df = table[table["issue"].isna()].reset_index(drop=True)
        st.session_state.issues_df = table[table["issue"].notna()].reset_index(drop=True)
        st.rerun()

# ---- Step 3: cleaning report ----
if st.session_state.clean_df is not None:
    clean_df = st.session_state.clean_df
    issues_df = st.session_state.issues_df
    total = len(clean_df) + len(issues_df)

    st.subheader("Cleaning report")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total rows", total)
    m2.metric("Ready to pitch", len(clean_df))
    m3.metric("Missing/invalid number", int((issues_df["issue"].isin(["missing phone", "invalid phone"])).sum()) if len(issues_df) else 0)
    m4.metric("Duplicates removed", int((issues_df["issue"].isin(["duplicate phone", "duplicate name"])).sum()) if len(issues_df) else 0)

    if len(issues_df):
        with st.expander(f"{len(issues_df)} leads need attention", expanded=False):
            st.caption("Fix a number or name below, then re-check it. Rows that still fail stay here.")
            edited_issues = st.data_editor(
                issues_df[["name", "phone_raw", "issue", "industry", "notes"]],
                key="issues_editor",
                width="stretch",
                disabled=["issue"],
            )
            if st.button("Re-check edited rows"):
                seen_phones = set(clean_df["clean_phone"])
                seen_names = set(clean_df["name"].str.lower())
                still_broken, newly_clean = [], []
                for i, row in edited_issues.iterrows():
                    orig = issues_df.iloc[i].to_dict()
                    name = str(row["name"]).strip()
                    clean_phone, err = normalize_phone(row["phone_raw"])
                    issue = None
                    if not name:
                        issue = "missing name"
                    elif err:
                        issue = err
                    elif clean_phone in seen_phones:
                        issue = "duplicate phone"
                    elif dedupe_by_name and name.lower() in seen_names:
                        issue = "duplicate name"

                    orig.update({"name": name, "phone_raw": row["phone_raw"], "clean_phone": clean_phone or "",
                                  "industry": row["industry"], "notes": row["notes"], "issue": issue,
                                  "include": issue is None})
                    if issue:
                        still_broken.append(orig)
                    else:
                        seen_phones.add(clean_phone)
                        seen_names.add(name.lower())
                        newly_clean.append(orig)

                st.session_state.clean_df = pd.concat([clean_df, pd.DataFrame(newly_clean)], ignore_index=True) if newly_clean else clean_df
                st.session_state.issues_df = pd.DataFrame(still_broken) if still_broken else issues_df.iloc[0:0]
                st.rerun()

            st.download_button(
                "Download flagged leads as CSV",
                issues_df.to_csv(index=False).encode("utf-8"),
                "leads_needing_attention.csv",
                "text/csv",
            )

    # ---- clean leads table: pick who to pitch ----
    if len(clean_df):
        st.subheader(f"Ready to pitch ({len(clean_df)})")
        display_cols = ["include", "name", "clean_phone", "industry", "notes", "pitch", "status"]
        edited_clean = st.data_editor(
            clean_df[display_cols],
            key="clean_editor",
            width="stretch",
            disabled=["name", "clean_phone", "industry", "pitch", "status"],
            column_config={"include": st.column_config.CheckboxColumn("Include")},
            height=min(400, 60 + 35 * len(clean_df)),
        )
        # persist include/notes edits back
        clean_df["include"] = edited_clean["include"]
        clean_df["notes"] = edited_clean["notes"]
        st.session_state.clean_df = clean_df

        selected = clean_df[clean_df["include"]]
        st.caption(f"{len(selected)} lead(s) selected for pitch generation.")

        # ---- Step 4: generate pitches ----
        if st.button(f"Generate pitches for {len(selected)} selected leads", type="primary", disabled=len(selected) == 0):
            if not api_key:
                st.error("Add your OpenRouter API key in the sidebar first.")
            else:
                progress = st.progress(0.0, text="Starting...")
                idxs = selected.index.tolist()
                for i, ix in enumerate(idxs):
                    lead = clean_df.loc[ix]
                    progress.progress((i) / len(idxs), text=f"Writing pitch for {lead['name']}...")
                    try:
                        user_prompt = build_prompt(lead)
                        pitch = call_gemma(api_key, f"{style_guide}\n\n{agency_info}", user_prompt, temperature)
                    except Exception as e:
                        pitch = f"[Error generating pitch: {e}]"
                    clean_df.loc[ix, "pitch"] = pitch
                    # a text_area widget remembers its own state by key once rendered,
                    # so it must be updated directly too, not just the dataframe
                    st.session_state[f"pitch_text_{lead['lead_id']}"] = pitch
                    time.sleep(0.15)
                progress.progress(1.0, text="Done.")
                st.session_state.clean_df = clean_df
                st.rerun()

        # ---- Step 5: review, edit, send ----
        to_review = clean_df[(clean_df["include"]) & (clean_df["pitch"] != "")]
        if len(to_review):
            st.subheader("Review & send")
            st.caption("Read every pitch before sending it. Edit anything that sounds off, then open it in WhatsApp.")

            for ix, lead in to_review.iterrows():
                with st.expander(f"{lead['name']}  ·  {lead['clean_phone']}  ·  {lead['status']}"):
                    pitch_key = f"pitch_text_{lead['lead_id']}"
                    # Seed session_state only once. Passing `value=` on every render
                    # would fight with programmatic updates from Regenerate below -
                    # Streamlit keeps the `value=` argument and silently drops the
                    # session_state override, so a regenerated pitch would never show.
                    if pitch_key not in st.session_state:
                        st.session_state[pitch_key] = lead["pitch"]
                    edited_pitch = st.text_area("Pitch", key=pitch_key, height=100, label_visibility="collapsed")
                    clean_df.loc[ix, "pitch"] = edited_pitch

                    b1, b2, b3 = st.columns([1, 1, 2])
                    with b1:
                        st.button(
                            "Regenerate",
                            key=f"regen_{lead['lead_id']}",
                            on_click=regenerate_pitch_callback,
                            args=(lead["lead_id"], ix),
                        )
                    err = st.session_state.get(f"error_{lead['lead_id']}")
                    if err:
                        st.error(err)
                    with b2:
                        if lead["clean_phone"]:
                            st.link_button("Open in WhatsApp", wa_link(lead["clean_phone"], edited_pitch))
                    with b3:
                        sent = st.checkbox("Mark as sent", value=(lead["status"] == "sent"), key=f"sent_{lead['lead_id']}")
                        clean_df.loc[ix, "status"] = "sent" if sent else "not sent"

            st.session_state.clean_df = clean_df

            st.divider()
            st.download_button(
                "Download full results (xlsx)",
                data=_to_xlsx_bytes(clean_df.drop(columns=["lead_id", "include"])),
                file_name="orvexa_leads_pitched.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    if st.button("Start over with a new file"):
        for key in ("raw_df", "clean_df", "issues_df"):
            st.session_state[key] = None
        st.rerun()
