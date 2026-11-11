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

# -----------------------------------------------------------------------------
# 1. PAGE CONFIGURATION & CONNEXT BRANDING
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Enterprise Digital & AEO Auditor",
    page_icon="🔍",
    layout="wide"
)

st.markdown("""
<style>
    .brand-header { font-size: 26px; font-weight: bold; color: #004b93; margin-bottom: 2px; }
    .brand-sub { font-size: 13px; color: #e85122; font-weight: 600; margin-bottom: 20px; }
    .stButton>button { background-color: #004b93; color: white; font-weight: bold; border-radius: 4px; }
    .metric-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="brand-header">Connext Global Solutions — Enterprise Digital & AEO Auditor</div>', unsafe_allow_html=True)
st.markdown('<div class="brand-sub">Multi-Page Autonomous Web Architecture, Schema DOM, CWV & AEO Benchmark Suite</div>', unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. API KEYS (READS FROM STREAMLIT SECRETS FIRST)
# -----------------------------------------------------------------------------
gemini_key = st.secrets.get("GEMINI_API_KEY", "")
pagespeed_key = st.secrets.get("PAGESPEED_API_KEY", "")

with st.sidebar:
    st.header("⚙️ Configuration")
    if gemini_key:
        st.success("✓ Gemini API Loaded")
    else:
        gemini_key = st.text_input("Gemini API Key (Free)", type="password")

    if pagespeed_key:
        st.success("✓ PageSpeed Key Loaded")
    else:
        pagespeed_key = st.text_input("PageSpeed API Key (Free)", type="password")

    st.markdown("---")
    crawl_limit = st.slider("Max Commercial Pages to Crawl", min_value=5, max_value=100, value=30, step=5)
    st.caption("Multi-threaded crawler runs concurrently without hitting timeouts.")

# -----------------------------------------------------------------------------
# 3. CORE MULTI-PAGE CRAWLING UTILITIES
# -----------------------------------------------------------------------------
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def discover_sitemap_urls(base_url, max_urls=30):
    """Finds URLs via sitemap.xml, sitemap_index.xml, or homepage fallback."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    found_urls = set()
    
    sitemap_candidates = [
        f"{root}/sitemap.xml",
        f"{root}/sitemap_index.xml",
        f"{root}/sitemap-products.xml"
    ]
    
    for sm in sitemap_candidates:
        try:
            r = requests.get(sm, headers=HEADERS, timeout=8)
            if r.status_code == 200 and ("xml" in r.headers.get("Content-Type", "") or "<urlset" in r.text or "<sitemapindex" in r.text):
                tree = ET.fromstring(r.content)
                # Handle standard sitemap namespaces
                for elem in tree.iter():
                    if elem.tag.endswith("loc") and elem.text:
                        loc = elem.text.strip()
                        if loc.endswith(".xml"):
                            # Nested sitemap
                            try:
                                sub_r = requests.get(loc, headers=HEADERS, timeout=6)
                                sub_tree = ET.fromstring(sub_r.content)
                                for sub_elem in sub_tree.iter():
                                    if sub_elem.tag.endswith("loc") and sub_elem.text:
                                        found_urls.add(sub_elem.text.strip())
                            except Exception:
                                continue
                        else:
                            found_urls.add(loc)
                if len(found_urls) >= max_urls:
                    break
        except Exception:
            continue

    # Fallback to homepage crawling if sitemap is absent/blocked
    if not found_urls:
        try:
            home_r = requests.get(base_url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(home_r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(base_url, a['href'])
                if parsed.netloc in href and not any(href.endswith(ext) for ext in ['.pdf', '.png', '.jpg', '.css', '.js']):
                    found_urls.add(href)
        except Exception:
            found_urls.add(base_url)

    # Prioritize commercial directory paths
    url_list = list(found_urls)
    commercial_prioritized = [u for u in url_list if any(p in u for p in ['/product', '/solution', '/industrie', '/service', '/lp'])]
    remaining = [u for u in url_list if u not in commercial_prioritized]
    
    final_urls = (commercial_prioritized + remaining)[:max_urls]
    return final_urls

def audit_single_page(url):
    """Audits one URL for H1 hierarchy, concatenated typography, and JSON-LD schemas."""
    record = {
        "URL": url,
        "Status": 0,
        "H1_Count": 0,
        "H1_Text": "N/A",
        "Concatenated_H1": False,
        "Total_Schemas": 0,
        "Commercial_Schema": False,
        "Schema_Types": "None",
        "Word_Count": 0
    }
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        record["Status"] = r.status_code
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            
            # 1. Heading Analysis
            h1s = [h.get_text(separator=" ", strip=True) for h in soup.find_all("h1")]
            record["H1_Count"] = len(h1s)
            if h1s:
                record["H1_Text"] = h1s[0]
                # Check for mashed words without spaces (e.g. poweredCPQ, Monetize4G)
                if re.search(r'[a-z][A-Z0-9]', h1s[0]):
                    record["Concatenated_H1"] = True

            # 2. Schema Analysis
            scripts = soup.find_all("script", type="application/ld+json")
            record["Total_Schemas"] = len(scripts)
            detected_types = []
            
            for s in scripts:
                if s.string:
                    try:
                        data = json.loads(s.string)
                        items = data if isinstance(data, list) else data.get("@graph", [data])
                        for it in items:
                            if isinstance(it, dict) and "@type" in it:
                                t = it["@type"]
                                detected_types.append(t)
                                if t in ["Product", "SoftwareApplication", "Service", "Organization"]:
                                    record["Commercial_Schema"] = True
                    except Exception:
                        continue
            
            record["Schema_Types"] = ", ".join(set(detected_types)) if detected_types else "None"
            
            # 3. Word Count
            body_text = soup.body.get_text(separator=" ", strip=True) if soup.body else ""
            record["Word_Count"] = len(body_text.split())
    except Exception:
        record["Status"] = 500
        
    return record

# -----------------------------------------------------------------------------
# 4. DASHBOARD INTERFACE
# -----------------------------------------------------------------------------
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("1. Domain Audit Setup")
    target_domain = st.text_input("Target Commercial Domain", value="https://www.csgi.com")
    run_btn = st.button("🚀 Run Site-Wide Multi-Page Audit")

with col2:
    st.subheader("2. Supplemental Data Ingestion")
    uploaded_files = st.file_uploader(
        "Upload Audit CSVs (Rankings, Google Ads, AEO, Cannibalization)",
        type=["csv"],
        accept_multiple_files=True
    )

# -----------------------------------------------------------------------------
# 5. EXECUTION & MULTI-PAGE AUDIT PIPELINE
# -----------------------------------------------------------------------------
if run_btn and target_domain:
    with st.spinner(f"Discovering XML sitemap and indexing up to {crawl_limit} commercial pages..."):
        # 1. Discover URLs
        discovered_urls = discover_sitemap_urls(target_domain, max_urls=crawl_limit)
        
        # 2. Check llms.txt at root
        parsed_root = f"{urlparse(target_domain).scheme}://{urlparse(target_domain).netloc}"
        try:
            llms_res = requests.get(f"{parsed_root}/llms.txt", headers=HEADERS, timeout=5)
            has_llms = (llms_res.status_code == 200)
        except Exception:
            has_llms = False

    with st.spinner(f"Crawling {len(discovered_urls)} leaf URLs concurrently across DOM, Headings, and Schemas..."):
        # 3. Multi-threaded leaf page crawl
        with ThreadPoolExecutor(max_workers=8) as executor:
            crawl_results = list(executor.map(audit_single_page, discovered_urls))

        df_crawl = pd.DataFrame(crawl_results)
        st.session_state["df_crawl"] = df_crawl
        st.session_state["has_llms"] = has_llms
        st.session_state["target_domain"] = target_domain

        # 4. Mobile Core Web Vitals on Homepage (PageSpeed API)
        if pagespeed_key:
            try:
                ps_url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={target_domain}&strategy=mobile&key={pagespeed_key}"
                ps_data = requests.get(ps_url, timeout=25).json()
                lh = ps_data.get("lighthouseResult", {})
                st.session_state["perf_score"] = int(lh.get("categories", {}).get("performance", {}).get("score", 0) * 100)
                st.session_state["tbt"] = lh.get("audits", {}).get("total-blocking-time", {}).get("displayValue", "N/A")
            except Exception:
                st.session_state["perf_score"] = "N/A"
                st.session_state["tbt"] = "N/A"
        else:
            st.session_state["perf_score"] = "N/A"
            st.session_state["tbt"] = "N/A"

    st.success(f"✓ Crawl Complete! Analyzed {len(df_crawl)} commercial URLs.")

# -----------------------------------------------------------------------------
# 6. RESULTS & ENTERPRISE AGGREGATES DISPLAY
# -----------------------------------------------------------------------------
if "df_crawl" in st.session_state:
    df_crawl = st.session_state["df_crawl"]
    total_pages = len(df_crawl)
    zero_schema_pct = (df_crawl["Total_Schemas"] == 0).sum() / total_pages * 100
    commercial_schema_pct = df_crawl["Commercial_Schema"].sum() / total_pages * 100
    missing_h1_pct = (df_crawl["H1_Count"] == 0).sum() / total_pages * 100
    concatenated_count = df_crawl["Concatenated_H1"].sum()

    st.markdown("### 📊 Enterprise Domain Aggregates")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Pages Audited", total_pages)
    m2.metric("Commercial Schemas", f"{commercial_schema_pct:.1f}%")
    m3.metric("Missing H1 Headings", f"{missing_h1_pct:.1f}%")
    m4.metric("Concatenated H1s", f"{concatenated_count} pages")
    m5.metric("llms.txt Standard", "Active" if st.session_state.get("has_llms") else "Missing (404)")

    # Data Table
    st.markdown("#### Crawled Pages Audit Inventory")
    st.dataframe(
        df_crawl[["URL", "H1_Count", "Concatenated_H1", "Total_Schemas", "Commercial_Schema", "Schema_Types", "Word_Count"]],
        use_container_width=True
    )

    # Ingested Files Summary
    if uploaded_files:
        st.markdown("---")
        st.markdown("### 📁 Ingested Forensic Datasets")
        for up_file in uploaded_files:
            up_df = pd.read_csv(up_file)
            st.write(f"**Dataset: `{up_file.name}`** ({len(up_df)} records)")
            st.dataframe(up_df.head(4), use_container_width=True)

    # -------------------------------------------------------------------------
    # 7. GENERATE COMPREHENSIVE PDF DELIVERABLE
    # -------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("3. Executive Deliverable Generation")
    
    if st.button("📄 Generate Connext Branded Enterprise PDF"):
        with st.spinner("Compiling multi-page PDF audit..."):
            
            # Build HTML table rows of crawled pages
            table_rows = ""
            for _, r in df_crawl.head(25).iterrows():
                table_rows += f"""
                <tr>
                    <td style="word-break: break-all;">{r['URL']}</td>
                    <td>{r['H1_Count']}</td>
                    <td>{'⚠️ Mashed' if r['Concatenated_H1'] else 'Clean'}</td>
                    <td>{r['Total_Schemas']}</td>
                    <td>{'✓ Active' if r['Commercial_Schema'] else '🔴 None'}</td>
                    <td>{r['Word_Count']}</td>
                </tr>
                """
            
            pdf_html = f"""
            <html>
            <head>
            <style>
                @page {{ size: letter; margin: 0.6in; }}
                body {{ font-family: 'DejaVu Sans', sans-serif; color: #1e293b; font-size: 8.5pt; line-height: 1.4; }}
                .header-bar {{ border-bottom: 3px solid #004b93; padding-bottom: 8px; margin-bottom: 12px; }}
                .brand-title {{ color: #004b93; font-size: 18pt; font-weight: bold; margin: 0; }}
                .brand-sub {{ color: #e85122; font-size: 10pt; font-weight: bold; }}
                .card-grid {{ display: flex; gap: 8px; margin: 12px 0; }}
                .card {{ flex: 1; background: #f8fafc; border: 1px solid #e2e8f0; border-top: 3px solid #004b93; padding: 8px; text-align: center; border-radius: 4px; }}
                .card-val {{ font-size: 14pt; font-weight: bold; color: #004b93; }}
                .card-lbl {{ font-size: 7pt; color: #64748b; text-transform: uppercase; font-weight: bold; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 7.5pt; }}
                th {{ background: #004b93; color: white; padding: 5px; border: 1px solid #003a73; }}
                td {{ padding: 4px 6px; border: 1px solid #e2e8f0; }}
                tr:nth-child(even) td {{ background: #f8fafc; }}
            </style>
            </head>
            <body>
                <div class="header-bar">
                    <div class="brand-title">Connext Global Solutions — Enterprise Digital Audit</div>
                    <div class="brand-sub">Multi-Page Architecture, Structured Schema, and Performance Intelligence</div>
                    <p style="margin: 4px 0 0 0; font-size: 8pt; color: #64748b;">Target Domain: <strong>{st.session_state['target_domain']}</strong> | Sample Depth: <strong>{total_pages} Pages</strong></p>
                </div>

                <div class="card-grid">
                    <div class="card">
                        <div class="card-val">{total_pages}</div>
                        <div class="card-lbl">Pages Audited</div>
                    </div>
                    <div class="card">
                        <div class="card-val">{commercial_schema_pct:.1f}%</div>
                        <div class="card-lbl">Commercial Schemas</div>
                    </div>
                    <div class="card">
                        <div class="card-val">{missing_h1_pct:.1f}%</div>
                        <div class="card-lbl">Missing H1 Headings</div>
                    </div>
                    <div class="card">
                        <div class="card-val">{concatenated_count}</div>
                        <div class="card-lbl">Mashed Headings</div>
                    </div>
                    <div class="card">
                        <div class="card-val">{'Active' if st.session_state.get('has_llms') else '404'}</div>
                        <div class="card-lbl">llms.txt Standard</div>
                    </div>
                </div>

                <h3 style="color: #004b93; margin-top: 14px;">Multi-Page Forensic Inventory (Sample)</h3>
                <table>
                    <thead>
                        <tr>
                            <th style="width: 45%;">URL</th>
                            <th style="width: 10%;">H1 Count</th>
                            <th style="width: 12%;">H1 Typography</th>
                            <th style="width: 10%;">Schemas</th>
                            <th style="width: 13%;">Commercial Schema</th>
                            <th style="width: 10%;">Words</th>
                        </tr>
                    </thead>
                    <tbody>
                        {table_rows}
                    </tbody>
                </table>
            </body>
            </html>
            """
            pdf_bytes = weasyprint.HTML(string=pdf_html).write_pdf()
            
            st.download_button(
                label="⬇️ Download Multi-Page Audit Report (PDF)",
                data=pdf_bytes,
                file_name="connext_multipage_enterprise_audit.pdf",
                mime="application/pdf"
            )
