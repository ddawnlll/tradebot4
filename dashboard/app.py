"""Streamlit entrypoint and navigation shell."""

from __future__ import annotations

import streamlit as st

from dashboard import cache, common, data

common.configure_page()

runs = cache.list_runs(str(data.runs_dir()))
common.sidebar(runs)

pages = [
    st.Page("pages/overview.py", title="Overview", icon=":material/monitoring:", default=True),
    st.Page("pages/breakdowns.py", title="Breakdowns", icon=":material/category:"),
    st.Page("pages/trade_replay.py", title="Trade Replay", icon=":material/candlestick_chart:"),
    st.Page("pages/calibration.py", title="Calibration", icon=":material/tune:"),
    st.Page("pages/error_pareto.py", title="Error Pareto", icon=":material/priority_high:"),
    st.Page("pages/execution_quality.py", title="Execution Quality", icon=":material/bolt:"),
    st.Page("pages/missed_opportunities.py", title="Missed Opportunities", icon=":material/search:"),
]

navigation = st.navigation(pages, position="sidebar")
navigation.run()
