import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import json
import weasyprint
import google.generativeai as genai

# -----------------------------------------------------------------------------
# PAGE CONFIG & BRAND STYLING
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Enterprise Digital Audit Suite",
    page_icon="🔍",
    layout="wide"
)

st.markdown("""
<style>
    .main-header { font-size: 26px; font-weight: bold; color: #004b93; }
    .sub-header { font-size: 14px; color: #e85122; font-weight: 600; margin-bottom: 20px; }
    .stButton>button { background-color: #004b93; color: white; border-radius: 4px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">Connext Global Solutions — Enterprise Digital & AEO Auditor</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Zero-Cost Autonomous Forensic SEO, AEO & Architecture Platform</div>', unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# SIDEBAR: ZERO-COST API SECRETS
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Configuration")
    gemini_key = st.text_input("Google AI Studio API Key (Free)", type="password")
    pagespeed_key = st.text_input("Google Cloud PageSpeed Key (Free)", type="password")
    st.markdown("---")
    st.caption("All APIs used in this tool are free tiers with zero credit card requirements.")

# -----------------------------------------------------------------------------
# MAIN INTERFACE
# -----------------------------------------------------------------------------
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. Audit Target")
    target_url = st.text_input("Target Commercial URL", value="https://www.csgi.com")
    run_audit_btn = st.button("🚀 Run Live Technical Audit")

with col2:
    st.subheader("2. Supplemental Data Ingestion")
    uploaded_files = st.file_uploader(
        "Upload SerpApi / Colab CSVs (Rankings, Ads, AEO)",
        type=["csv"],
        accept_multiple_files=True
    )

# -----------------------------------------------------------------------------
# AUDIT EXECUTION ENGINE
# -----------------------------------------------------------------------------
if run_audit_btn:
    if not target_url:
        st.error("Please enter a target URL.")
    else:
        with st.spinner("Executing live technical extraction..."):
            audit_data = {}
            
            # A. Live HTML DOM Scraping
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                res = requests.get(target_url, headers=headers, timeout=12)
                soup = BeautifulSoup(res.text, "html.parser")
                
                h1_tags = [h.get_text(strip=True) for h in soup.find_all("h1")]
                schemas = [s.string for s in soup.find_all("script", type="application/ld+json") if s.string]
                
                # Check llms.txt
                base_domain = target_url.split("/")[0] + "//" + target_url.split("/")[2]
                llms_check = requests.get(f"{base_domain}/llms.txt", headers=headers, timeout=5)
                has_llms = (llms_check.status_code == 200)

                audit_data["h1_count"] = len(h1_tags)
                audit_data["h1_sample"] = h1_tags[0] if h1_tags else "MISSING"
                audit_data["schema_count"] = len(schemas)
                audit_data["has_llms_txt"] = has_llms
                
            except Exception as e:
                st.error(f"DOM Crawl Error: {e}")
                
            # B. Core Web Vitals (Free PageSpeed API)
            if pagespeed_key:
                try:
                    ps_endpoint = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={target_url}&strategy=mobile&key={pagespeed_key}"
                    ps_res = requests.get(ps_endpoint, timeout=25).json()
                    lighthouse = ps_res.get("lighthouseResult", {})
                    perf_score = int(lighthouse.get("categories", {}).get("performance", {}).get("score", 0) * 100)
                    tbt = lighthouse.get("audits", {}).get("total-blocking-time", {}).get("displayValue", "N/A")
                    lcp = lighthouse.get("audits", {}).get("largest-contentful-paint", {}).get("displayValue", "N/A")
                    
                    audit_data["perf_score"] = perf_score
                    audit_data["tbt"] = tbt
                    audit_data["lcp"] = lcp
                except Exception as e:
                    audit_data["perf_score"] = "API Error"
            else:
                audit_data["perf_score"] = "No API Key Provided"

            # Display Live Metrics
            st.success("✓ Live Audit Complete!")
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_col1.metric("H1 Tags Found", audit_data.get("h1_count", 0))
            m_col2.metric("Schemas Injected", audit_data.get("schema_count", 0))
            m_col3.metric("llms.txt Available?", "Yes" if audit_data.get("has_llms_txt") else "No (404)")
            m_col4.metric("Mobile Performance", f"{audit_data.get('perf_score', 'N/A')}/100")

            # Store in session state for report generation
            st.session_state["audit_data"] = audit_data

# -----------------------------------------------------------------------------
# PDF REPORT EXPORT
# -----------------------------------------------------------------------------
if "audit_data" in st.session_state:
    st.markdown("---")
    st.subheader("3. Executive Deliverable")
    
    if st.button("📄 Generate Connext Branded PDF"):
        with st.spinner("Compiling PDF report..."):
            data = st.session_state["audit_data"]
            
            html_template = f"""
            <html>
            <head>
            <style>
                @page {{ size: letter; margin: 0.8in; }}
                body {{ font-family: sans-serif; color: #1e293b; }}
                .brand-title {{ color: #004b93; font-size: 22px; font-weight: bold; border-bottom: 2px solid #e85122; padding-bottom: 8px; }}
                .stat-box {{ background: #f8fafc; border: 1px solid #e2e8f0; padding: 12px; margin: 15px 0; border-radius: 4px; }}
            </style>
            </head>
            <body>
                <div class="brand-title">Connext Global Solutions — Executive Performance Audit</div>
                <p><strong>Target URL:</strong> {target_url}</p>
                <div class="stat-box">
                    <h3>Technical Diagnostics</h3>
                    <ul>
                        <li><strong>Primary Heading (H1):</strong> {data.get('h1_sample')}</li>
                        <li><strong>Structured Data:</strong> {data.get('schema_count')} schemas detected</li>
                        <li><strong>AI Readability (llms.txt):</strong> {'Present' if data.get('has_llms_txt') else 'Missing (404 Error)'}</li>
                        <li><strong>Mobile Speed Score:</strong> {data.get('perf_score')}/100</li>
                    </ul>
                </div>
            </body>
            </html>
            """
            pdf_bytes = weasyprint.HTML(string=html_template).write_pdf()
            
            st.download_button(
                label="⬇️ Download Executive Audit PDF",
                data=pdf_bytes,
                file_name=f"audit_report_{target_url.replace('https://','').replace('/','')}.pdf",
                mime="application/pdf"
            )