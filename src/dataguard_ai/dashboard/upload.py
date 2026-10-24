"""Local upload UI; deliberately independent of Olist reports and providers."""

import hashlib
import json

import pandas as pd
import streamlit as st

from dataguard_ai.generic.analysis import (
    DEFAULT_LIMITS,
    DELIMITERS,
    ENCODINGS,
    AnalysisError,
    Rules,
    analyze,
    parse_csv,
    read_upload,
)


def upload_view() -> None:
    st.title("Analyze Your CSV")
    st.write("Profile a CSV and choose the structural constraints that matter to you.")
    st.warning(
        "Use only data you are authorized to inspect. Uploads go to this Streamlit "
        "server and may contain personal data. The default local server keeps analysis "
        "on your machine; a remote deployment receives your file. No data is sent to "
        "AI APIs or PostgreSQL, and this analyzer does not save datasets to disk. "
        "Close the session to release its upload; a shared server is not "
        "a privacy boundary."
    )
    st.caption(
        "Limits: 5 MiB · 50,000 rows · 100 columns · 250,000 cells · "
        "64 MiB estimated working memory · 16,000 characters per cell. "
        "These are application limits, not a hard process-memory guarantee."
    )
    uploaded = st.file_uploader("Upload CSV", type=["csv"], key="generic_upload")
    encoding = st.selectbox("CSV encoding", ENCODINGS)
    delimiter = st.selectbox("CSV delimiter", tuple(DELIMITERS))
    if uploaded is None:
        st.session_state.pop("generic_result", None)
        st.session_state.pop("generic_source", None)
        st.info("Upload a CSV to begin. No existing Olist reports are required.")
        return
    try:
        content = read_upload(uploaded)
        source = (hashlib.sha256(content).hexdigest(), encoding, delimiter)
        if st.session_state.get("generic_source") != source:
            st.session_state.pop("generic_result", None)
            # Column widget state belongs to one uploaded schema only.
            for key in list(st.session_state):
                if key.startswith("csv_rule_"):
                    del st.session_state[key]
            st.session_state["generic_source"] = source
        dataset = parse_csv(content, encoding=encoding, delimiter=DELIMITERS[delimiter])
    except AnalysisError as error:
        st.session_state.pop("generic_result", None)
        st.error(str(error))
        return
    st.caption("Cells stay as text. Nothing is corrected or written back to the CSV.")
    if st.checkbox(
        "Show local data preview (may reveal sensitive values)", key="csv_rule_preview"
    ):
        st.dataframe(
            pd.DataFrame(dataset.rows[:20], columns=dataset.columns),
            hide_index=True,
            width="stretch",
        )
        st.caption("First 20 data records only. Preview contains original cell values.")
    with st.form("generic_rules"):
        st.subheader("Choose validation rules")
        st.caption(
            "No business constraints are assumed. Missing means empty/whitespace-only. "
            "Optional checks skip missing cells; select Required to reject them. "
            "Unique flags every member of a repeated nonmissing value. "
            "Nonnegative also checks finite decimal syntax (dot decimal, "
            "optional exponent)."
        )
        required = st.multiselect(
            "Required columns", dataset.columns, key="csv_rule_required"
        )
        unique = st.multiselect(
            "Unique columns", dataset.columns, key="csv_rule_unique"
        )
        numeric = st.multiselect(
            "Numeric columns", dataset.columns, key="csv_rule_numeric"
        )
        nonnegative = st.multiselect(
            "Nonnegative columns", dataset.columns, key="csv_rule_nonnegative"
        )
        # A shared explicit format keeps configuration inspectable and predictable.
        dates = st.multiselect(
            "Date/time columns", dataset.columns, key="csv_rule_dates"
        )
        pattern = st.text_input(
            "Date/time format for selected columns",
            value="%Y-%m-%d",
            key="csv_rule_format",
            help=(
                "Python strptime format, e.g. %Y-%m-%d or %H:%M:%S. "
                "Parsing allows unpadded components."
            ),
        )
        submitted = st.form_submit_button("Analyze CSV")
    if submitted:
        try:
            st.session_state["generic_result"] = analyze(
                dataset,
                Rules(
                    tuple(required),
                    tuple(unique),
                    tuple(numeric),
                    tuple(nonnegative),
                    dict.fromkeys(dates, pattern),
                ),
            )
        except AnalysisError as error:
            st.session_state.pop("generic_result", None)
            st.error(str(error))
    result = st.session_state.get("generic_result")
    if result is None:
        st.info(
            "Select constraints and click Analyze CSV; selecting none runs "
            "profiling only."
        )
        return
    st.subheader("Analysis results")
    st.caption(
        "Snapshot of the last submitted configuration; submit again after "
        "changing rules."
    )
    profile = result["profile"]
    columns = st.columns(2)
    columns[0].metric("Data rows", f"{profile['row_count']:,}")
    columns[1].metric("Columns", profile["column_count"])
    columns[0].metric(
        "Duplicate rows beyond first", profile["duplicate_rows_beyond_first"]
    )
    columns[1].metric("Configured-rule failures", result["finding_count"])
    st.dataframe(pd.DataFrame(profile["columns"]), hide_index=True, width="stretch")
    st.caption(
        "Type inference is heuristic; a unique column in this file is "
        "only a candidate key."
    )
    if result["counts"]:
        st.dataframe(pd.DataFrame(result["counts"]), hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame(result["findings"]), hide_index=True, width="stretch")
    else:
        st.info(
            "No failures under the submitted rules. This is not proof of "
            "overall data quality."
        )
    if result["findings_truncated"]:
        st.warning(
            f"Only the first {DEFAULT_LIMITS.max_findings:,} failures are listed; "
            "totals count all failures."
        )
    with st.expander("Submitted configuration and limitations"):
        st.json(result["configured_rules"])
        for limitation in result["limitations"]:
            st.write(limitation)
        st.caption(f"Generated (UTC): {result['generated_at']}")
    st.caption(
        "JSON omits cell values and filenames. Column names, row positions and counts "
        "can still be sensitive: review before sharing. No file is saved automatically."
    )
    st.download_button(
        "Download quality report (JSON)",
        json.dumps(result, ensure_ascii=False, indent=2),
        file_name="csv_quality_report.json",
        mime="application/json",
    )
