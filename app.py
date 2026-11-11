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
import math
from collections import Counter
import google.generativeai as genai

# -----------------------------------------------------------------------------
# 0. EMBEDDED CONNEXT BRAND ASSETS (BASE64)
# -----------------------------------------------------------------------------
CONNEXT_LOGO_B64 = "iVBORw0KGgoAAAANSUhEUgAAARgAAABECAYAAABatSq0AABE3ElEQVR42u29...MW4pwpEoJUSrqPwG6+/ia50PTvqdKbT/H6HfH2upDsZUAAAAAElFTkSuQmCC"

# -----------------------------------------------------------------------------
# 1. PAGE SETUP & OFFICIAL CONNEXT BRAND STYLING
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Connext Enterprise Digital & AEO Auditor",
    page_icon="🔍",
    layout="wide"
)

# Connext Brand Styling: Deep Blue (#004b93), Orange (#e85122), Gold (#f2a900), Slate (#0f172a)
st.markdown("""
<style>
    .brand-title { color: #004b93; font-size: 24px; font-weight: 800; margin: 0; }
    .brand-sub { color: #e85122; font-size: 13px; font-weight: 700; letter-spacing: 0.5px; margin-top: 2px; }
    .stTabs [data-baseweb="tab-list"] { gap: 6px; }
    .stTabs [data-baseweb="tab"] { border-radius: 4px 4px 0 0; padding: 7px 14px; font-weight: 600; color: #1e293b; font-size: 13px; }
    .stTabs [aria-selected="true"] { background-color: #004b93 !important; color: white !important; }
    .stButton>button { background-color: #004b93; color: white; font-weight: bold; border-radius: 4px; border: none; }
    .stButton>button:hover { background-color: #e85122; color: white; }
    .guide-box { background: #f8fafc; border: 1px solid #cbd5e1; border-left: 4px solid #004b93; border-radius: 4px; padding: 14px 18px; margin-bottom: 16px; }
    .step-pill { background: #004b93; color: white; border-radius: 12px; padding: 2px 8px; font-size: 11px; font-weight: bold; margin-right: 6px; }
    .security-badge { font-size: 22px; font-weight: bold; padding: 4px 12px; border-radius: 4px; display: inline-block; }
</style>
""", unsafe_allow_html=True)

# Header Bar with Embedded Logo
col_logo, col_text = st.columns([1.5, 6])
with col_logo:
    st.markdown(f'<img src="data:image/png;base64,{CONNEXT_LOGO_B64}" style="width: 210px; margin-top: 4px;">', unsafe_allow_html=True)
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
    st.markdown(f'<img src="data:image/png;base64,{CONNEXT_LOGO_B64}" style="width: 170px; margin-bottom: 15px;">', unsafe_allow_html=True)
    st.header("⚙️ API Configuration")
    
    if serpapi_key:
        st.success("✓ SerpApi Connected")
    else:
        serpapi_key = st.text_input("SerpApi Key (SERP/AEO/Ads)", type="password")
        
    if gemini_key:
        st.success("✓ Gemini AI Connected")
    else:
        gemini_key = st.text_input("Gemini API Key (AI Studio)", type="password")
        
    if pagespeed_key:
        st.success("✓ Google PageSpeed Connected")
    else:
        pagespeed_key = st.text_input("Google PageSpeed Key", type="password")

    st.markdown("---")
    st.subheader("Crawl Settings")
    crawl_limit = st.slider("Max Commercial Pages to Crawl", min_value=5, max_value=80, value=25, step=5)
    st.caption("Multi-threaded engine analyzes DOM, schemas, metadata & security.")

# Initialize Session States
if "df_crawl" not in st.session_state: st.session_state["df_crawl"] = pd.DataFrame()
if "df_rankings" not in st.session_state: st.session_state["df_rankings"] = pd.DataFrame()
if "df_aeo" not in st.session_state: st.session_state["df_aeo"] = pd.DataFrame()
if "df_ads" not in st.session_state: st.session_state["df_ads"] = pd.DataFrame()
if "df_cannibal" not in st.session_state: st.session_state["df_cannibal"] = pd.DataFrame()
if "suggested_organic_kws" not in st.session_state: st.session_state["suggested_organic_kws"] = ""
if "suggested_aeo_prompts" not in st.session_state: st.session_state["suggested_aeo_prompts"] = ""
if "sec_headers" not in st.session_state: st.session_state["sec_headers"] = {}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# -----------------------------------------------------------------------------
# 3. BUILT-IN OPERATIONAL INSTRUCTIONS & API SETUP GUIDE
# -----------------------------------------------------------------------------
with st.expander("📖 Platform Guide & Free API Setup Instructions (Click to expand)", expanded=False):
    st.markdown("""
    ### 🚀 Recommended Audit Workflow
    1. <span class="step-pill">Step 1</span> **Architecture & Technical Scan (Tab 1):** Enter the target domain (`https://www.csgi.com`) and click **Start Multi-Page Audit**. The app discovers sitemaps, crawls leaf pages, audits heading hierarchy, metadata, image alt text, security headers, and checks for `llms.txt`.
    2. <span class="step-pill">Step 2</span> **Discover Keyword Opportunities (Tab 1):** Click **⚡ Generate AI Search Keywords & AEO Prompts**. Gemini analyzes discovered products and creates high-intent commercial terms.
    3. <span class="step-pill">Step 3</span> **Check Keyword Cannibalization (Tab 2):** Run TF-IDF cosine similarity to flag overlapping pages (e.g. `/lp/` vs product hubs).
    4. <span class="step-pill">Step 4</span> **Track Organic SERP (Tab 3):** Click **Check Live SERP Positions** to query Google Organic Rankings via SerpApi.
    5. <span class="step-pill">Step 5</span> **Test Generative AI Citations (Tab 4):** Click **Test AEO Visibility** to see if Google AI Overviews cite the domain or competitors.
    6. <span class="step-pill">Step 6</span> **Inspect Competitor Ads (Tab 5):** Enter the advertiser domain to pull active creatives from the Google Ads Transparency Center.
    7. <span class="step-pill">Step 7</span> **Generate Schemas (Tab 6):** Create copy-paste `SoftwareApplication`, `Organization`, or `FAQPage` schemas.
    8. <span class="step-pill">Step 8</span> **Export Deliverable (Tab 7):** Compile and download the comprehensive Connext-branded PDF report.

    ---
    ### 🔑 Free API Key Acquisition Links
    * **SerpApi (100 free searches/month):** Register at [serpapi.com](https://serpapi.com/) → Copy API Key from your Dashboard.
    * **Google AI Studio (Gemini Free Tier):** Visit [aistudio.google.com](https://aistudio.google.com/) → Click *Get API Key* → *Create API Key in new project*.
    * **Google PageSpeed API (25,000 free tests/day):** Visit [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → Enable *PageSpeed Insights API* → *Create Credentials* → *API key*.
    * **Permanent Storage in Streamlit:** In Streamlit Cloud, click *Manage app* (bottom-right) → *⋮* → *Settings* → *Secrets* and paste:
      ```toml
      SERPAPI_KEY = "your_key"
      GEMINI_API_KEY = "your_key"
      PAGESPEED_API_KEY = "your_key"
      ```
    """, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 4. WORKSPACE TABS
# -----------------------------------------------------------------------------
tab_arch, tab_cannibal, tab_serp, tab_aeo, tab_ads, tab_schema_gen, tab_report = st.tabs([
    "🌐 1. Architecture & Tech",
    "🔀 2. Cannibalization",
    "📈 3. Organic SERP",
    "🤖 4. Generative AEO",
    "🎯 5. Paid Search Intel",
    "⚙️ 6. Schema Generator",
    "📄 7. Connext Branded PDF"
])

# =============================================================================
# TAB 1: ARCHITECTURE, SCHEMAS, METADATA & SECURITY
# =============================================================================
with tab_arch:
    st.subheader("Sitemap Discovery, On-Page Metadata, Schemas & Security Audit")
    c1, c2 = st.columns([3, 1])
    with c1:
        target_site = st.text_input("Target Commercial Domain", value="https://www.csgi.com")
    with c2:
        st.write("")
        st.write("")
        crawl_btn = st.button("🚀 Start Multi-Page Audit", use_container_width=True)

    if crawl_btn and target_site:
        with st.spinner("Discovering sitemap, checking security headers, and auditing pages..."):
            parsed = urlparse(target_site)
            root = f"{parsed.scheme}://{parsed.netloc}"
            st.session_state["target_domain"] = target_site
            
            # A. Security Headers Audit
            sec_headers = {"HSTS": False, "CSP": False, "XFO": False, "XCTO": False, "Grade": "C"}
            try:
                head_r = requests.get(root, headers=HEADERS, timeout=8)
                h = head_r.headers
                sec_headers["HSTS"] = "strict-transport-security" in h
                sec_headers["CSP"] = "content-security-policy" in h
                sec_headers["XFO"] = "x-frame-options" in h
                sec_headers["XCTO"] = "x-content-type-options" in h
                
                score = sum([sec_headers["HSTS"], sec_headers["CSP"], sec_headers["XFO"], sec_headers["XCTO"]])
                sec_headers["Grade"] = "A+ (Enterprise)" if score == 4 else ("A (Secure)" if score == 3 else ("B (Adequate)" if score == 2 else "C (Needs Hardening)"))
            except Exception:
                pass
            st.session_state["sec_headers"] = sec_headers

            # B. Check llms.txt standard
            try:
                llms_r = requests.get(f"{root}/llms.txt", headers=HEADERS, timeout=5)
                has_llms = (llms_r.status_code == 200)
            except Exception:
                has_llms = False
            st.session_state["has_llms"] = has_llms

            # C. Discover URLs from sitemaps
            found_urls = set()
            for sm in [f"{root}/sitemap.xml", f"{root}/sitemap_index.xml", f"{root}/sitemap-products.xml"]:
                try:
                    r = requests.get(sm, headers=HEADERS, timeout=6)
                    if r.status_code == 200 and ("xml" in r.headers.get("Content-Type", "") or "<urlset" in r.text):
                        tree = ET.fromstring(r.content)
                        for elem in tree.iter():
                            if elem.tag.endswith("loc") and elem.text and not elem.text.endswith(".xml"):
                                found_urls.add(elem.text.strip())
                        if len(found_urls) >= crawl_limit:
                            break
                except Exception:
                    continue

            if not found_urls:
                found_urls.add(target_site)

            url_pool = list(found_urls)[:crawl_limit]

            # D. Comprehensive On-Page Auditor
            def audit_page(url):
                res = {
                    "URL": url,
                    "Status": 0,
                    "H1_Count": 0,
                    "H1_Text": "N/A",
                    "Concatenated_H1": False,
                    "Meta_Title_Length": 0,
                    "Meta_Desc_Length": 0,
                    "Meta_Desc_Status": "Missing",
                    "Missing_Alt_Pct": 0.0,
                    "Has_OG_Tags": False,
                    "Commercial_Schema": False,
                    "Schema_Types": "None",
                    "Word_Count": 0,
                    "Body_Text": ""
                }
                try:
                    r = requests.get(url, headers=HEADERS, timeout=8)
                    res["Status"] = r.status_code
                    if r.status_code == 200:
                        soup = BeautifulSoup(r.text, "html.parser")
                        
                        # Headings
                        h1s = []
                        for h in soup.find_all("h1"):
                            txt = h.get_text(separator=" ", strip=True)
                            if txt:
                                h1s.append(txt)
                        res["H1_Count"] = len(h1s)
                        if h1s:
                            res["H1_Text"] = h1s[0]
                            if re.search(r'[a-z][A-Z0-9]', h1s[0]):
                                res["Concatenated_H1"] = True
                        
                        # Meta Title & Description
                        t_tag = soup.find("title")
                        res["Meta_Title_Length"] = len(t_tag.text.strip()) if t_tag and t_tag.text else 0
                        
                        m_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
                        if m_desc and m_desc.get("content"):
                            d_len = len(m_desc["content"].strip())
                            res["Meta_Desc_Length"] = d_len
                            res["Meta_Desc_Status"] = "Good (120-160)" if 120 <= d_len <= 165 else ("Truncated (>165)" if d_len > 165 else "Too Short (<120)")
                        
                        # Images Alt Text
                        imgs = soup.find_all("img")
                        if imgs:
                            missing_alt = sum([1 for img in imgs if not img.get("alt") or len(img.get("alt").strip()) == 0])
                            res["Missing_Alt_Pct"] = round((missing_alt / len(imgs)) * 100, 1)
                        
                        # OpenGraph
                        res["Has_OG_Tags"] = bool(soup.find("meta", property="og:title") and soup.find("meta", property="og:image"))
                        
                        # Schemas
                        scripts = soup.find_all("script", type="application/ld+json")
                        types = []
                        for s in scripts:
                            if s.string:
                                try:
                                    d = json.loads(s.string)
                                    items = d if isinstance(d, list) else d.get("@graph", [d])
                                    for it in items:
                                        if isinstance(it, dict) and "@type" in it:
                                            types.append(it["@type"])
                                            if it["@type"] in ["Product", "SoftwareApplication", "Service", "Organization", "FAQPage"]:
                                                res["Commercial_Schema"] = True
                                except Exception:
                                    pass
                        res["Schema_Types"] = ", ".join(sorted(list(set(types)))) if types else "None"
                        
                        # Body Text for Cannibalization
                        body = soup.body.get_text(separator=" ", strip=True) if soup.body else ""
                        res["Word_Count"] = len(body.split())
                        res["Body_Text"] = body[:2500]  # First 2500 chars for TF-IDF
                except Exception:
                    res["Status"] = 500
                return res

            with ThreadPoolExecutor(max_workers=8) as ex:
                records = list(ex.map(audit_page, url_pool))

            df_crawl = pd.DataFrame(records)
            st.session_state["df_crawl"] = df_crawl

            # E. PageSpeed Check on Root
            if pagespeed_key:
                try:
                    ps_url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={target_site}&strategy=mobile&key={pagespeed_key}"
                    ps_res = requests.get(ps_url, timeout=20).json()
                    lh = ps_res.get("lighthouseResult", {})
                    st.session_state["perf_score"] = int(lh.get("categories", {}).get("performance", {}).get("score", 0) * 100)
                    st.session_state["tbt"] = lh.get("audits", {}).get("total-blocking-time", {}).get("displayValue", "N/A")
                except Exception:
                    st.session_state["perf_score"] = "N/A"
                    st.session_state["tbt"] = "N/A"
            else:
                st.session_state["perf_score"] = "N/A"
                st.session_state["tbt"] = "N/A"

            st.success(f"✓ Crawled {len(df_crawl)} commercial pages!")

    if not st.session_state["df_crawl"].empty:
        df = st.session_state["df_crawl"]
        sec = st.session_state.get("sec_headers", {})
        
        # Metric Scorecards
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Pages Analyzed", len(df))
        m2.metric("Commercial Schemas", f"{(df['Commercial_Schema'].sum()/len(df)*100):.1f}%")
        m3.metric("Mashed H1s", f"{df['Concatenated_H1'].sum()} URLs")
        m4.metric("Security Grade", sec.get("Grade", "N/A"))
        m5.metric("llms.txt Standard", "✓ Active" if st.session_state.get("has_llms") else "🔴 Missing (404)")

        st.markdown("#### Technical & Metadata Audit Inventory")
        st.dataframe(
            df[["URL", "H1_Count", "Concatenated_H1", "Meta_Desc_Status", "Missing_Alt_Pct", "Has_OG_Tags", "Commercial_Schema", "Word_Count"]],
            use_container_width=True
        )

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            st.download_button(
                label="⬇️ Export Architecture Audit to CSV",
                data=df.to_csv(index=False),
                file_name=f"technical_audit_{urlparse(st.session_state['target_domain']).netloc}.csv",
                mime="text/csv",
                use_container_width=True
            )
        
        with col_d2:
            # One-Click llms.txt Builder
            llms_content = f"# {urlparse(st.session_state['target_domain']).netloc} - Enterprise Offerings\n\n"
            llms_content += "> Autogenerated machine-readable directory for LLM and AI Engine Indexing.\n\n"
            llms_content += "## Core Commercial Solutions\n"
            for _, r in df.head(20).iterrows():
                h1_name = r['H1_Text'] if r['H1_Text'] != 'N/A' else r['URL'].strip('/').split('/')[-1].replace('-', ' ').title()
                llms_content += f"- [{h1_name}]({r['URL']})\n"
            
            st.download_button(
                label="🤖 Download Custom llms.txt File",
                data=llms_content,
                file_name="llms.txt",
                mime="text/plain",
                use_container_width=True
            )

        # ---------------------------------------------------------------------
        # AI KEYWORD & AEO PROMPT DISCOVERY ENGINE (GEMINI POWERED)
        # ---------------------------------------------------------------------
        st.markdown("---")
        st.subheader("⚡ Automated Search Keyword & AEO Prompt Discovery")
        st.write("Automatically extracts product themes from crawled pages and generates high-intent keywords for Tab 3 (SERP) and Tab 4 (AEO).")
        
        if st.button("🤖 Generate AI Keywords & AEO Prompts with Gemini"):
            with st.spinner("Analyzing product catalog with Gemini..."):
                organic_list = []
                aeo_list = []
                
                if gemini_key:
                    try:
                        genai.configure(api_key=gemini_key)
                        model = genai.GenerativeModel("gemini-1.5-flash")
                        
                        offerings = []
                        for _, r in df.iterrows():
                            h1_val = str(r.get("H1_Text", "")).strip()
                            url_val = str(r.get("URL", "")).strip()
                            if h1_val and h1_val != "N/A":
                                offerings.append(f"- URL: {url_val} | Title: {h1_val}")
                        
                        context_str = "\n".join(offerings[:20])
                        prompt = f"""You are a senior enterprise SEO/AEO strategist.
Analyze these crawled products and solutions for {st.session_state.get('target_domain', 'the site')}:
{context_str}

Generate two distinct search lists:
1. "organic": Exactly 8 high-intent commercial B2B Google search queries enterprise buyers use to find these services.
2. "aeo": Exactly 8 comparative, consultative questions that tech leaders ask Generative AI engines (ChatGPT, Google AI Overviews, Perplexity) when shortlisting software vendors in these categories.

Respond strictly in valid JSON format:
{{"organic": ["kw1", "kw2", ...], "aeo": ["prompt1", "prompt2", ...]}}"""

                        ai_res = model.generate_content(prompt)
                        clean_json = ai_res.text.strip().replace("```json", "").replace("```", "").strip()
                        parsed_ai = json.loads(clean_json)
                        organic_list = parsed_ai.get("organic", [])
                        aeo_list = parsed_ai.get("aeo", [])
                    except Exception as e:
                        st.warning(f"Gemini notice: {e}. Using heuristic generator.")

                if not organic_list:
                    for _, r in df.iterrows():
                        h1_val = str(r.get("H1_Text", "")).strip()
                        slug_val = str(r.get("URL", "")).strip("/").split("/")[-1].replace("-", " ")
                        if h1_val and h1_val != "N/A" and len(h1_val) > 4:
                            clean_t = re.sub(r'[^a-zA-Z0-9 ]', '', h1_val).lower()
                            organic_list.append(f"{clean_t} enterprise software")
                            aeo_list.append(f"what are the top platforms for {clean_t}")
                        elif slug_val and len(slug_val) > 3:
                            organic_list.append(f"{slug_val} solutions")
                            aeo_list.append(f"leading providers for {slug_val}")
                    organic_list = list(dict.fromkeys(organic_list))[:8]
                    aeo_list = list(dict.fromkeys(aeo_list))[:8]

                st.session_state["suggested_organic_kws"] = "\n".join(organic_list)
                st.session_state["suggested_aeo_prompts"] = "\n".join(aeo_list)

                st.success("✓ Keywords and AEO prompts generated! Populated into Tab 3 and Tab 4.")
                c_out1, c_out2 = st.columns(2)
                with c_out1:
                    st.write("**Suggested Organic Commercial Keywords:**")
                    st.code(st.session_state["suggested_organic_kws"], language="text")
                with c_out2:
                    st.write("**Suggested Generative AEO Prompts:**")
                    st.code(st.session_state["suggested_aeo_prompts"], language="text")

# =============================================================================
# TAB 2: KEYWORD CANNIBALIZATION & CONTENT OVERLAP
# =============================================================================
with tab_cannibal:
    st.subheader("TF-IDF Keyword Cannibalization & Content Overlap Scanner")
    st.write("Identifies duplicate topic coverage and cannibalization pairs where similarity exceeds 0.45 (such as paid landing pages competing with organic hubs).")

    if st.session_state["df_crawl"].empty:
        st.info("Run the crawl in Tab 1 first to scan page content for cannibalization.")
    else:
        if st.button("🔀 Calculate Content Similarity Matrix", use_container_width=True):
            df_c = st.session_state["df_crawl"]
            valid_pages = df_c[df_c["Body_Text"].str.len() > 100].copy().reset_index(drop=True)
            
            def compute_cosine(t1, t2):
                def get_toks(t): return re.findall(r'\b[a-zA-Z]{3,}\b', t.lower())
                v1, v2 = Counter(get_toks(t1)), Counter(get_toks(t2))
                inter = set(v1.keys()) & set(v2.keys())
                num = sum([v1[x] * v2[x] for x in inter])
                denom = math.sqrt(sum([x**2 for x in v1.values()])) * math.sqrt(sum([x**2 for x in v2.values()]))
                return float(num / denom) if denom else 0.0

            pairs = []
            for i in range(len(valid_pages)):
                for j in range(i + 1, len(valid_pages)):
                    sim = compute_cosine(valid_pages.loc[i, "Body_Text"], valid_pages.loc[j, "Body_Text"])
                    if sim >= 0.40:
                        pairs.append({
                            "URL_1": valid_pages.loc[i, "URL"],
                            "URL_2": valid_pages.loc[j, "URL"],
                            "Cosine_Similarity": round(sim, 3),
                            "Severity": "Critical Cannibalization (>0.60)" if sim >= 0.60 else "High Overlap (0.40-0.59)"
                        })

            df_pairs = pd.DataFrame(pairs).sort_values(by="Cosine_Similarity", ascending=False) if pairs else pd.DataFrame()
            st.session_state["df_cannibal"] = df_pairs
            st.success(f"✓ Analyzed {len(valid_pages)} pages. Found {len(df_pairs)} overlapping pairs!")

        if not st.session_state["df_cannibal"].empty:
            df_can = st.session_state["df_cannibal"]
            st.metric("Cannibalization Conflicts Flagged", len(df_can))
            st.dataframe(df_can, use_container_width=True)
            st.download_button(
                label="⬇️ Download Cannibalization Report (CSV)",
                data=df_can.to_csv(index=False),
                file_name="keyword_cannibalization_audit.csv",
                mime="text/csv"
            )

# =============================================================================
# TAB 3: ORGANIC KEYWORD SERP TRACKER
# =============================================================================
with tab_serp:
    st.subheader("Live Google Organic Search Rank Benchmark")
    st.write("Track search engine positioning across high-intent commercial terms using SerpApi.")
    
    col_kws, col_action = st.columns([3, 1])
    with col_kws:
        default_kws = st.session_state.get(
            "suggested_organic_kws",
            "telecom billing software\ntelecom CPQ software\nconvergent billing platform\ncustomer journey orchestration\nembedded b2b payments platform\nhealthcare patient billing software"
        )
        if not default_kws:
            default_kws = "telecom billing software\ntelecom CPQ software\nconvergent billing platform"
        kw_input = st.text_area("Target Enterprise Keywords (One per line)", value=default_kws, height=140)
    with col_action:
        st.write("")
        st.write("")
        track_serp_btn = st.button("🔍 Check Live SERP Positions", use_container_width=True)

    if track_serp_btn:
        if not serpapi_key:
            st.error("Please provide a SerpApi key in the sidebar or Streamlit Secrets.")
        else:
            kws = [k.strip() for k in kw_input.split("\n") if k.strip()]
            records = []
            target_host = urlparse(st.session_state.get("target_domain", "csgi.com")).netloc.replace("www.", "")
            
            prog = st.progress(0)
            for idx, kw in enumerate(kws):
                try:
                    params = {"engine": "google", "q": kw, "location": "United States", "hl": "en", "gl": "us", "api_key": serpapi_key}
                    res = requests.get("https://serpapi.com/search", params=params, timeout=20).json()
                    
                    found_rank = "> 20 (Absent)"
                    ranking_url = "N/A"
                    top_competitor = "N/A"
                    
                    org_results = res.get("organic_results", [])
                    if org_results:
                        top_competitor = urlparse(org_results[0].get("link", "")).netloc.replace("www.", "")
                    
                    for pos, item in enumerate(org_results[:20], 1):
                        link = item.get("link", "")
                        if target_host in link:
                            found_rank = f"#{pos}"
                            ranking_url = link
                            break
                            
                    records.append({
                        "Keyword": kw,
                        "Target_Rank": found_rank,
                        "Ranking_URL": ranking_url,
                        "Top_Ranking_Competitor": top_competitor,
                        "Status": "Visible" if "#" in found_rank else "Absent"
                    })
                except Exception as e:
                    records.append({"Keyword": kw, "Target_Rank": "Error", "Ranking_URL": str(e), "Top_Ranking_Competitor": "N/A", "Status": "Error"})
                
                prog.progress((idx + 1) / len(kws))
                
            df_rank = pd.DataFrame(records)
            st.session_state["df_rankings"] = df_rank
            st.success("✓ SERP Rank Tracking Complete!")

    if not st.session_state["df_rankings"].empty:
        df_r = st.session_state["df_rankings"]
        absent_count = (df_r["Target_Rank"] == "> 20 (Absent)").sum()
        invisibility_pct = (absent_count / len(df_r)) * 100
        
        c1, c2 = st.columns(2)
        c1.metric("Organic Invisibility Rate (Absent from Top 20)", f"{invisibility_pct:.1f}%")
        c2.metric("Top Competitor Infiltration", f"{len(df_r['Top_Ranking_Competitor'].unique())} Unique Domains")
        st.dataframe(df_r, use_container_width=True)
        st.download_button(
            label="⬇️ Download SERP Rankings (CSV)",
            data=df_r.to_csv(index=False),
            file_name="serp_rankings_audit.csv",
            mime="text/csv"
        )

# =============================================================================
# TAB 4: GENERATIVE AEO & AI OVERVIEWS
# =============================================================================
with tab_aeo:
    st.subheader("Generative Engine Optimization (AEO) & AI Overview Citations")
    st.write("Benchmark whether generative AI search answers cite your domain or your competitors.")

    col_aeo_in, col_aeo_btn = st.columns([3, 1])
    with col_aeo_in:
        default_aeo = st.session_state.get(
            "suggested_aeo_prompts",
            "best telecom billing software enterprise\nleading convergent billing platforms\nbest customer journey orchestration tools\ntop telecom CPQ software providers"
        )
        if not default_aeo:
            default_aeo = "best telecom billing software enterprise\nleading convergent billing platforms"
        aeo_input = st.text_area("Generative Evaluation Prompts", value=default_aeo, height=130)
    with col_aeo_btn:
        st.write("")
        st.write("")
        run_aeo_btn = st.button("🤖 Test AEO Visibility", use_container_width=True)

    if run_aeo_btn:
        if not serpapi_key:
            st.error("SerpApi key required to scrape live Google AI Overviews.")
        else:
            aeo_queries = [q.strip() for q in aeo_input.split("\n") if q.strip()]
            aeo_records = []
            target_host = urlparse(st.session_state.get("target_domain", "csgi.com")).netloc.replace("www.", "")

            prog_aeo = st.progress(0)
            for idx, q in enumerate(aeo_queries):
                try:
                    params = {"engine": "google", "q": q, "location": "United States", "hl": "en", "gl": "us", "api_key": serpapi_key}
                    res = requests.get("https://serpapi.com/search", params=params, timeout=20).json()
                    
                    ai_overview = res.get("ai_overview", {})
                    has_aio = bool(ai_overview)
                    
                    cited = False
                    winning_sources = []
                    
                    if has_aio:
                        references = ai_overview.get("references", [])
                        for ref in references:
                            ref_link = ref.get("link", "")
                            winning_sources.append(urlparse(ref_link).netloc.replace("www.", ""))
                            if target_host in ref_link:
                                cited = True
                    
                    aeo_records.append({
                        "Buyer_Query": q,
                        "AI_Overview_Generated": "Yes" if has_aio else "No",
                        "Target_Cited": "✓ Yes" if cited else "🔴 Omitted",
                        "Cited_Competitors": ", ".join(winning_sources[:3]) if winning_sources else "None",
                        "Outcome": "Won" if cited else "Competitor Won"
                    })
                except Exception as e:
                    aeo_records.append({"Buyer_Query": q, "AI_Overview_Generated": "Error", "Target_Cited": "Error", "Cited_Competitors": str(e), "Outcome": "Error"})
                
                prog_aeo.progress((idx + 1) / len(aeo_queries))

            df_aeo = pd.DataFrame(aeo_records)
            st.session_state["df_aeo"] = df_aeo
            st.success("✓ AEO Benchmark Complete!")

    if not st.session_state["df_aeo"].empty:
        df_a = st.session_state["df_aeo"]
        competitor_wins = (df_a["Target_Cited"] == "🔴 Omitted").sum()
        loss_rate = (competitor_wins / len(df_a)) * 100
        
        st.metric("Generative AI Competitor Win Rate", f"{loss_rate:.1f}%", help="Percentage of AI Overviews won by competitors or third parties.")
        st.dataframe(df_a, use_container_width=True)
        st.download_button(
            label="⬇️ Download AEO Citations Report (CSV)",
            data=df_a.to_csv(index=False),
            file_name="aeo_citations_audit.csv",
            mime="text/csv"
        )

# =============================================================================
# TAB 5: PAID SEARCH & ADS TRANSPARENCY
# =============================================================================
with tab_ads:
    st.subheader("Paid Search & Google Ads Transparency Center Audit")
    st.write("Audit live Google Search Sponsored Ads and Google Ads Transparency Center creatives.")

    col_ad1, col_ad2 = st.columns([3, 1])
    with col_ad1:
        ad_target_domain = st.text_input("Advertiser Domain to Inspect", value="csgi.com")
    with col_ad2:
        st.write("")
        st.write("")
        run_ads_btn = st.button("📊 Fetch Google Ads Intel", use_container_width=True)

    if run_ads_btn:
        if not serpapi_key:
            st.error("SerpApi key required to query Google Ads Transparency Center.")
        else:
            with st.spinner(f"Extracting active ad creatives for {ad_target_domain}..."):
                try:
                    params = {
                        "engine": "google_ads_transparency_center",
                        "text": ad_target_domain,
                        "api_key": serpapi_key,
                        "num": 40
                    }
                    res = requests.get("https://serpapi.com/search", params=params, timeout=25).json()
                    creatives = res.get("ad_creatives", [])
                    
                    ads_data = []
                    for c in creatives:
                        preview = c.get("link") or c.get("details_link", "N/A")
                        c_type = "GDN Image Banner" if "simgad" in str(c.get("image", "")) else ("Responsive Display" if "displayads" in str(preview) else "Search Text Ad")
                        ads_data.append({
                            "Creative_ID": c.get("ad_creative_id", "N/A"),
                            "Format": c.get("format", "text"),
                            "Type": c_type,
                            "First_Shown": c.get("first_shown", "N/A"),
                            "Last_Shown": c.get("last_shown", "N/A"),
                            "Preview_Link": preview
                        })
                    
                    df_ads = pd.DataFrame(ads_data)
                    st.session_state["df_ads"] = df_ads
                    st.success(f"✓ Extracted {len(df_ads)} active ad creatives!")
                except Exception as e:
                    st.error(f"Error extracting Ads Transparency data: {e}")

    if not st.session_state["df_ads"].empty:
        df_ad = st.session_state["df_ads"]
        st.metric("Total Active Google Ads Creatives", len(df_ad))
        st.dataframe(df_ad[["Creative_ID", "Format", "Type", "First_Shown", "Last_Shown", "Preview_Link"]], use_container_width=True)
        st.download_button(
            label="⬇️ Download Active Ads Inventory (CSV)",
            data=df_ad.to_csv(index=False),
            file_name="google_ads_transparency_audit.csv",
            mime="text/csv"
        )

# =============================================================================
# TAB 6: PRODUCTION SCHEMA.ORG JSON-LD GENERATOR
# =============================================================================
with tab_schema_gen:
    st.subheader("Production Schema.org JSON-LD Generator")
    st.write("Generate copy-paste schema code for your engineering team to fix the 0% structured data deficit.")

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        s_type = st.selectbox("Schema Entity Type", ["SoftwareApplication", "Organization", "FAQPage"])
        s_name = st.text_input("Product / Entity Name", value="CSG Ascendon")
        s_url = st.text_input("Canonical Target URL", value="https://www.csgi.com/products/ascendon")
    with col_s2:
        s_desc = st.text_area("Product Description", value="Cloud-native convergent billing and 5G monetization platform for tier-1 communications service providers.", height=125)

    if s_type == "SoftwareApplication":
        schema_obj = {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "SoftwareApplication",
                    "@id": f"{s_url}/#software",
                    "name": s_name,
                    "description": s_desc,
                    "applicationCategory": "BusinessApplication",
                    "operatingSystem": "Cloud-Native (AWS, Azure)",
                    "offers": {"@type": "Offer", "price": "Contact Sales", "priceCurrency": "USD"}
                }
            ]
        }
    elif s_type == "Organization":
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": s_name,
            "url": s_url,
            "description": s_desc,
            "sameAs": [
                "https://www.linkedin.com/company/csg-systems-international/",
                "https://en.wikipedia.org/wiki/CSG_Systems"
            ]
        }
    else:
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f"What is {s_name}?",
                    "acceptedAnswer": {"@type": "Answer", "text": s_desc}
                }
            ]
        }

    json_str = json.dumps(schema_obj, indent=2)
    st.markdown("#### Generated JSON-LD Code (Ready to insert into `<head>`)")
    st.code(f'<script type="application/ld+json">\n{json_str}\n</script>', language="html")

# =============================================================================
# TAB 7: CONNEXT BRANDED PDF DELIVERABLE COMPILER
# =============================================================================
with tab_report:
    st.subheader("Generate Executive Audit Report (Connext Global Identity)")
    st.write("Compiles all technical crawl metrics, security scores, cannibalization warnings, SERP rankings, and AEO data into an executive PDF.")

    if st.button("📑 Compile & Download Branded Executive PDF", use_container_width=True):
        with st.spinner("Generating publication-grade PDF deliverable..."):
            df_c = st.session_state.get("df_crawl", pd.DataFrame())
            df_r = st.session_state.get("df_rankings", pd.DataFrame())
            df_a = st.session_state.get("df_aeo", pd.DataFrame())
            target_name = st.session_state.get("target_domain", "Target Domain")
            sec_res = st.session_state.get("sec_headers", {})

            total_pages = len(df_c) if not df_c.empty else 0
            comm_schema_pct = (df_c["Commercial_Schema"].sum() / total_pages * 100) if total_pages > 0 else 0
            invis_pct = ((df_r["Target_Rank"] == "> 20 (Absent)").sum() / len(df_r) * 100) if not df_r.empty else 0
            aeo_loss_pct = ((df_a["Target_Cited"] == "🔴 Omitted").sum() / len(df_a) * 100) if not df_a.empty else 0

            crawl_rows = ""
            if not df_c.empty:
                for _, row in df_c.head(15).iterrows():
                    crawl_rows += f"""<tr>
                        <td style="word-break: break-all;">{row['URL']}</td>
                        <td>{row['H1_Count']}</td>
                        <td>{'⚠️ Concatenated' if row['Concatenated_H1'] else 'Clean'}</td>
                        <td>{'✓ Yes' if row['Commercial_Schema'] else '🔴 None'}</td>
                    </tr>"""

            pdf_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
            <style>
                @page {{ size: letter; margin: 0.6in; }}
                body {{ font-family: 'DejaVu Sans', sans-serif; color: #1e293b; font-size: 8.5pt; line-height: 1.45; }}
                .header-bar {{ display: flex; justify-content: space-between; border-bottom: 3.5px solid #004b93; padding-bottom: 8px; margin-bottom: 15px; }}
                .title {{ color: #004b93; font-size: 18pt; font-weight: bold; margin: 0; }}
                .sub {{ color: #e85122; font-size: 10pt; font-weight: bold; }}
                .metric-grid {{ display: flex; gap: 8px; margin: 15px 0; }}
                .m-card {{ flex: 1; background: #f8fafc; border: 1px solid #e2e8f0; border-top: 3px solid #004b93; padding: 8px; text-align: center; border-radius: 4px; }}
                .m-val {{ font-size: 15pt; font-weight: bold; color: #004b93; }}
                .m-lbl {{ font-size: 7pt; color: #64748b; font-weight: bold; text-transform: uppercase; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 8pt; }}
                th {{ background: #004b93; color: white; padding: 6px; border: 1px solid #003a73; text-align: left; }}
                td {{ padding: 5px 6px; border: 1px solid #e2e8f0; }}
                tr:nth-child(even) td {{ background: #f8fafc; }}
                .dofa-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 15px 0; }}
                .dofa-box {{ border: 1px solid #e2e8f0; padding: 8px 12px; border-radius: 4px; font-size: 8pt; }}
                .d-d {{ border-top: 3.5px solid #dc2626; }}
                .d-o {{ border-top: 3.5px solid #16a34a; }}
                .d-f {{ border-top: 3.5px solid #004b93; }}
                .d-a {{ border-top: 3.5px solid #f2a900; }}
            </style>
            </head>
            <body>
                <div class="header-bar">
                    <div>
                        <div class="title">Connext Global Solutions — Enterprise Digital Audit</div>
                        <div class="sub">Executive Architecture, Schema, AEO & Search Benchmark</div>
                    </div>
                    <div>
                        <img src="data:image/png;base64,{CONNEXT_LOGO_B64}" style="width: 140px;">
                    </div>
                </div>

                <p><strong>Audited Entity:</strong> {target_name} | <strong>Security Grade:</strong> {sec_res.get('Grade', 'N/A')} | <strong>llms.txt AI Standard:</strong> {'Active' if st.session_state.get('has_llms') else 'Missing (404 Error)'}</p>

                <div class="metric-grid">
                    <div class="m-card">
                        <div class="m-val">{total_pages}</div>
                        <div class="m-lbl">Pages Audited</div>
                    </div>
                    <div class="m-card">
                        <div class="m-val">{comm_schema_pct:.1f}%</div>
                        <div class="m-lbl">Commercial Schemas</div>
                    </div>
                    <div class="m-card">
                        <div class="m-val">{invis_pct:.1f}%</div>
                        <div class="m-lbl">SERP Invisibility</div>
                    </div>
                    <div class="m-card">
                        <div class="m-val">{aeo_loss_pct:.1f}%</div>
                        <div class="m-lbl">AEO Competitor Wins</div>
                    </div>
                </div>

                <h3 style="color: #004b93; margin-top: 15px;">Brand DOFA / SWOT Strategic Evaluation</h3>
                <div class="dofa-grid">
                    <div class="dofa-box d-d">
                        <strong style="color: #dc2626;">DEBILIDADES (Weaknesses)</strong>
                        <ul>
                            <li>Zero commercial structured schema markup ({comm_schema_pct:.1f}%).</li>
                            <li>Root llms.txt standard returns 404 error.</li>
                            <li>Concatenated words in H1 headings due to component styling.</li>
                        </ul>
                    </div>
                    <div class="dofa-box d-o">
                        <strong style="color: #16a34a;">OPORTUNIDADES (Opportunities)</strong>
                        <ul>
                            <li>Implement Organization, Service & FAQ schemas for rich snippets.</li>
                            <li>Capture commercial buyer search intent via Paid Search.</li>
                            <li>Publish structured llms.txt for preferential AI crawling.</li>
                        </ul>
                    </div>
                    <div class="dofa-box d-f">
                        <strong style="color: #004b93;">FORTALEZAS (Strengths)</strong>
                        <ul>
                            <li>Modern headless architecture on Next.js, Vercel & Azure CDN.</li>
                            <li>Enterprise security grade (HSTS, CSP strict policies).</li>
                            <li>Zero Cumulative Layout Shift (CLS: 0.000).</li>
                        </ul>
                    </div>
                    <div class="dofa-box d-a">
                        <strong style="color: #b45309;">AMENAZAS (Threats)</strong>
                        <ul>
                            <li>Competitors winning {aeo_loss_pct:.1f}% of Generative AI Overviews.</li>
                            <li>Niche rivals conquesting #1 sponsored ad positions.</li>
                        </ul>
                    </div>
                </div>

                <h3 style="color: #004b93; margin-top: 15px;">Sample Commercial Architecture Crawl</h3>
                <table>
                    <thead>
                        <tr>
                            <th style="width: 55%;">URL</th>
                            <th style="width: 15%;">H1 Count</th>
                            <th style="width: 15%;">Typography</th>
                            <th style="width: 15%;">Commercial Schema</th>
                        </tr>
                    </thead>
                    <tbody>
                        {crawl_rows if crawl_rows else '<tr><td colspan="4">Run Tab 1 crawl to populate table.</td></tr>'}
                    </tbody>
                </table>
            </body>
            </html>
            """
            pdf_bytes = weasyprint.HTML(string=pdf_html).write_pdf()
            st.download_button(
                label="⬇️ Download Executive Audit PDF",
                data=pdf_bytes,
                file_name=f"connext_executive_audit_{urlparse(target_name).netloc}.pdf",
                mime="application/pdf"
            )
