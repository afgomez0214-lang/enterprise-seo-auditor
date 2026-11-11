import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import weasyprint
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor
import re
import json
import os
import google.generativeai as genai

# -----------------------------------------------------------------------------
# 1. PAGE SETUP & OFFICIAL CONNEXT BRAND STYLING
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Connext Enterprise Digital & AEO Auditor",
    page_icon="🔍",
    layout="wide"
)

# Connext Brand Colors: Deep Blue (#004b93), Orange (#e85122), Gold (#f2a900), Slate (#0f172a)
st.markdown("""
<style>
    .brand-bar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 3.5px solid #004b93;
        padding-bottom: 12px;
        margin-bottom: 20px;
    }
    .brand-title {
        color: #004b93;
        font-size: 24px;
        font-weight: 800;
        margin: 0;
    }
    .brand-sub {
        color: #e85122;
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.5px;
        margin-top: 2px;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 4px 4px 0 0;
        padding: 8px 16px;
        font-weight: 600;
        color: #1e293b;
    }
    .stTabs [aria-selected="true"] {
        background-color: #004b93 !important;
        color: white !important;
    }
    .stButton>button {
        background-color: #004b93;
        color: white;
        font-weight: bold;
        border-radius: 4px;
        border: none;
    }
    .stButton>button:hover {
        background-color: #e85122;
        color: white;
    }
    .metric-box {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-left: 4px solid #004b93;
        padding: 10px 14px;
        border-radius: 4px;
        margin-bottom: 10px;
    }
</style>
""", unsafe_allow_html=True)

# Header with Logo
logo_path = "logo.png"
col_logo, col_text = st.columns([1.5, 6])
with col_logo:
    if os.path.exists(logo_path):
        st.image(logo_path, width=220)
    else:
        st.markdown("<h2 style='color:#004b93; margin:0;'>connext</h2>", unsafe_allow_html=True)
with col_text:
    st.markdown('<div class="brand-title">Enterprise Digital Performance, AEO & Search Intelligence</div>', unsafe_allow_html=True)
    st.markdown('<div class="brand-sub">CONNEXT GLOBAL SOLUTIONS — PROPRIETARY AUDIT PLATFORM</div>', unsafe_allow_html=True)

st.markdown("---")

# -----------------------------------------------------------------------------
# 2. SECRETS & API MANAGEMENT
# -----------------------------------------------------------------------------
serpapi_key = st.secrets.get("SERPAPI_KEY", "")
gemini_key = st.secrets.get("GEMINI_API_KEY", "")
pagespeed_key = st.secrets.get("PAGESPEED_API_KEY", "")

with st.sidebar:
    if os.path.exists(logo_path):
        st.image(logo_path, width=180)
    st.header("⚙️ API Configuration")
    
    # SerpApi
    if serpapi_key:
        st.success("✓ SerpApi Connected (SERP/AEO/Ads)")
    else:
        serpapi_key = st.text_input("SerpApi Key (SERP/AEO/Ads)", type="password")
        
    # Gemini API
    if gemini_key:
        st.success("✓ Gemini AI Connected")
    else:
        gemini_key = st.text_input("Gemini API Key (AI Studio)", type="password")
        
    # PageSpeed API
    if pagespeed_key:
        st.success("✓ Google PageSpeed Connected")
    else:
        pagespeed_key = st.text_input("Google PageSpeed Key", type="password")

    st.markdown("---")
    st.subheader("Crawl Settings")
    crawl_limit = st.slider("Max Commercial Pages", min_value=5, max_value=80, value=25, step=5)
    st.caption("Powered by multi-threaded asynchronous requests.")

# Initialize Session State
if "df_crawl" not in st.session_state:
    st.session_state["df_crawl"] = pd.DataFrame()
if "df_rankings" not in st.session_state:
    st.session_state["df_rankings"] = pd.DataFrame()
if "df_aeo" not in st.session_state:
    st.session_state["df_aeo"] = pd.DataFrame()
if "df_ads" not in st.session_state:
    st.session_state["df_ads"] = pd.DataFrame()

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# -----------------------------------------------------------------------------
# 3. WORKSPACE TABS
# -----------------------------------------------------------------------------
tab_arch, tab_serp, tab_aeo, tab_ads, tab_report = st.tabs([
    "🌐 1. Architecture & Schemas",
    "📈 2. Organic Keyword SERP",
    "🤖 3. Generative AEO & AI Overviews",
    "🎯 4. Paid Search & Ads Intel",
    "📄 5. Connext Branded PDF Deliverable"
])

# =============================================================================
# TAB 1: ARCHITECTURE, SCHEMAS & SITEMAP CRAWLER
# =============================================================================
with tab_arch:
    st.subheader("Sitemap Discovery, Heading Typography & Structured Schemas")
    c1, c2 = st.columns([3, 1])
    with c1:
        target_site = st.text_input("Target Domain", value="https://www.csgi.com")
    with c2:
        st.write("")
        st.write("")
        crawl_btn = st.button("🚀 Start Multi-Page Audit", use_container_width=True)

    if crawl_btn and target_site:
        with st.spinner("Discovering sitemap and auditing pages concurrently..."):
            parsed = urlparse(target_site)
            root = f"{parsed.scheme}://{parsed.netloc}"
            
            # Check llms.txt standard
            try:
                llms_r = requests.get(f"{root}/llms.txt", headers=HEADERS, timeout=5)
                has_llms = (llms_r.status_code == 200)
            except Exception:
                has_llms = False
            st.session_state["has_llms"] = has_llms
            st.session_state["target_domain"] = target_site

            # Discover URLs
            found_urls = set()
            for sm in [f"{root}/sitemap.xml", f"{root}/sitemap_index.xml", f"{root}/sitemap-products.xml"]:
                try:
                    r = requests.get(sm, headers=HEADERS, timeout=6)
                    if r.status_code == 200 and ("xml" in r.headers.get("Content-Type", "") or "<urlset" in r.text):
                        tree = ET.fromstring
