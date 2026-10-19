
import streamlit as st
import pandas as pd
import subprocess
import sys
from pathlib import Path
from audit_agent import get_audit_recommendations
from database import get_connection


st.set_page_config(
    page_title="SiteAudit",
    page_icon="🔎",
    layout="wide"
)

st.subheader("Run Website Scan")

with st.form("scan_form"):
    website_url = st.text_input(
        "Website URL",
        placeholder="https://example.com"
    )

    page_limit = st.number_input(
        "Maximum pages",
        min_value=1,
        max_value=200,
        value=50,
        step=1
    )

    run_scan = st.form_submit_button("Start Scan")

if run_scan:
    if not website_url.strip():
        st.error("Please enter a website URL.")
    else:
        crawler_path = Path(__file__).resolve().parent / "crawler.py"

        with st.spinner("Crawling website and saving results to MySQL..."):
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(crawler_path)
                    ],
                    input=f"{website_url.strip()}\n{page_limit}\n",
                    capture_output=True,
                    text=True,
                    timeout=900
                )

                if result.returncode == 0:
                    st.success("Scan process completed.")
                    st.code(result.stdout or "Scan finished.")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("The scan failed.")
                    st.code(result.stderr or result.stdout)

            except subprocess.TimeoutExpired:
                st.error(
                    "The scan exceeded 15 minutes. "
                    "Check the crawler and try a smaller page limit."
                )
            except Exception as error:
                st.error(f"Could not start the crawler: {error}")


st.title("SiteAudit — Website Audit Dashboard")
st.caption("Monitor website scans, SEO issues, broken links and resolutions.")

@st.cache_data(ttl=5)
def load_data(query):
    connection = get_connection()
    try:
        return pd.read_sql(query, connection)
    finally:
        connection.close()


try:
    scans = load_data("""
        SELECT scan_id, start_url, page_limit,
               pages_inspected, issues_detected, scan_time
        FROM scans
        ORDER BY scan_id DESC
    """)

    issues = load_data("""
        SELECT issue_id, scan_id, source_url, target_url,
               issue_type, http_status, severity, status
        FROM issues
        ORDER BY issue_id DESC
    """)

    pages = load_data("""
        SELECT page_id, scan_id, url, http_status,
               final_url, redirect_count, title, error
        FROM pages
        ORDER BY page_id DESC
    """)

    if not scans.empty:
        selected_scan = st.selectbox(
            "Select scan",
            scans["scan_id"].tolist(),
            format_func=lambda scan_id: (
                f"Scan #{scan_id} — "
                f"{scans.loc[scans['scan_id'] == scan_id, 'start_url'].iloc[0]}"
            )
        )

        current_scan = scans[scans["scan_id"] == selected_scan]
        current_issues = issues[issues["scan_id"] == selected_scan]
        current_pages = pages[pages["scan_id"] == selected_scan]

        total_pages = len(current_pages)
        total_issues = len(current_issues)
        open_issues = (
            current_issues["status"].eq("Open").sum()
            if not current_issues.empty else 0
        )
        resolved_issues = (
            current_issues["status"].eq("Resolved").sum()
            if not current_issues.empty else 0
        )

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Pages Crawled", total_pages)
        col2.metric("Issues Detected", total_issues)
        col3.metric("Open Issues", int(open_issues))
        col4.metric("Resolved Issues", int(resolved_issues))

        st.subheader("Scan Details")
        st.write("**Website:**", current_scan.iloc[0]["start_url"])
        st.write("**Scan time:**", current_scan.iloc[0]["scan_time"])

        
        st.subheader("Audit Agent Recommendations")

        if st.button(
            "Analyze Selected Scan",
            key=f"analyze_scan_{selected_scan}"
        ):
            try:
                recommendations = get_audit_recommendations(
                    int(selected_scan)
                )
                st.session_state["audit_recommendations"] = {
                    "scan_id": int(selected_scan),
                    "items": recommendations
                }
            except Exception as error:
                st.error(f"Audit analysis failed: {error}")

        saved_analysis = st.session_state.get("audit_recommendations")

        if (
            saved_analysis
            and saved_analysis["scan_id"] == int(selected_scan)
        ):
            recommendations = saved_analysis["items"]

            if not recommendations:
                st.success("No issues found in this scan.")
            else:
                for item in recommendations:
                    with st.expander(
                        f"Issue #{item['issue_id']} — "
                        f"{item['issue_type']} — "
                        f"{item['priority_label']}"
                    ):
                        st.write("**Status:**", item["status"])
                        st.write("**Target URL:**", item["target_url"])
                        st.write("**HTTP status:**", item["http_status"])
                        st.write("**Impact:**", item["impact"])
                        st.write(
                            "**Recommended action:**",
                            item["recommended_action"]
                        )
                        st.code(item["example_fix"])


        st.subheader("Detected Issues")

        
        st.subheader("Scan Comparison")

        if len(scans) >= 2:
            scan_ids = scans["scan_id"].tolist()

            previous_scan_id = st.selectbox(
                "Compare with previous scan",
                options=[sid for sid in scan_ids if sid != selected_scan],
                index=0,
                format_func=lambda sid: (
                    f"Scan #{sid} — "
                    f"{scans.loc[scans['scan_id'] == sid, 'scan_time'].iloc[0]}"
                ),
                key=f"compare_scan_{selected_scan}"
            )

            previous_issues = issues[
                issues["scan_id"] == previous_scan_id
            ]
            current_issues = issues[
                issues["scan_id"] == selected_scan
            ]

            previous_count = len(previous_issues)
            current_count = len(current_issues)

            col1, col2, col3 = st.columns(3)

            col1.metric(
                "Previous Scan Issues",
                previous_count
            )

            col2.metric(
                "Selected Scan Issues",
                current_count,
                delta=current_count - previous_count,
                delta_color="inverse"
            )

            col3.metric(
                "Issue Count Change",
                current_count - previous_count,
                delta=current_count - previous_count,
                delta_color="inverse"
            )

            # Compare issues using their type and target URL.
            previous_keys = set(
                zip(
                    previous_issues["issue_type"],
                    previous_issues["target_url"]
                )
            )

            current_keys = set(
                zip(
                    current_issues["issue_type"],
                    current_issues["target_url"]
                )
            )

            new_keys = current_keys - previous_keys
            missing_keys = previous_keys - current_keys

            st.write("**Newly detected issues**")

            if new_keys:
                new_df = current_issues[
                    current_issues.apply(
                        lambda row: (
                            row["issue_type"], row["target_url"]
                        ) in new_keys,
                        axis=1
                    )
                ]
                st.dataframe(new_df, use_container_width=True, hide_index=True)
            else:
                st.success("No newly detected issues.")

            st.write("**Issues no longer detected in the selected scan**")

            if missing_keys:
                missing_df = previous_issues[
                    previous_issues.apply(
                        lambda row: (
                            row["issue_type"], row["target_url"]
                        ) in missing_keys,
                        axis=1
                    )
                ]
                st.dataframe(
                    missing_df,
                    use_container_width=True,
                    hide_index=True
                )
                st.caption(
                    "These issues were not detected in the selected scan. "
                    "This does not automatically prove they were repaired."
                )
            else:
                st.info("No previous issues disappeared from the scan.")

        else:
            st.info(
                "Run at least two scans to compare website audit results."
            )


        if current_issues.empty:
            st.success("No issues were recorded for this scan.")
        else:
            issue_types = ["All"] + sorted(
                current_issues["issue_type"].dropna().unique().tolist()
            )
            chosen_type = st.selectbox("Filter by issue type", issue_types)

            filtered_issues = current_issues.copy()
            if chosen_type != "All":
                filtered_issues = filtered_issues[
                    filtered_issues["issue_type"] == chosen_type
                ]

            st.dataframe(
                filtered_issues,
                use_container_width=True,
                hide_index=True
            )

        
       
        st.subheader("Recheck Issue")

        if not current_issues.empty:
            issue_options = current_issues["issue_id"].tolist()

            selected_issue_id = st.selectbox(
                "Select issue to recheck",
                options=issue_options,
                format_func=lambda issue_id: (
                    f"#{issue_id} | "
                    f"{current_issues.loc[current_issues['issue_id'] == issue_id, 'issue_type'].iloc[0]} | "
                    f"{current_issues.loc[current_issues['issue_id'] == issue_id, 'target_url'].iloc[0]}"
                ),
                key=f"recheck_select_{selected_scan}"
            )

            if st.button(
                "Recheck Selected Issue",
                key=f"recheck_button_{selected_scan}"
            ):
                selected_issue = current_issues[
                    current_issues["issue_id"] == selected_issue_id
                ].iloc[0]

                with st.spinner("Rechecking URL and saving result..."):
                    try:
                        from recheck import recheck_issue, save_recheck

                        result = recheck_issue(
                            str(selected_issue["target_url"]),
                            str(selected_issue["issue_type"])
                        )

                        save_recheck(
                            int(selected_issue_id),
                            result
                        )

                        # Preserve the result across Streamlit refreshes.
                        st.session_state["last_recheck"] = {
                            "scan_id": int(selected_scan),
                            "issue_id": int(selected_issue_id),
                            "result": result
                        }

                        st.cache_data.clear()
                        st.rerun()

                    except Exception as error:
                        st.error(f"Recheck failed: {error}")

            # Display the saved result after rerun.
            saved_recheck = st.session_state.get("last_recheck")

            if (
                saved_recheck
                and saved_recheck["scan_id"] == int(selected_scan)
                and saved_recheck["issue_id"] == int(selected_issue_id)
            ):
                result = saved_recheck["result"]

                st.markdown("### Latest Recheck Result")

                col1, col2, col3 = st.columns(3)
                col1.metric(
                    "HTTP Status",
                    result["http_status"]
                    if result["http_status"] is not None
                    else "Unavailable"
                )
                col2.metric("Redirects", result["redirects"])
                col3.metric("Issue Status", result["issue_status"])

                st.write("**Target URL:**", result["target_url"])
                st.write("**Final URL:**", result["final_url"] or "Unavailable")
                st.write("**Checked at:**", result["checked_at"])

                if result["issue_status"] == "Resolved":
                    st.success("The issue has been resolved.")
                elif result["error"]:
                    st.warning(
                        "The recheck could not confirm resolution: "
                        + result["error"]
                    )
                else:
                    st.error("The issue remains open.")

        else:
            st.info("No issues are available to recheck for this scan.")

        st.subheader("Crawled Pages")
        st.dataframe(current_pages, use_container_width=True, hide_index=True)

        st.subheader("Issue Distribution")
        if not current_issues.empty:
            distribution = (
                current_issues.groupby("issue_type")
                .size()
                .reset_index(name="Count")
            )
            st.bar_chart(distribution, x="issue_type", y="Count")

        st.subheader("Export Results")
        if not current_issues.empty:
            st.download_button(
                "Download issues as CSV",
                data=current_issues.to_csv(index=False).encode("utf-8"),
                file_name=f"siteaudit_scan_{selected_scan}_issues.csv",
                mime="text/csv"
            )

        if not current_pages.empty:
            st.download_button(
                "Download pages as CSV",
                data=current_pages.to_csv(index=False).encode("utf-8"),
                file_name=f"siteaudit_scan_{selected_scan}_pages.csv",
                mime="text/csv"
            )

        if st.button("Refresh database data"):
            st.cache_data.clear()
            st.rerun()

    else:
        st.info("No scans found. Run crawler.py first.")

except Exception as error:
    st.error(f"Could not load dashboard data: {error}")
    st.info(
        "Check that MySQL Server is running and database.py "
        "contains the correct connection settings."
    )
