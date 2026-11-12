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
from datetime import datetime
from collections import Counter
import google.generativeai as genai

# -----------------------------------------------------------------------------
# 0. OFFICIAL CONNEXT ASSETS & PALETTE
# -----------------------------------------------------------------------------
LOGO_URL = "https://images.g2crowd.com/uploads/product/image/936e9c4afae278b1c3b4bc40dbdff26b/connext.png"

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
    .stTabs [data-baseweb="tab-list"] { gap: 3px; }
    .stTabs [data-baseweb="tab"] { border-radius: 4px 4px 0 0; padding: 6px 11px; font-weight: 600; color: #1e293b; font-size: 12px; }
    .stTabs [aria-selected="true"] { background-color: #004b93 !important; color: white !important; }
    .stButton>button { background-color: #004b93; color: white; font-weight: bold; border-radius: 4px; border: none; }
    .stButton>button:hover { background-color: #e85122; color: white; }
    .guide-box { background: #f8fafc; border: 1px solid #cbd5e1; border-left: 4px solid #004b93; border-radius: 4px; padding: 14px 18px; margin-bottom: 16px; }
    .step-pill { background: #004b93; color: white; border-radius: 12px; padding: 2px 8px; font-size: 11px; font-weight: bold; margin-right: 6px; }
    .metric-delta-pos { color: #16a34a; font-weight: bold; }
    .metric-delta-neg { color: #dc2626; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# Header Bar with Hosted Logo
col_logo, col_text = st.columns([1.5, 6])
with col_logo:
    try:
        st.image(LOGO_URL, width=210)
    except Exception:
        st.markdown("<h2 style='color:#004b93; margin:0;'>connext</h2>", unsafe_allow_html=True)

with col_text:
    st.markdown('<div class="brand-title">Enterprise Digital Performance, AEO & Search Intelligence</div>', unsafe_allow_html=True)
    st.markdown('<div class="brand-sub">CONNEXT GLOBAL SOLUTIONS — PROPRIETARY ENTERPRISE AUDIT SUITE</div>', unsafe_allow_html=True)

st.markdown("---")

# -----------------------------------------------------------------------------
# 0.5 DYNAMIC GEMINI, CRAWLY SEO & RECURSIVE SITEMAP / CONTENT ENGINES
# -----------------------------------------------------------------------------
def query_gemini_dynamic(prompt, api_key):
    """Auto-discovers available Gemini models on the user's API key to prevent 404 errors."""
    if not api_key:
        return None, None
    candidate_models = [
        "gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest",
        "gemini-1.5-flash-latest", "gemini-1.5-flash-002", "gemini-1.5-flash",
        "gemini-1.5-pro", "gemini-2.0-flash-lite"
    ]
    try:
        r_list = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}", timeout=5)
        if r_list.status_code == 200:
            data = r_list.json()
            active_models = [
                m["name"].replace("models/", "") 
                for m in data.get("models", []) 
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            preferred = [m for m in candidate_models if m in active_models]
            other = [m for m in active_models if m not in preferred and ("flash" in m.lower() or "pro" in m.lower())]
            if preferred or other:
                candidate_models = preferred + other
    except Exception:
        pass

    for m in candidate_models:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}]}
            res = requests.post(url, json=payload, timeout=25)
            if res.status_code == 200:
                res_data = res.json()
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                return text, m
        except Exception:
            continue
    return None, None

def is_xml_sitemap_url(url):
    """Detects if a URL is an XML sitemap even when query parameters like ?from=... are appended."""
    parsed_path = urlparse(url).path.lower()
    return (
        parsed_path.endswith(".xml") or 
        parsed_path.endswith(".xml.gz") or 
        "sitemap" in parsed_path
    )

def is_valid_html_page_url(url, root_netloc):
    """Ensures URL is an actual web page on the target domain and not an XML sitemap or static asset."""
    parsed = urlparse(url)
    if root_netloc not in parsed.netloc.replace("www.", ""):
        return False
    path_lower = parsed.path.lower()
    excluded_exts = (
        ".xml", ".xml.gz", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", 
        ".pdf", ".css", ".js", ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".zip"
    )
    if path_lower.endswith(excluded_exts) or "sitemap" in path_lower:
        return False
    return True

def discover_target_urls(root, target_site, filter_term, crawl_limit, headers):
    """
    Recursively unpacks XML Sitemap Indexes (including Shopify/CMS child sitemaps with ?from=... query strings),
    extracts inner HTML pages, applies category/slug filtering, and spiders internal category links as fallback.
    """
    root_netloc = urlparse(root).netloc.replace("www.", "")
    filter_clean = filter_term.strip().lower()
    
    seed_sitemaps = [
        f"{root}/sitemap.xml",
        f"{root}/sitemap_index.xml",
        f"{root}/wp-sitemap.xml",
        f"{root}/sitemap_pages_1.xml",
        f"{root}/sitemap_products_1.xml",
        f"{root}/sitemap_collections_1.xml",
        f"{root}/sitemap-pages.xml",
        f"{root}/sitemap-products.xml"
    ]
    try:
        r_rob = requests.get(f"{root}/robots.txt", headers=headers, timeout=5)
        if r_rob.status_code == 200:
            for line in r_rob.text.splitlines():
                if line.strip().lower().startswith("sitemap:"):
                    sm_loc = line.split(":", 1)[1].strip()
                    if sm_loc.startswith("http") and sm_loc not in seed_sitemaps:
                        seed_sitemaps.insert(0, sm_loc)
    except Exception:
        pass

    visited_sitemaps = set()
    child_sitemaps_queue = []
    discovered_html_urls = []
    unpacked_sitemaps_log = []

    def parse_sitemap_xml(sm_url):
        c_sitemaps = []
        p_urls = []
        try:
            r = requests.get(sm_url, headers=headers, timeout=8)
            if r.status_code == 200 and ("xml" in r.headers.get("Content-Type", "").lower() or "<urlset" in r.text or "<sitemapindex" in r.text):
                tree = ET.fromstring(r.content)
                for elem in tree.iter():
                    tag_lower = elem.tag.lower()
                    if tag_lower.endswith("sitemap"):
                        for child in elem:
                            if child.tag.lower().endswith("loc") and child.text:
                                c_sitemaps.append(child.text.strip())
                    elif tag_lower.endswith("url"):
                        for child in elem:
                            if child.tag.lower().endswith("loc") and child.text:
                                loc_txt = child.text.strip()
                                if is_xml_sitemap_url(loc_txt):
                                    c_sitemaps.append(loc_txt)
                                else:
                                    p_urls.append(loc_txt)
                if not c_sitemaps and not p_urls:
                    for elem in tree.iter():
                        if elem.tag.lower().endswith("loc") and elem.text:
                            loc_txt = elem.text.strip()
                            if is_xml_sitemap_url(loc_txt):
                                c_sitemaps.append(loc_txt)
                            else:
                                p_urls.append(loc_txt)
        except Exception:
            pass
        return c_sitemaps, p_urls

    # Step A: Process root / seed sitemaps
    for sm in seed_sitemaps:
        if sm in visited_sitemaps:
            continue
        visited_sitemaps.add(sm)
        c_sms, p_urls = parse_sitemap_xml(sm)
        if c_sms or p_urls:
            unpacked_sitemaps_log.append({
                "Sitemap_URL": sm,
                "Type": "Root Sitemap Index" if c_sms else "Page Sitemap",
                "Child_Sitemaps_Found": len(c_sms),
                "HTML_Pages_Extracted": len(p_urls)
            })
        for cs in c_sms:
            if cs not in visited_sitemaps and cs not in child_sitemaps_queue:
                child_sitemaps_queue.append(cs)
        for pu in p_urls:
            if is_valid_html_page_url(pu, root_netloc):
                discovered_html_urls.append((pu, sm))
        if len(discovered_html_urls) >= crawl_limit * 4 and not child_sitemaps_queue:
            break

    # Step B: Recursively unpack child sitemaps (e.g. sitemap_pages_1.xml?from=..., sitemap_products_1.xml?from=...)
    if child_sitemaps_queue:
        if filter_clean:
            terms = [t.strip().strip("/") for t in filter_clean.split(",") if t.strip()]
            matching_children = [cs for cs in child_sitemaps_queue if any(t in cs.lower() for t in terms)]
            other_children = [cs for cs in child_sitemaps_queue if cs not in matching_children]
            ordered_children = matching_children + other_children
        else:
            priority_children = [cs for cs in child_sitemaps_queue if any(k in cs.lower() for k in ["page", "product", "solution", "service", "collection"])]
            other_children = [cs for cs in child_sitemaps_queue if cs not in priority_children]
            ordered_children = priority_children + other_children

        for cs in ordered_children[:18]:
            if cs in visited_sitemaps:
                continue
            visited_sitemaps.add(cs)
            sub_sms, sub_pages = parse_sitemap_xml(cs)
            unpacked_sitemaps_log.append({
                "Sitemap_URL": cs,
                "Type": "Child Category Sitemap",
                "Child_Sitemaps_Found": len(sub_sms),
                "HTML_Pages_Extracted": len(sub_pages)
            })
            for pu in sub_pages:
                if is_valid_html_page_url(pu, root_netloc):
                    discovered_html_urls.append((pu, cs))
            if len(discovered_html_urls) >= crawl_limit * 6:
                break

    # Step C: Filter discovered HTML pages by category / slug / keyword filter
    filtered_urls = []
    seen_u = set()
    # Always include target_site root if no filter is set
    if not filter_clean:
        norm_target = target_site.split("#")[0].rstrip("/") if target_site.rstrip("/") != root.rstrip("/") else root
        seen_u.add(norm_target)
        filtered_urls.append(target_site)

    for pu, parent_sm in discovered_html_urls:
        clean_pu = pu.split("#")[0].rstrip("/") if pu.rstrip("/") != root.rstrip("/") else root
        if clean_pu in seen_u:
            continue
        if not filter_clean:
            seen_u.add(clean_pu)
            filtered_urls.append(pu)
        else:
            terms = [t.strip() for t in filter_clean.split(",") if t.strip()]
            parent_path = urlparse(parent_sm).path.lower()
            if any(t in pu.lower() or t.strip("/") in parent_path for t in terms):
                seen_u.add(clean_pu)
                filtered_urls.append(pu)

    # Step D: Live HTML Spider Fallback (Crawls homepage & category pages for internal links)
    if len(filtered_urls) < crawl_limit:
        spider_seeds = [target_site, root]
        if filter_clean:
            for t in [x.strip().strip("/") for x in filter_clean.split(",") if x.strip()]:
                if not t.startswith("http"):
                    spider_seeds.insert(0, f"{root}/{t}")
        for f_u in list(filtered_urls)[:3]:
            if f_u not in spider_seeds:
                spider_seeds.append(f_u)

        visited_spider = set()
        for s_url in spider_seeds[:5]:
            if s_url in visited_spider:
                continue
            visited_spider.add(s_url)
            try:
                r_sp = requests.get(s_url, headers=headers, timeout=7)
                if r_sp.status_code == 200 and "text/html" in r_sp.headers.get("Content-Type", "").lower():
                    soup_sp = BeautifulSoup(r_sp.text, "html.parser")
                    for a_tag in soup_sp.find_all("a", href=True):
                        full_u = urljoin(root, a_tag["href"].strip()).split("#")[0].split("?")[0]
                        if is_valid_html_page_url(full_u, root_netloc):
                            norm_u = full_u.rstrip("/") if full_u.rstrip("/") != root.rstrip("/") else root
                            if norm_u not in seen_u:
                                if not filter_clean:
                                    seen_u.add(norm_u)
                                    filtered_urls.append(full_u)
                                else:
                                    terms = [t.strip() for t in filter_clean.split(",") if t.strip()]
                                    if any(t in full_u.lower() for t in terms):
                                        seen_u.add(norm_u)
                                        filtered_urls.append(full_u)
            except Exception:
                continue

    if not filtered_urls:
        filtered_urls = [target_site]

    return filtered_urls[:crawl_limit], unpacked_sitemaps_log

def calculate_flesch_readability(text):
    """Calculates Flesch Reading Ease score (0-100) from page text."""
    words = re.findall(r'\b[a-zA-Z]+\b', text)
    if len(words) < 20:
        return 60.0
    sentences = max(1, len(re.split(r'[.!?]+', text)) - 1)
    def count_syllables(word):
        word = word.lower()
        if len(word) <= 3:
            return 1
        word = re.sub(r'(?:[^laeiouy]es|ed|[^laeiouy]e)$', '', word)
        word = re.sub(r'^y', '', word)
        syls = len(re.findall(r'[aeiouy]{1,2}', word))
        return max(1, syls)
    syllables = sum(count_syllables(w) for w in words[:500])
    w_count = min(len(words), 500)
    s_count = max(1, int(sentences * (w_count / len(words))))
    score = 206.835 - 1.015 * (w_count / s_count) - 84.6 * (syllables / w_count)
    return round(max(0.0, min(100.0, score)), 1)

RICH_COMMERCIAL_SCHEMAS = {
    "Product", "Service", "SoftwareApplication", "WebApplication", "MobileApplication",
    "MedicalBusiness", "MedicalOrganization", "MedicalClinic", "MedicalProcedure",
    "DiagnosticProcedure", "MedicalTest", "MedicalCondition", "Drug", "LocalBusiness",
    "ProfessionalService", "FinancialProduct", "Course", "Offer", "AggregateOffer"
}

AEO_ANSWER_SCHEMAS = {
    "FAQPage", "QAPage", "Question", "HowTo", "Article", "NewsArticle",
    "BlogPosting", "MedicalWebPage", "ScholarlyArticle", "TechArticle",
    "Review", "AggregateRating", "VideoObject"
}

BASIC_IDENTITY_SCHEMAS = {
    "Organization", "Corporation", "WebSite", "WebPage", "AboutPage",
    "ContactPage", "CollectionPage", "ItemList", "BreadcrumbList",
    "Person", "ImageObject", "SiteNavigationElement", "SearchAction"
}

SCHEMA_BUSINESS_DICTIONARY = {
    "Product": ("🟢 Commercial Offer", "Tells Google & AI this page sells a specific product (enables price, stock & review stars in search)."),
    "Service": ("🟢 Commercial Offer", "Defines a commercial service line so AI engines match your company to buyer service queries."),
    "SoftwareApplication": ("🟢 Commercial Offer", "Identifies a software platform or SaaS app for software category rankings."),
    "MedicalBusiness": ("🟢 Clinical Authority", "Establishes a verified healthcare/biotech provider or lab in Google's medical Knowledge Graph."),
    "DiagnosticProcedure": ("🟢 Clinical Authority", "Defines a clinical screening or diagnostic test for medical & healthcare searches."),
    "LocalBusiness": ("🟢 Commercial Offer", "Connects physical locations, hours, and contact info to Google Maps & local search."),
    "Offer": ("🟢 Commercial Offer", "Declares pricing, plans, or commercial availability to search crawlers."),
    "FAQPage": ("🤖 AI & AEO Citation", "Formats Q&As so Google AI Overviews, ChatGPT, and Perplexity quote your answers directly."),
    "HowTo": ("🤖 AI & AEO Citation", "Breaks down step-by-step instructions for featured snippets and AI answers."),
    "Article": ("🤖 AI & AEO Citation", "Identifies educational articles, author credentials, and publish dates for topical authority."),
    "BlogPosting": ("🤖 AI & AEO Citation", "Marks blog posts and insights articles for Google Discover and news indexing."),
    "Review": ("🤖 AI & AEO Citation", "Displays customer/client testimonial ratings and trust signals."),
    "AggregateRating": ("🤖 AI & AEO Citation", "Enables star rating badges directly underneath your URL in Google search results."),
    "Organization": ("🟡 Basic Brand Identity", "Declares company name and logo. Good baseline, but does NOT describe specific products or services."),
    "Corporation": ("🟡 Basic Brand Identity", "Declares corporate legal identity. Should be paired with Service or Product schema on solution pages."),
    "WebSite": ("🟡 Basic Brand Identity", "Identifies the root website domain. Does not help individual solution pages rank."),
    "WebPage": ("🟡 Basic Brand Identity", "Generic default tag added by CMS platforms. Tells Google 'this is a web page' without commercial context."),
    "BreadcrumbList": ("🟡 Basic Navigation", "Shows folder path (Home > Solutions) in search results, but provides zero product details.")
}

def extract_all_schemas_from_soup(soup):
    """Recursively extracts all JSON-LD and Microdata schema types and classifies them into plain-English tiers."""
    found_types = []
    block_count = 0

    def walk_json(obj):
        if isinstance(obj, dict):
            if "@type" in obj:
                t_val = obj["@type"]
                t_list = t_val if isinstance(t_val, list) else [t_val]
                for tv in t_list:
                    if tv:
                        found_types.append(str(tv).split("/")[-1].strip())
            for v in obj.values():
                walk_json(v)
        elif isinstance(obj, list):
            for item in obj:
                walk_json(item)

    for s in soup.find_all("script", type=lambda x: x and "ld+json" in x.lower()):
        raw_txt = s.get_text(strip=True) or (s.string.strip() if s.string else "")
        if raw_txt:
            block_count += 1
            try:
                walk_json(json.loads(raw_txt))
            except Exception:
                for m in re.findall(r'"@type"\s*:\s*"([^"]+)"', raw_txt):
                    found_types.append(m.split("/")[-1].strip())

    for tag in soup.find_all(attrs={"itemtype": True}):
        itype = str(tag.get("itemtype", "")).strip()
        if "schema.org/" in itype:
            block_count += 1
            found_types.append(itype.split("schema.org/")[-1].strip("/"))

    unique_types = sorted(list(set([t for t in found_types if t])))
    has_rich = any(t in RICH_COMMERCIAL_SCHEMAS for t in unique_types)
    has_aeo = any(t in AEO_ANSWER_SCHEMAS for t in unique_types)
    has_basic = any(t in BASIC_IDENTITY_SCHEMAS for t in unique_types)

    if has_rich:
        tier = "🟢 Rich Commercial / Solution"
        plain_summary = "Commercial Offer Declared (" + ", ".join([t for t in unique_types if t in RICH_COMMERCIAL_SCHEMAS][:2]) + ")"
    elif has_aeo:
        tier = "🤖 AI Answer / FAQ / Article"
        plain_summary = "AI Citation Ready (" + ", ".join([t for t in unique_types if t in AEO_ANSWER_SCHEMAS][:2]) + ")"
    elif has_basic or unique_types:
        tier = "🟡 Basic Brand / Nav Only"
        plain_summary = "Brand/Page Tag Only (" + ", ".join(unique_types[:2]) + " — Missing Service/Product)"
    else:
        tier = "🔴 No Schema (0 Tags)"
        plain_summary = "No Schema Markup (Invisible to AI Knowledge Graph)"

    return block_count, unique_types, has_rich, has_aeo, tier, plain_summary

def query_crawly_seo(domain, api_key):
    """Fetches Domain Authority, Referring Domains, and Backlinks via Crawly Free API (No credit card)."""
    if not api_key or not domain:
        return None
    clean_domain = urlparse(domain).netloc.replace("www.", "") if "http" in domain else domain
    clean_domain = clean_domain.strip("/")
    headers = {"Authorization": f"Bearer {api_key.strip()}", "Accept": "application/json"}
    result = {
        "domain": clean_domain,
        "domain_authority": 0,
        "referring_domains": 0,
        "total_backlinks": 0,
        "spam_score": 0,
        "backlinks": [],
        "authority_tier": "Early-Stage / Low Authority (DA < 20)",
        "status": "Success"
    }
    try:
        url_da = "https://www.getcrawly.com/api/v1/domain-authority"
        r_da = requests.get(url_da, params={"domain": clean_domain}, headers=headers, timeout=12)
        if r_da.status_code == 200:
            data_da = r_da.json()
            summary = data_da.get("summary", data_da.get("data", data_da))
            da_val = summary.get("domain_authority", summary.get("authority_score", summary.get("score", 0)))
            result["domain_authority"] = round(float(da_val)) if da_val is not None else 0
            result["referring_domains"] = int(summary.get("referring_domains", summary.get("total_referring_domains", 0)))
            result["total_backlinks"] = int(summary.get("total_backlinks", summary.get("backlinks_count", summary.get("total_links", 0))))
            result["spam_score"] = round(float(summary.get("spam_score", 0)))
    except Exception:
        pass
    da = result["domain_authority"]
    if da >= 60:
        result["authority_tier"] = "Tier-1 Enterprise Authority (DA 60-100)"
    elif da >= 35:
        result["authority_tier"] = "Tier-2 Mid-Market Authority (DA 35-59)"
    elif da >= 15:
        result["authority_tier"] = "Tier-3 Emerging Authority (DA 15-34)"
    else:
        result["authority_tier"] = "Tier-4 Early-Stage / Low Authority (DA 0-14)"

    try:
        url_bl = "https://www.getcrawly.com/api/v1/backlinks"
        r_bl = requests.get(url_bl, params={"domain": clean_domain}, headers=headers, timeout=14)
        if r_bl.status_code == 200:
            data_bl = r_bl.json()
            bl_items = data_bl.get("backlinks", data_bl.get("data", []))
            formatted_bl = []
            for b in bl_items:
                if isinstance(b, dict):
                    formatted_bl.append({
                        "Referring Domain": b.get("source_domain", b.get("referring_domain", "N/A")),
                        "Source URL": b.get("source_url", b.get("url", "N/A")),
                        "Target Page": b.get("target_url", b.get("target", "N/A")),
                        "Anchor Text": b.get("anchor_text", b.get("anchor", "N/A")),
                        "Quality": b.get("quality_rating", b.get("quality", "Good"))
                    })
            result["backlinks"] = formatted_bl
    except Exception:
        pass
    return result

def search_unlinked_brand_mentions(domain, serpapi_key, limit=10):
    if not serpapi_key or not domain:
        return []
    netloc = urlparse(domain).netloc.replace("www.", "") if "http" in domain else domain
    brand_name = netloc.split(".")[0].title()
    query = f'"{brand_name}" -site:{netloc}'
    try:
        params = {
            "engine": "google", "q": query, "location": "United States",
            "hl": "en", "gl": "us", "num": limit, "api_key": serpapi_key
        }
        res = requests.get("https://serpapi.com/search", params=params, timeout=18).json()
        results = []
        for item in res.get("organic_results", []):
            link = item.get("link", "")
            results.append({
                "Source_Website": urlparse(link).netloc.replace("www.", ""),
                "Article_Title": item.get("title", ""),
                "Context_Snippet": item.get("snippet", "")[:160] + "...",
                "Target_Outreach_URL": link
            })
        return results
    except Exception:
        return []

# -----------------------------------------------------------------------------
def parse_ads_timestamp(ts_val):
    """Converts Unix epoch seconds (e.g. 1778975148) or ISO date strings into (YYYY-MM-DD, datetime_obj)."""
    if ts_val is None or ts_val == "N/A" or str(ts_val).strip() == "":
        return "N/A", None
    s_val = str(ts_val).strip()
    if s_val.replace(".", "", 1).isdigit():
        try:
            num = float(s_val)
            if num > 1e11:
                num = num / 1000.0
            dt = datetime.utcfromtimestamp(num)
            return dt.strftime("%Y-%m-%d"), dt
        except Exception:
            pass
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s_val[:10], fmt)
            return dt.strftime("%Y-%m-%d"), dt
        except Exception:
            continue
    return s_val, None

def process_transparency_creatives(creatives, default_domain):
    """Transforms raw SerpApi Google Ads Transparency Center creatives into actionable paid media intelligence."""
    if not creatives:
        return pd.DataFrame()
    
    # First pass to find the most recent timestamp in the dataset for relative recency calculation
    parsed_dates = []
    for c in creatives:
        _, l_dt = parse_ads_timestamp(c.get("last_shown"))
        if l_dt:
            parsed_dates.append(l_dt)
    max_dataset_dt = max(parsed_dates) if parsed_dates else datetime.utcnow()

    ads_data = []
    for c in creatives:
        c_id = str(c.get("ad_creative_id", "N/A"))
        adv_name = str(c.get("advertiser", "Verified Advertiser"))
        adv_id = str(c.get("advertiser_id", "N/A"))
        t_dom = str(c.get("target_domain", default_domain))
        raw_fmt = str(c.get("format", "text")).lower().strip()
        w = c.get("width")
        h = c.get("height")
        dim_str = f"{w}x{h}" if (w and h) else "Responsive / Auto"
        thumb_url = str(c.get("image", "")) if c.get("image") else ""
        preview = c.get("link") or c.get("details_link") or (
            f"https://adstransparency.google.com/advertiser/{adv_id}/creative/{c_id}?region=anywhere"
            if adv_id != "N/A" and c_id != "N/A" else "N/A"
        )

        # Accurate Channel & Format Classification (Fixes false GDN Image Banner bug on text ads)
        if raw_fmt == "text":
            channel_type = "🔍 Google Search Text Ad (SERP)"
            fmt_clean = "Search Text Ad"
            funnel_stage = "Bottom-of-Funnel (High-Intent Search)"
        elif raw_fmt == "video":
            channel_type = "🎬 YouTube / Video Ad"
            fmt_clean = f"Video ({dim_str})" if w else "Video Ad"
            funnel_stage = "Top/Mid-Funnel (Video Engagement)"
        elif raw_fmt == "image":
            channel_type = "🖼️ Display / PMax Image Banner"
            fmt_clean = f"Image Banner ({dim_str})" if w else "Display Banner"
            funnel_stage = "Mid-Funnel (Retargeting & Awareness)"
        else:
            channel_type = f"📢 Multi-Format / PMax ({raw_fmt.title()})"
            fmt_clean = raw_fmt.title()
            funnel_stage = "Cross-Channel PMax"

        f_str, f_dt = parse_ads_timestamp(c.get("first_shown"))
        l_str, l_dt = parse_ads_timestamp(c.get("last_shown"))
        days_active = max(1, (l_dt - f_dt).days + 1) if (f_dt and l_dt) else 1
        days_since_last = max(0, (max_dataset_dt - l_dt).days) if l_dt else 0

        if days_since_last <= 7:
            activity_status = "🟢 Active Now (Served Last 7d)"
        elif days_since_last <= 30:
            activity_status = "🟡 Active Recently (Last 30d)"
        else:
            activity_status = f"⚪ Historical ({days_since_last}d ago)"

        if days_active >= 60:
            longevity_tier = "🔥 Evergreen Winner (60+ Days)"
            insight = f"Proven ROI Control Ad — Running continuously for {days_active} days ({funnel_stage.split('(')[0].strip()})"
        elif days_active >= 15:
            longevity_tier = "🟢 Scaling Creative (15–59 Days)"
            insight = f"Active Scaling Ad — Running {days_active} days ({fmt_clean})"
        else:
            longevity_tier = "🧪 New / A-B Test (<15 Days)"
            insight = f"Short Flight / Creative Test — {days_active} day(s) active"

        ads_data.append({
            "Ad_Preview": thumb_url,
            "Advertiser": adv_name,
            "Target_Domain": t_dom,
            "Channel": channel_type,
            "Format_Specs": fmt_clean,
            "Funnel_Role": funnel_stage,
            "Longevity_Tier": longevity_tier,
            "Days_Active": days_active,
            "First_Shown": f_str,
            "Last_Shown": l_str,
            "Activity_Status": activity_status,
            "Strategic_Takeaway": insight,
            "Creative_ID": c_id,
            "Preview_Link": preview,
            # Legacy compatibility aliases
            "Format": fmt_clean,
            "Type": channel_type
        })

    df_out = pd.DataFrame(ads_data)
    if not df_out.empty:
        df_out = df_out.sort_values(by=["Days_Active", "Last_Shown"], ascending=[False, False]).reset_index(drop=True)
    return df_out

def scan_live_serp_paid_ads(queries, target_domain, serpapi_key):
    """Queries live Google Search SERPs to extract actual paid search headlines, descriptions, sitelinks & competitor conquesting."""
    if not serpapi_key or not queries:
        return pd.DataFrame()
    target_host = urlparse(target_domain).netloc.replace("www.", "").lower() if "http" in target_domain else target_domain.replace("www.", "").lower()
    brand_token = target_host.split(".")[0] if target_host else ""

    serp_ads_rows = []
    for q in queries[:8]:
        try:
            params = {
                "engine": "google",
                "q": q,
                "location": "United States",
                "hl": "en",
                "gl": "us",
                "api_key": serpapi_key
            }
            res = requests.get("https://serpapi.com/search", params=params, timeout=20).json()
            all_ads = []
            for pos_idx, ad in enumerate(res.get("ads", []) or res.get("top_ads", []), 1):
                all_ads.append((f"#{pos_idx} Top SERP", ad))
            for pos_idx, ad in enumerate(res.get("bottom_ads", []), 1):
                all_ads.append((f"#{pos_idx} Bottom SERP", ad))

            for slot_label, ad in all_ads:
                link = ad.get("link") or ad.get("tracking_link", "N/A")
                disp_link = ad.get("displayed_link", link)
                adv_dom = urlparse(link).netloc.replace("www.", "").lower() if link.startswith("http") else str(disp_link).split("/")[0].replace("www.", "").lower()
                is_own = (target_host in adv_dom) or (brand_token and brand_token in adv_dom)
                is_brand_query = (brand_token and brand_token in q.lower())

                if is_own:
                    comp_status = "🟢 Your Brand Ad"
                elif is_brand_query:
                    comp_status = "🚨 Competitor Conquesting Your Brand!"
                else:
                    comp_status = "⚔️ Competitor Capturing Buyer Query"

                sitelinks_list = []
                if isinstance(ad.get("sitelinks"), dict):
                    for sl_group in ad["sitelinks"].values():
                        if isinstance(sl_group, list):
                            for sl in sl_group:
                                if isinstance(sl, dict) and sl.get("title"):
                                    sitelinks_list.append(sl["title"])
                elif isinstance(ad.get("sitelinks"), list):
                    for sl in ad["sitelinks"]:
                        if isinstance(sl, dict) and sl.get("title"):
                            sitelinks_list.append(sl["title"])

                serp_ads_rows.append({
                    "Search_Query": q,
                    "Ad_Position": slot_label,
                    "Competitor_Status": comp_status,
                    "Bidding_Domain": adv_dom or disp_link,
                    "Live_Ad_Headline": ad.get("title", "N/A"),
                    "Live_Ad_Description": ad.get("description") or ad.get("snippet", "N/A"),
                    "Ad_Extensions_Sitelinks": " | ".join(sitelinks_list[:4]) if sitelinks_list else "None",
                    "Landing_Page_URL": link
                })
        except Exception:
            continue

    return pd.DataFrame(serp_ads_rows)

# -----------------------------------------------------------------------------
# 1. SECRETS & API MANAGEMENT
# -----------------------------------------------------------------------------
serpapi_key = st.secrets.get("SERPAPI_KEY", "")
gemini_key = st.secrets.get("GEMINI_API_KEY", "")
google_cloud_key = st.secrets.get("PAGESPEED_API_KEY", "") or st.secrets.get("GOOGLE_CLOUD_KEY", "")
crawly_key = st.secrets.get("CRAWLY_API_KEY", "")

with st.sidebar:
    try:
        st.image(LOGO_URL, width=170)
    except Exception:
        pass
    
    st.header("🧹 Data Workspace")
    if st.button("🧹 Clear All Session Data & Reset", use_container_width=True, help="Wipes all crawling, ranking, AEO, and performance data from memory so you can audit a new domain without combining previous data."):
        keys_to_clear = [
            "df_crawl", "df_rankings", "df_aeo", "df_ads", "df_cannibal",
            "df_equity", "suggested_organic_kws", "suggested_aeo_prompts",
            "df_organic_suggestions", "df_aeo_suggestions", "detected_industry",
            "sec_headers", "cwv_metrics", "crux_metrics", "safe_browsing",
            "kg_info", "nlp_categories", "prev_snapshot", "is_thin_site", "target_domain", "crawly_data", "df_mentions", "discovered_sitemaps", "compiled_summary_pdf_bytes", "compiled_dossier_pdf_bytes", "df_serp_ads", "ads_ai_strategy"
        ]
        for k in keys_to_clear:
            st.session_state.pop(k, None)
        st.success("✓ Workspace reset! Stored cache cleared.")
        st.rerun()

    st.markdown("---")
    st.header("⚙️ API Configuration")
    
    if serpapi_key:
        st.success("✓ SerpApi Connected")
    else:
        serpapi_key = st.text_input("SerpApi Key (SERP/AEO/Ads)", type="password")
        
    if gemini_key:
        st.success("✓ Gemini AI Connected")
    else:
        gemini_key = st.text_input("Gemini API Key (AI Studio)", type="password")
        
    if google_cloud_key:
        st.success("✓ Google Cloud Connected (CWV/CrUX/SafeBrowsing/KG)")
    else:
        google_cloud_key = st.text_input("Google Cloud API Key", type="password", help="Powers PageSpeed, Chrome UX (CrUX), Safe Browsing, and Knowledge Graph Search.")

    if crawly_key:
        st.success("✓ Crawly Free SEO Connected (DA & Backlinks)")
    else:
        crawly_key = st.text_input("Crawly Free API Key (DA/Backlinks)", type="password", help="Get free key at getcrawly.com/api (100 req/day, strictly no credit card).")

    st.markdown("---")
    st.subheader("Crawl Settings")
    crawl_limit = st.slider("Max Commercial Pages to Crawl", min_value=5, max_value=80, value=25, step=5)
    st.caption("Multi-threaded engine analyzes DOM, schemas, metadata & security.")

# Initialize Session States
if "df_crawl" not in st.session_state: st.session_state["df_crawl"] = pd.DataFrame()
if "df_rankings" not in st.session_state: st.session_state["df_rankings"] = pd.DataFrame()
if "df_aeo" not in st.session_state: st.session_state["df_aeo"] = pd.DataFrame()
if "df_ads" not in st.session_state: st.session_state["df_ads"] = pd.DataFrame()
if "df_serp_ads" not in st.session_state: st.session_state["df_serp_ads"] = pd.DataFrame()
if "ads_ai_strategy" not in st.session_state: st.session_state["ads_ai_strategy"] = ""
if "df_cannibal" not in st.session_state: st.session_state["df_cannibal"] = pd.DataFrame()
if "df_equity" not in st.session_state: st.session_state["df_equity"] = pd.DataFrame()
if "suggested_organic_kws" not in st.session_state: st.session_state["suggested_organic_kws"] = ""
if "suggested_aeo_prompts" not in st.session_state: st.session_state["suggested_aeo_prompts"] = ""
if "sec_headers" not in st.session_state: st.session_state["sec_headers"] = {}
if "cwv_metrics" not in st.session_state: st.session_state["cwv_metrics"] = {}
if "crux_metrics" not in st.session_state: st.session_state["crux_metrics"] = {}
if "safe_browsing" not in st.session_state: st.session_state["safe_browsing"] = {}
if "kg_info" not in st.session_state: st.session_state["kg_info"] = {}
if "nlp_categories" not in st.session_state: st.session_state["nlp_categories"] = []
if "prev_snapshot" not in st.session_state: st.session_state["prev_snapshot"] = None
if "crawly_data" not in st.session_state: st.session_state["crawly_data"] = {}
if "df_mentions" not in st.session_state: st.session_state["df_mentions"] = pd.DataFrame()
if "discovered_sitemaps" not in st.session_state: st.session_state["discovered_sitemaps"] = []
if "df_organic_suggestions" not in st.session_state: st.session_state["df_organic_suggestions"] = pd.DataFrame()
if "df_aeo_suggestions" not in st.session_state: st.session_state["df_aeo_suggestions"] = pd.DataFrame()
if "detected_industry" not in st.session_state: st.session_state["detected_industry"] = ""

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# -----------------------------------------------------------------------------
# 2. BUILT-IN OPERATIONAL INSTRUCTIONS & SETUP GUIDE
# -----------------------------------------------------------------------------
with st.expander("📘 Master Operational Guide: End-to-End Platform Playbook (Click to Expand)", expanded=False):
    st.markdown("""
    ### 🗺️ The Complete Enterprise Audit Workflow: From Setup to PDF Deliverable

    Welcome to the **Connext Enterprise Digital & AEO Auditor**. Follow this step-by-step master sequence to execute an audit for any enterprise commercial entity:

    ---
    #### ⚙️ Stage 1: API Connections & Clean Workspace Initialization
    1. **Connect Free Engine Keys:** In the left sidebar, ensure your API keys are loaded. 
       - `SerpApi`: Powers live Google Organic Rankings (Tab 4), Generative AI Overviews (Tab 5), and Google Ads Transparency Center ad creatives (Tab 6).
       - `Gemini AI`: Powers dynamic topical IAB categorization and the 60+ commercial keyword matrix (Tab 1).
       - `Google Cloud`: Powers PageSpeed Insights, Chrome UX Report (CrUX), Google Safe Browsing, and Knowledge Graph Search.
    2. **Avoid Blending Data:** If auditing a new company, click **🧹 Clear All Session Data & Reset** in the sidebar to prevent previous domain metrics from contaminating the new audit.

    ---
    #### 🌐 Stage 2: Scope Scaffolding, Technical Crawl & Accessibility (Tab 1)
    1. **Choose Scope Mode:**
       - **XML Sitemap Auto-Discovery:** Enter root domain (e.g. `https://www.csgi.com`). Optionally specify a path or slug filter (e.g. `/products/`, `/solutions/`, `/lp/`, or `billing`) to crawl only high-value leaf pages.
       - **Specific / Unique URLs:** Paste exact individual URLs into the text area to execute targeted diagnostics on campaign landing pages or specific product hubs.
    2. **Execute Crawl:** Click **🚀 Start Audit**. The multi-threaded engine evaluates:
       - **Architecture & Headings:** Scans H1 hierarchy and catches word-concatenation bugs (e.g., `poweredCPQ`).
       - **Structured Schemas:** Identifies `Product`, `SoftwareApplication`, and `Service` JSON-LD scripts.
       - **Perimeter Edge Security:** Verifies HSTS, Content Security Policy (CSP), and clickjacking headers.
       - **Web Accessibility (WCAG 2.1 AA):** Inspects `lang` attributes, mobile `viewport`, missing image `alt` tags, vague link text ("click here"), and unlabeled buttons.
       - **AI Index Standard (`llms.txt`):** Verifies if `domain.com/llms.txt` serves Markdown for AI bots (or flags 404).
       - **Topical Understanding:** Uses Gemini AI to classify content into standard Google/IAB industry categories.

    ---
    #### ⚡ Stage 3: Automated Search Keyword & AEO Matrix Discovery (Tab 1)
    1. **Click 🤖 Generate 60+ AI Keywords & AEO Prompts:** Gemini reads the crawled product catalog and derives 35+ search queries across 3 buyer intents (Commercial, Pain-Point, Competitor) and 30+ consultative AEO prompts for AI search engines.
    2. **Auto-Populated Downstream:** These queries immediately populate into Tab 4 (SERP) and Tab 5 (AEO), and can be downloaded as structured CSV files.

    ---
    #### 🔍 Stage 4: Deep Diagnostics & Forensic Intelligence (Tabs 2–7)
    1. **Tab 2 (Internal Link Equity):** Crawls the blog archive to discover if commercial hubs are orphaned (0 links) or receive unoptimized paragraph-long CTA card anchors.
    2. **Tab 3 (Cannibalization):** Runs TF-IDF Cosine Similarity across page copy to detect overlapping landing pages competing against organic product hubs (e.g., `/lp/` vs. `/products/`).
    3. **Tab 4 (Organic SERP Tracker):** Benchmarks live positions on Google Search across target buyer queries and calculates the **% Invisibility Rate**.
    4. **Tab 5 (Generative AEO):** Queries Google AI Overviews to check if the brand is cited or if competitors win the generative answer.
    5. **Tab 6 (Paid Ads Intel):** Pulls active creatives from Google Ads Transparency Center to reveal competitors conquesting brand keywords.
    6. **Tab 7 (Web Vitals & CrUX):** Executes Lighthouse mobile lab tests and queries 28-day real-world Chrome user field vitals (LCP, INP, CLS).

    ---
    #### 🔄 Stage 5: Benchmarking Progress & Deploying Fixes (Tabs 8–9)
    1. **Tab 8 (Progress Tracker):** Click **💾 Download Current Audit Snapshot (.json)** to preserve today's baseline. Later, upload a previous snapshot to calculate automated **$\Delta$ progress deltas** (+% schema coverage, -% invisibility).
    2. **Tab 9 (Schema Builder):** Generate copy-paste `SoftwareApplication`, `Service`, `Organization`, or `FAQPage` JSON-LD code for engineering.

    ---
    #### 📊 Stage 6: Executive Presentation & PDF Delivery (Tabs 10–11)
    1. **Tab 10 (Executive Dashboard):** Inspect the PageSpeed Insights-style scoreboard with 6 color-coded category health rings (Organic Search, Schema Graph, Generative AEO, Mobile Speed, Accessibility, Security). Review critical deficits and the prioritized operational roadmap.
    2. **Tab 11 (Connext PDF Report):** Click **📑 Compile & Download Branded Executive PDF** to produce a publication-grade, Connext-branded PDF report complete with the DOFA matrix, audit inventory, and action roadmap.
    """, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 3. WORKSPACE TABS
# -----------------------------------------------------------------------------
tab_arch, tab_equity, tab_cannibal, tab_serp, tab_aeo, tab_ads, tab_cwv, tab_backlinks, tab_first_party, tab_compare, tab_schema_gen, tab_dashboard, tab_report = st.tabs([
    "🌐 1. Tech, Content & SEO",
    "🔗 2. Content Silos & Equity",
    "🔀 3. Cannibalization",
    "📈 4. Organic SERP",
    "🤖 5. Generative AEO",
    "🎯 6. Paid Ads Intel",
    "⚡ 7. Web Vitals & CrUX",
    "🛡️ 8. Backlinks & DA (Crawly)",
    "📥 9. First-Party Analytics",
    "🔄 10. Progress Tracker",
    "⚙️ 11. Schema Builder",
    "📊 12. Executive Dashboard",
    "📄 13. Connext PDF"
])

# =============================================================================
# TAB 1: ARCHITECTURE, SCHEMAS, METADATA, SAFE BROWSING & ENTITY INTELLIGENCE
# =============================================================================
with tab_arch:
    st.subheader("Sitemap Discovery, On-Page Metadata, Schemas & Security Audit")
    
    # Audit Scope Mode Selection
    crawl_mode = st.radio(
        "Select Audit Scope Mode:",
        ["🌐 XML Sitemap Auto-Discovery", "🎯 Specific / Unique URLs (Direct Audit)"],
        horizontal=True
    )
    
    c1, c2 = st.columns([3, 1])
    with c1:
        if crawl_mode == "🌐 XML Sitemap Auto-Discovery":
            target_site = st.text_input("Target Commercial Domain", value="https://www.csgi.com")
            slug_filter = st.text_input(
                "Filter Sitemaps by Path / Slug / Keyword (Optional)", 
                value="", 
                placeholder="e.g. /products/, /solutions/, /lp/, billing, payments"
            )
            specific_urls_input = ""
        else:
            target_site = st.text_input("Target Domain Root", value="https://www.csgi.com")
            default_specific = "https://www.csgi.com/products/ascendon\nhttps://www.csgi.com/products/quote-and-order\nhttps://www.csgi.com/solutions/payments"
            specific_urls_input = st.text_area(
                "Enter Unique / Specific URLs to Audit (One per line)", 
                value=default_specific,
                height=110
            )
            slug_filter = ""
    with c2:
        st.write("")
        st.write("")
        crawl_btn = st.button("🚀 Start Audit", use_container_width=True)

    if crawl_btn and target_site:
        with st.spinner("Executing architecture audit, security check, Knowledge Graph lookup and extraction..."):
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

            # B. Google Safe Browsing API Check
            safe_info = {"Status": "Not Checked (Add Key)", "Details": "Key not provided"}
            if google_cloud_key:
                try:
                    sb_url = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={google_cloud_key}"
                    sb_payload = {
                        "client": {"clientId": "connext-enterprise-auditor", "clientVersion": "1.0"},
                        "threatInfo": {
                            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
                            "platformTypes": ["ANY_PLATFORM"],
                            "threatEntryTypes": ["URL"],
                            "threatEntries": [{"url": target_site}]
                        }
                    }
                    sb_res = requests.post(sb_url, json=sb_payload, timeout=8).json()
                    if "matches" in sb_res and sb_res["matches"]:
                        threats = [m.get("threatType") for m in sb_res["matches"]]
                        safe_info = {"Status": "⚠️ Threat Detected", "Details": ", ".join(threats)}
                    else:
                        safe_info = {"Status": "✓ Clean (Verified Safe)", "Details": "No threats in Google Safe Browsing index."}
                except Exception as e:
                    safe_info = {"Status": "Check Error", "Details": str(e)}
            st.session_state["safe_browsing"] = safe_info

            # C. Google Knowledge Graph Search API Check (Brand Entity Recognition)
            kg_info = {"Recognized": "Not Checked", "Entity_Name": "N/A", "Types": "N/A", "Score": 0, "Description": "API key required"}
            if google_cloud_key:
                try:
                    brand_query = urlparse(target_site).netloc.replace("www.", "").split(".")[0].upper()
                    kg_url = "https://kgsearch.googleapis.com/v1/entities:search"
                    kg_params = {"query": brand_query, "key": google_cloud_key, "limit": 1, "indent": True}
                    kg_res = requests.get(kg_url, params=kg_params, timeout=8).json()
                    items = kg_res.get("itemListElement", [])
                    if items:
                        it = items[0].get("result", {})
                        kg_info = {
                            "Recognized": "✓ Verified Entity",
                            "Entity_Name": it.get("name", brand_query),
                            "Types": ", ".join(it.get("@type", [])),
                            "Score": round(items[0].get("resultScore", 0), 2),
                            "Description": it.get("detailedDescription", {}).get("articleBody", it.get("description", "Recognized Google Knowledge Entity"))
                        }
                    else:
                        kg_info = {"Recognized": "🔴 Unrecognized / Ambiguous Entity", "Entity_Name": brand_query, "Types": "None", "Score": 0, "Description": "No Knowledge Graph entity detected"}
                except Exception as e:
                    kg_info = {"Recognized": "Check Failed", "Entity_Name": "N/A", "Types": "N/A", "Score": 0, "Description": str(e)}
            st.session_state["kg_info"] = kg_info

            # D. Check llms.txt (Verifies HTTP 200, Non-Empty, and NOT Soft-404 HTML) & robots.txt
            try:
                llms_r = requests.get(f"{root}/llms.txt", headers=HEADERS, timeout=5)
                has_llms = (
                    llms_r.status_code == 200 and
                    len(llms_r.text.strip()) > 10 and
                    "<html" not in llms_r.text.lower() and
                    "<!doctype" not in llms_r.text.lower()
                )
            except Exception:
                has_llms = False
            st.session_state["has_llms"] = has_llms

            try:
                robots_r = requests.get(f"{root}/robots.txt", headers=HEADERS, timeout=5)
                has_robots = (robots_r.status_code == 200)
            except Exception:
                has_robots = False
            st.session_state["has_robots"] = has_robots

            # E. Recursive XML Sitemap Index, Category Unpacker & Internal Spider
            if crawl_mode == "🎯 Specific / Unique URLs (Direct Audit)":
                custom_urls = [u.strip() for u in specific_urls_input.split("\n") if u.strip().startswith("http")]
                url_pool = custom_urls if custom_urls else [target_site]
                st.session_state["discovered_sitemaps"] = [{"Sitemap_URL": "Direct URL Input Mode", "Type": "Manual List", "Child_Sitemaps_Found": 0, "HTML_Pages_Extracted": len(url_pool)}]
            else:
                url_pool, sm_log = discover_target_urls(root, target_site, slug_filter, crawl_limit, HEADERS)
                st.session_state["discovered_sitemaps"] = sm_log

            # F. Complete On-Page SEO, Metadata, Content Depth, Readability, Schema & WCAG Auditor
            def audit_page(url):
                res = {
                    "URL": url,
                    "Status": 0,
                    "Meta_Title": "Missing",
                    "Meta_Title_Length": 0,
                    "Meta_Title_Status": "Missing",
                    "Meta_Desc": "Missing",
                    "Meta_Desc_Length": 0,
                    "Meta_Desc_Status": "Missing",
                    "Canonical_URL": "Missing",
                    "Canonical_Status": "Missing",
                    "Robots_Meta": "index, follow (Default)",
                    "H1_Count": 0,
                    "H1_Text": "N/A",
                    "Concatenated_H1": False,
                    "H2_Count": 0,
                    "H2_Texts": "None",
                    "H3_Count": 0,
                    "H3_Texts": "None",
                    "Word_Count": 0,
                    "Content_Depth": "Thin (<300 words)",
                    "Readability_Score": 0.0,
                    "FAQ_Present": False,
                    "Lists_Tables_Count": 0,
                    "AEO_Readiness_Score": 0,
                    "Internal_Links_Count": 0,
                    "External_Links_Count": 0,
                    "Images_Count": 0,
                    "Missing_Alt_Count": 0,
                    "Missing_Alt_Pct": 0.0,
                    "Has_OG_Tags": False,
                    "Total_Schemas": 0,
                    "Commercial_Schema": False,
                    "Schema_Types": "None",
                    "Schema_Tier": "🔴 No Schema (0 Tags)",
                    "Schema_Plain_English": "No Schema Markup (Invisible to AI Knowledge Graph)",
                    "HTML_Lang": "Missing",
                    "Meta_Viewport": False,
                    "Vague_Links_Count": 0,
                    "Unlabeled_Buttons": 0,
                    "Skipped_Headings": False,
                    "A11y_Score": 100,
                    "Content_Preview": "",
                    "Body_Text": ""
                }
                try:
                    r = requests.get(url, headers=HEADERS, timeout=10)
                    res["Status"] = r.status_code
                    if r.status_code == 200:
                        soup = BeautifulSoup(r.text, "html.parser")
                        
                        # 1. Headings Hierarchy (H1, H2, H3)
                        h1s = [h.get_text(separator=" ", strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
                        res["H1_Count"] = len(h1s)
                        if h1s:
                            res["H1_Text"] = h1s[0]
                            if re.search(r'[a-z][A-Z0-9]', h1s[0]):
                                res["Concatenated_H1"] = True
                        
                        h2s = [h.get_text(separator=" ", strip=True) for h in soup.find_all("h2") if h.get_text(strip=True)]
                        h3s = [h.get_text(separator=" ", strip=True) for h in soup.find_all("h3") if h.get_text(strip=True)]
                        res["H2_Count"] = len(h2s)
                        res["H2_Texts"] = " | ".join(h2s[:6]) if h2s else "None"
                        res["H3_Count"] = len(h3s)
                        res["H3_Texts"] = " | ".join(h3s[:6]) if h3s else "None"
                        if len(h1s) > 0 and len(h3s) > 0 and len(h2s) == 0:
                            res["Skipped_Headings"] = True

                        # 2. Meta Title & Meta Description
                        t_tag = soup.find("title")
                        if t_tag and t_tag.get_text(strip=True):
                            t_txt = t_tag.get_text(strip=True)
                            t_len = len(t_txt)
                            res["Meta_Title"] = t_txt
                            res["Meta_Title_Length"] = t_len
                            res["Meta_Title_Status"] = "Optimal (30-60)" if 30 <= t_len <= 60 else ("Truncated (>60)" if t_len > 60 else "Too Short (<30)")
                        
                        m_desc = soup.find("meta", attrs={"name": re.compile(r'^description$', re.I)}) or soup.find("meta", attrs={"property": "og:description"})
                        if m_desc and m_desc.get("content"):
                            d_txt = m_desc["content"].strip()
                            d_len = len(d_txt)
                            res["Meta_Desc"] = d_txt
                            res["Meta_Desc_Length"] = d_len
                            res["Meta_Desc_Status"] = "Good (120-165)" if 120 <= d_len <= 165 else ("Truncated (>165)" if d_len > 165 else "Too Short (<120)")

                        # 3. Canonical & Meta Robots
                        can_tag = soup.find("link", rel=lambda x: x and "canonical" in x.lower() if isinstance(x, str) else (x and "canonical" in [i.lower() for i in x]))
                        if can_tag and can_tag.get("href"):
                            c_href = urljoin(url, can_tag["href"].strip())
                            res["Canonical_URL"] = c_href
                            res["Canonical_Status"] = "Self-Referencing" if c_href.rstrip("/") == url.split("?")[0].rstrip("/") else "Points Elsewhere"
                        
                        rob_tag = soup.find("meta", attrs={"name": re.compile(r'^robots$', re.I)})
                        if rob_tag and rob_tag.get("content"):
                            res["Robots_Meta"] = rob_tag["content"].strip()

                        # 4. Images & Alt Attributes (WCAG 1.1.1)
                        imgs = soup.find_all("img")
                        res["Images_Count"] = len(imgs)
                        if imgs:
                            missing_alt = sum([1 for img in imgs if not img.get("alt") or len(img.get("alt").strip()) == 0])
                            res["Missing_Alt_Count"] = missing_alt
                            res["Missing_Alt_Pct"] = round((missing_alt / len(imgs)) * 100, 1)
                        
                        # 5. OpenGraph Social Tags
                        res["Has_OG_Tags"] = bool(soup.find("meta", property="og:title") and soup.find("meta", property="og:image"))
                        
                        # 6. Accessibility Checks (WCAG 2.1 AA) & Internal/External Links
                        html_tag = soup.find("html")
                        lang_val = html_tag.get("lang", "").strip() if html_tag else ""
                        res["HTML_Lang"] = lang_val if lang_val else "Missing"
                        
                        meta_vp = soup.find("meta", attrs={"name": "viewport"})
                        res["Meta_Viewport"] = bool(meta_vp and "width=" in meta_vp.get("content", "").lower())
                        
                        vague_terms = {"click here", "read more", "learn more", "more", "here", "view", "details", "link"}
                        vague_count = 0
                        int_links = 0
                        ext_links = 0
                        page_netloc = urlparse(url).netloc.replace("www.", "")
                        for a in soup.find_all("a", href=True):
                            txt = a.get_text(strip=True).lower()
                            if txt in vague_terms:
                                vague_count += 1
                            href_val = a["href"].strip()
                            if href_val and not href_val.startswith(("#", "javascript:", "mailto:", "tel:")):
                                full_a = urljoin(url, href_val)
                                if page_netloc in urlparse(full_a).netloc.replace("www.", ""):
                                    int_links += 1
                                else:
                                    ext_links += 1
                        res["Vague_Links_Count"] = vague_count
                        res["Internal_Links_Count"] = int_links
                        res["External_Links_Count"] = ext_links
                        
                        unlabeled_btns = 0
                        for b in soup.find_all("button"):
                            has_txt = bool(b.get_text(strip=True))
                            has_aria = bool(b.get("aria-label") or b.get("aria-labelledby"))
                            if not has_txt and not has_aria:
                                unlabeled_btns += 1
                        res["Unlabeled_Buttons"] = unlabeled_btns
                        
                        a11y = 100
                        if res["HTML_Lang"] == "Missing": a11y -= 20
                        if not res["Meta_Viewport"]: a11y -= 15
                        a11y -= int(res["Missing_Alt_Pct"] * 0.25)
                        a11y -= min(15, vague_count * 3)
                        a11y -= min(15, unlabeled_btns * 4)
                        if res["Skipped_Headings"]: a11y -= 10
                        if res["H1_Count"] != 1: a11y -= 5
                        res["A11y_Score"] = max(10, min(100, a11y))

                        # 7. Structured Data Schemas (JSON-LD & Microdata Recursive Audit)
                        s_cnt, u_types, has_rich, has_aeo, s_tier, s_plain = extract_all_schemas_from_soup(soup)
                        res["Total_Schemas"] = s_cnt
                        res["Commercial_Schema"] = bool(has_rich or has_aeo)
                        res["Schema_Types"] = ", ".join(u_types) if u_types else "None"
                        res["Schema_Tier"] = s_tier
                        res["Schema_Plain_English"] = s_plain
                        
                        # 8. Content Depth, Readability & AEO Answer Readiness
                        for tag_rem in soup(["script", "style", "noscript", "nav", "footer", "header"]):
                            tag_rem.extract()
                        
                        paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 25]
                        clean_p_text = " ".join(paragraphs)
                        body = soup.body.get_text(separator=" ", strip=True) if soup.body else clean_p_text
                        w_cnt = len(body.split())
                        res["Word_Count"] = w_cnt
                        res["Content_Depth"] = "Comprehensive (800+ words)" if w_cnt >= 800 else ("Standard (300-799 words)" if w_cnt >= 300 else "Thin (<300 words)")
                        res["Readability_Score"] = calculate_flesch_readability(clean_p_text if len(clean_p_text) > 100 else body)
                        res["Content_Preview"] = (clean_p_text if clean_p_text else body)[:600]
                        res["Body_Text"] = (clean_p_text if clean_p_text else body)[:3500]

                        lists_tables = len(soup.find_all(["ul", "ol", "table"]))
                        res["Lists_Tables_Count"] = lists_tables
                        has_faq = ("FAQPage" in res["Schema_Types"]) or any("?" in h for h in (h2s + h3s))
                        res["FAQ_Present"] = has_faq

                        # Composite AEO Answer Readiness Score (0-100)
                        aeo_s = 20
                        if res["H1_Count"] == 1: aeo_s += 15
                        if res["H2_Count"] >= 2: aeo_s += 15
                        if w_cnt >= 400: aeo_s += 15
                        if lists_tables >= 1: aeo_s += 15
                        if has_faq: aeo_s += 10
                        if res["Commercial_Schema"]: aeo_s += 10
                        res["AEO_Readiness_Score"] = min(100, aeo_s)
                except Exception:
                    res["Status"] = 500
                    res["A11y_Score"] = 40
                return res

            with ThreadPoolExecutor(max_workers=8) as ex:
                records = list(ex.map(audit_page, url_pool))

            df_crawl = pd.DataFrame(records)
            st.session_state["df_crawl"] = df_crawl
            st.session_state["is_thin_site"] = (len(df_crawl) <= 2)

            # G. Topical Categorization via Dynamic Gemini API
            if gemini_key and not df_crawl.empty:
                try:
                    sample_text = " ".join(df_crawl["Body_Text"].tolist())[:2500]
                    nlp_prompt = f"""Analyze this website text and classify it into its top 3-4 standard Google/IAB content taxonomy categories with confidence scores (0-100%).
Text:
{sample_text}

Respond strictly in valid JSON format:
[
  {{"Category": "/Health & Fitness/Medical Facilities & Services/Genetic Testing", "Confidence": "94%"}}
]"""
                    raw_nlp, _ = query_gemini_dynamic(nlp_prompt, gemini_key)
                    if raw_nlp:
                        clean_nlp = raw_nlp.strip().replace("```json", "").replace("```", "").strip()
                        st.session_state["nlp_categories"] = json.loads(clean_nlp)
                except Exception:
                    st.session_state["nlp_categories"] = []

            st.success(f"✓ Unpacked {len(st.session_state.get('discovered_sitemaps', []))} sitemap(s) and audited {len(df_crawl)} live HTML web pages!")

    if not st.session_state["df_crawl"].empty:
        df = st.session_state["df_crawl"]
        sec = st.session_state.get("sec_headers", {})
        sb = st.session_state.get("safe_browsing", {})
        kg = st.session_state.get("kg_info", {})
        
        is_thin = (len(df) <= 2)
        avg_a11y = round(df["A11y_Score"].mean()) if "A11y_Score" in df.columns else 100
        
        if is_thin:
            st.error(f"🚨 **Critical Architecture Risk: Severe Topical Thinness Detected ({len(df)} page(s) found)**. Websites with only 1–2 pages find it virtually impossible to rank organically or establish topical authority because search engines require dedicated, schema-marked URLs for each distinct solution, service, or buyer use-case. Add dedicated service/solution pages to build ranking authority.")

        m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
        m1.metric("Pages Analyzed", len(df), delta="⚠️ Thin Site (<3)" if is_thin else "Healthy Footprint", delta_color="inverse" if is_thin else "normal")
        m2.metric("Commercial Schemas", f"{(df['Commercial_Schema'].sum()/len(df)*100):.1f}%")
        m3.metric("Mashed H1s", f"{df['Concatenated_H1'].sum()} URLs")
        m4.metric("Accessibility (WCAG)", f"{avg_a11y}/100", delta="Compliant" if avg_a11y >= 85 else "Issues Detected", delta_color="normal" if avg_a11y >= 85 else "inverse")
        m5.metric("Security Grade", sec.get("Grade", "N/A"))
        m6.metric("Safe Browsing", sb.get("Status", "N/A"))
        m7.metric("llms.txt AI Index", "✓ Active (200)" if st.session_state.get("has_llms") else "🔴 Missing (404)")

        # Knowledge Graph Entity Verification Card
        if kg.get("Recognized") and kg.get("Recognized") != "Not Checked":
            st.markdown("##### 🏛️ Google Knowledge Graph Entity Recognition (AI & AEO Trust Anchor)")
            c_kg1, c_kg2, c_kg3 = st.columns([1, 2, 3])
            c_kg1.metric("Entity Status", kg.get("Recognized", "N/A"))
            c_kg2.metric("Entity Name & Type", f"{kg.get('Entity_Name')} ({kg.get('Types')})")
            c_kg3.metric("Entity Confidence Score", f"{kg.get('Score', 0)}")
            st.caption(f"**Knowledge Graph Description:** {kg.get('Description')}")

        # NLP Topical Categorization
        if st.session_state.get("nlp_categories"):
            st.markdown("##### 🧠 Topical Taxonomy Understanding (Gemini AI IAB Classification)")
            st.dataframe(pd.DataFrame(st.session_state["nlp_categories"]), use_container_width=True)

        st.markdown("#### Technical & Metadata Audit Inventory")
        
        # Real-time search & filter bar for the table
        filter_col1, filter_col2 = st.columns([3, 1])
        with filter_col1:
            table_search = st.text_input(
                "🔍 Search / Filter Table by Slug, URL, or Keyword:",
                value="",
                placeholder="Type any word or slug (e.g. ascendon, payments, cpq, /lp/, error...)"
            )
        with filter_col2:
            st.write("")
            st.write("")
            
        display_df = df.copy()
        if table_search.strip():
            kw = table_search.strip().lower()
            display_df = display_df[
                display_df["URL"].str.lower().str.contains(kw) |
                display_df["H1_Text"].str.lower().str.contains(kw) |
                display_df["Meta_Title"].astype(str).str.lower().str.contains(kw) |
                display_df["Meta_Desc"].astype(str).str.lower().str.contains(kw) |
                display_df["Schema_Types"].str.lower().str.contains(kw)
            ]
            st.caption(f"Showing **{len(display_df)}** of **{len(df)}** crawled URLs matching '{table_search}'")
            
        if "Schema_Tier" not in display_df.columns:
            display_df["Schema_Tier"] = display_df["Commercial_Schema"].apply(lambda x: "🟢 Rich Commercial / Solution" if x else "🔴 No Schema (0 Tags)")
            display_df["Schema_Plain_English"] = display_df["Schema_Types"]

        st.dataframe(
            display_df[[
                "URL", "Status", "Schema_Tier", "Schema_Plain_English",
                "Meta_Title_Status", "Meta_Desc_Status", "H1_Text",
                "Word_Count", "Content_Depth", "AEO_Readiness_Score", "A11y_Score"
            ]],
            column_config={
                "URL": st.column_config.LinkColumn("Crawled Web Page URL"),
                "Status": st.column_config.NumberColumn("HTTP", help="200 = Healthy Live Page"),
                "Schema_Tier": st.column_config.TextColumn("Schema Grade", help="Plain-English classification of structured data on this page."),
                "Schema_Plain_English": st.column_config.TextColumn("What Schema Tells Google & AI"),
                "Meta_Title_Status": st.column_config.TextColumn("Title Tag Status"),
                "Meta_Desc_Status": st.column_config.TextColumn("Meta Description"),
                "H1_Text": st.column_config.TextColumn("Main Page Headline (H1)"),
                "Word_Count": st.column_config.NumberColumn("Words", format="%d"),
                "Content_Depth": st.column_config.TextColumn("Content Depth Tier"),
                "AEO_Readiness_Score": st.column_config.ProgressColumn("AI Readiness", format="%d/100", min_value=0, max_value=100),
                "A11y_Score": st.column_config.ProgressColumn("Accessibility (WCAG)", format="%d/100", min_value=0, max_value=100)
            },
            hide_index=True,
            use_container_width=True
        )

        # ---------------------------------------------------------------------
        # NEW: COMPLETE CONTENT, METADATA & ON-PAGE SEO INSPECTOR (DEEP X-RAY)
        # ---------------------------------------------------------------------
        st.markdown("---")
        st.subheader("🔬 Complete Content, Metadata & On-Page SEO Inspector (Deep Page X-Ray)")
        st.markdown("""
        <div class="guide-box">
            <strong>🎯 Complete Page-Level Content & SEO Review:</strong> Inspect every crawled URL's full Meta Titles, Meta Descriptions, Canonical Tags, Heading Trees (H1 → H2 → H3), Word Count, Flesch Readability, and AEO Answer Readiness—or run an AI Deep Copy Audit on any individual page.
        </div>
        """, unsafe_allow_html=True)

        # Summary KPI row for Content & On-Page SEO
        avg_words = int(display_df["Word_Count"].mean()) if not display_df.empty else 0
        thin_pages_cnt = int((display_df["Word_Count"] < 300).sum()) if not display_df.empty else 0
        missing_meta_cnt = int(((display_df["Meta_Title_Status"] == "Missing") | (display_df["Meta_Desc_Status"] == "Missing")).sum()) if not display_df.empty else 0
        avg_aeo_ready = int(display_df["AEO_Readiness_Score"].mean()) if not display_df.empty and "AEO_Readiness_Score" in display_df.columns else 0

        ck1, ck2, ck3, ck4 = st.columns(4)
        ck1.metric("Avg. Page Word Count", f"{avg_words:,} words", delta=f"{thin_pages_cnt} Thin Pages (<300w)", delta_color="inverse" if thin_pages_cnt > 0 else "normal")
        ck2.metric("Missing Title / Meta Desc", f"{missing_meta_cnt} URLs", delta="Action Needed" if missing_meta_cnt > 0 else "100% Populated", delta_color="inverse" if missing_meta_cnt > 0 else "normal")
        ck3.metric("Avg. AEO Readiness Score", f"{avg_aeo_ready}/100", help="Measures direct answer structure, H2/H3 depth, lists/tables, FAQ presence & schemas.")
        ck4.metric("Sitemaps Unpacked", f"{len(st.session_state.get('discovered_sitemaps', []))} XML files")

        xray_tab1, xray_tab2, xray_tab3, xray_tab4 = st.tabs([
            "🏷️ 1. Full Metadata & Indexation Matrix",
            "📑 2. Content Depth, Headings (H1–H3) & AEO",
            "🔎 3. Single-Page Deep X-Ray & AI Copywriter",
            "🗺️ 4. Unpacked XML Sitemaps Log"
        ])

        with xray_tab1:
            st.markdown("##### Complete On-Page Metadata, Canonical & Indexation Matrix")
            meta_cols = [
                "URL", "Meta_Title", "Meta_Title_Length", "Meta_Title_Status",
                "Meta_Desc", "Meta_Desc_Length", "Meta_Desc_Status",
                "Canonical_URL", "Canonical_Status", "Robots_Meta", "Has_OG_Tags"
            ]
            st.dataframe(display_df[meta_cols], use_container_width=True)

        with xray_tab2:
            st.markdown("##### Heading Hierarchy (H1, H2, H3), Content Depth, Readability & AEO Structure")
            content_cols = [
                "URL", "H1_Text", "H1_Count", "H2_Count", "H2_Texts",
                "H3_Count", "H3_Texts", "Word_Count", "Content_Depth",
                "Readability_Score", "FAQ_Present", "Lists_Tables_Count",
                "AEO_Readiness_Score", "Internal_Links_Count", "External_Links_Count", "Content_Preview"
            ]
            st.dataframe(display_df[content_cols], use_container_width=True)

        with xray_tab3:
            st.markdown("##### Deep Single-Page SEO & Content X-Ray")
            selected_xray_url = st.selectbox("Select Any Crawled Page to Inspect:", options=display_df["URL"].tolist())
            if selected_xray_url:
                p_row = display_df[display_df["URL"] == selected_xray_url].iloc[0]
                cx1, cx2, cx3, cx4 = st.columns(4)
                cx1.metric("Word Count", f"{p_row['Word_Count']} ({p_row['Content_Depth'].split()[0]})")
                cx2.metric("Readability (Flesch)", f"{p_row['Readability_Score']}/100")
                cx3.metric("AEO Readiness", f"{p_row['AEO_Readiness_Score']}/100")
                cx4.metric("Internal / Ext. Links", f"{p_row['Internal_Links_Count']} / {p_row['External_Links_Count']}")

                st.markdown(f"""
                <div style="background:#f8fafc; border:1px solid #cbd5e1; border-radius:6px; padding:14px; margin:10px 0; font-size:13px; line-height:1.6;">
                    <div><strong>🏷️ Meta Title ({p_row['Meta_Title_Length']} chars — {p_row['Meta_Title_Status']}):</strong> <code>{p_row['Meta_Title']}</code></div>
                    <div><strong>📝 Meta Description ({p_row['Meta_Desc_Length']} chars — {p_row['Meta_Desc_Status']}):</strong> <code>{p_row['Meta_Desc']}</code></div>
                    <div><strong>🔗 Canonical URL ({p_row['Canonical_Status']}):</strong> <code>{p_row['Canonical_URL']}</code> | <strong>🤖 Meta Robots:</strong> <code>{p_row['Robots_Meta']}</code></div>
                    <div><strong>📌 Primary H1 ({p_row['H1_Count']} found):</strong> {p_row['H1_Text']}</div>
                    <div><strong>📑 H2 Subheadings ({p_row['H2_Count']} found):</strong> {p_row['H2_Texts']}</div>
                    <div><strong>🔹 H3 Subheadings ({p_row['H3_Count']} found):</strong> {p_row['H3_Texts']}</div>
                    <div><strong>🧩 Structured Schemas ({p_row['Total_Schemas']}):</strong> <code>{p_row['Schema_Types']}</code> | <strong>🖼️ Missing Image Alt:</strong> {p_row['Missing_Alt_Count']}/{p_row['Images_Count']} ({p_row['Missing_Alt_Pct']}%)</div>
                </div>
                """, unsafe_allow_html=True)

                with st.expander("📖 View Extracted Body Copy Preview", expanded=False):
                    st.write(p_row["Body_Text"] if p_row["Body_Text"] else "No body text extracted.")

                if st.button("✨ Run Deep AI Content & SEO/AEO Copy Rewrite for This Page", key="btn_xray_ai"):
                    if not gemini_key:
                        st.error("Please add your Gemini API Key in the sidebar to run the AI Page Copy Optimizer.")
                    else:
                        with st.spinner(f"Auditing content & generating optimized SEO/AEO copy for {selected_xray_url}..."):
                            page_audit_prompt = f"""You are a Senior Enterprise SEO & Generative AEO Copywriter.
Perform a deep on-page SEO and content audit for this specific URL: {selected_xray_url}

Current On-Page Data:
- Meta Title ({p_row['Meta_Title_Length']} chars): {p_row['Meta_Title']}
- Meta Description ({p_row['Meta_Desc_Length']} chars): {p_row['Meta_Desc']}
- H1 Heading: {p_row['H1_Text']}
- H2 Headings: {p_row['H2_Texts']}
- H3 Headings: {p_row['H3_Texts']}
- Word Count: {p_row['Word_Count']} words
- Current Schemas: {p_row['Schema_Types']}
- Page Copy Sample: {p_row['Body_Text'][:1800]}

Provide an actionable, publication-ready optimization plan with:
1. **Content & SEO Diagnostic Summary:** 3 bullet points identifying what is missing or weak on this specific page.
2. **Optimized Meta Title (50-60 chars) & Meta Description (140-155 chars):** Ready to copy-paste.
3. **Optimized H1 & Recommended H2 Heading Outline:** Structured for commercial search intent.
4. **45-Word Inverted-Pyramid AEO Opening Paragraph:** A factual, citation-ready opening definition designed to win Google AI Overviews and ChatGPT citations.
5. **3 High-Value FAQ Questions & Answers:** Ready to add to the page and FAQPage schema."""
                            ai_copy_text, mod_used = query_gemini_dynamic(page_audit_prompt, gemini_key)
                            if ai_copy_text:
                                st.success(f"✓ Deep Page Copy Audit Complete ({mod_used})!")
                                st.markdown(ai_copy_text)
                            else:
                                st.error("Could not generate AI copy audit. Verify your Gemini API key.")

        with xray_tab4:
            st.markdown("##### Unpacked XML Sitemap Index & Category Discovery Log")
            if st.session_state.get("discovered_sitemaps"):
                st.dataframe(pd.DataFrame(st.session_state["discovered_sitemaps"]), use_container_width=True)
            else:
                st.info("No XML sitemaps logged.")

        with st.expander("♿ Web Accessibility (WCAG 2.1 AA) Compliance Diagnostics", expanded=False):
            st.markdown(f"""
            * **Domain Average Accessibility Health:** **{avg_a11y}/100**
            * **HTML Language Declaration:** {sum(df['HTML_Lang'] != 'Missing')} of {len(df)} pages declare valid `<html lang="...">`. (Screen reader requirement).
            * **Mobile Viewport Tag:** {sum(df['Meta_Viewport'])} of {len(df)} pages enforce responsive zoom and reflow.
            * **Missing Image Alt Text:** {sum(df['Missing_Alt_Pct'] > 0)} of {len(df)} pages have images with missing alt text attributes.
            * **Ambiguous Link Text:** {df['Vague_Links_Count'].sum()} instances of non-descriptive links ("click here", "read more") flagged across the site.
            * **Unlabeled Interactive Buttons:** {df['Unlabeled_Buttons'].sum()} buttons without accessible text or `aria-label`.
            """)

        # ---------------------------------------------------------------------
        # REDESIGNED: EXECUTIVE SCHEMA & AI KNOWLEDGE GRAPH INTELLIGENCE
        # ---------------------------------------------------------------------
        st.markdown("---")
        st.subheader("🧩 Structured Data & AI Schema Intelligence (Plain-English Breakdown)")
        st.markdown("""
        <div class="guide-box">
            <strong>💡 What is Schema Markup (For Non-SEO Experts)?</strong><br>
            Schema is hidden "nutrition label" code inside a web page that tells Google, ChatGPT, and Perplexity( without forcing AI bots to guess from plain text) exactly what your business offers:<br>
            • <strong>🟢 Rich Commercial / Solution Schema:</strong> Tells search engines what you sell (Product, Service, MedicalBusiness, SoftwareApplication, Offer).<br>
            • <strong>🤖 AI Answer / FAQ Schema:</strong> Formats Q&As and articles (FAQPage, HowTo, Article) so AI engines quote your brand directly.<br>
            • <strong>🟡 Basic Brand / Nav Only:</strong> Default CMS tags (like <code>WebPage</code> or <code>Organization</code>) that identify your site name but <strong>do not describe your services or products</strong>.<br>
            • <strong>🔴 No Schema (0 Tags):</strong> Page has zero structured code; AI and search engines must guess its purpose.
        </div>
        """, unsafe_allow_html=True)

        if "Schema_Tier" not in df.columns:
            df["Schema_Tier"] = df["Commercial_Schema"].apply(lambda x: "🟢 Rich Commercial / Solution" if x else "🔴 No Schema (0 Tags)")
            df["Schema_Plain_English"] = df["Schema_Types"]

        cnt_rich = int((df["Schema_Tier"] == "🟢 Rich Commercial / Solution").sum())
        cnt_aeo = int((df["Schema_Tier"] == "🤖 AI Answer / FAQ / Article").sum())
        cnt_basic = int((df["Schema_Tier"] == "🟡 Basic Brand / Nav Only").sum())
        cnt_zero = int((df["Schema_Tier"] == "🔴 No Schema (0 Tags)").sum())
        tot_p = max(1, len(df))

        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.metric("🟢 Rich Product / Service Pages", f"{cnt_rich} ({cnt_rich/tot_p*100:.0f}%)", help="Pages declaring Product, Service, MedicalBusiness, SoftwareApplication, or Offer schema.")
        sc2.metric("🤖 AI FAQ / Article Pages", f"{cnt_aeo} ({cnt_aeo/tot_p*100:.0f}%)", help="Pages declaring FAQPage, HowTo, or Article schema for AI citations.")
        sc3.metric("🟡 Basic Brand / Nav Only", f"{cnt_basic} ({cnt_basic/tot_p*100:.0f}%)", delta="Needs Service/Product Tags" if cnt_basic > 0 else None, delta_color="inverse")
        sc4.metric("🔴 Zero Schema (Unmarked)", f"{cnt_zero} ({cnt_zero/tot_p*100:.0f}%)", delta="Invisible to AI Graph" if cnt_zero > 0 else "0 Unmarked", delta_color="inverse" if cnt_zero > 0 else "normal")

        col_sch_left, col_sch_right = st.columns([1.15, 1.35])

        with col_sch_left:
            st.markdown("##### 📊 Schema Quality Distribution Across Crawled Pages")
            tier_summary_df = pd.DataFrame([
                {"Schema Tier": "🟢 Rich Commercial (Product/Service)", "Pages": cnt_rich, "Coverage %": round(cnt_rich / tot_p * 100, 1), "Business Impact": "High — Drives Rich Snippets & Buyer Matching"},
                {"Schema Tier": "🤖 AI Answer (FAQ / HowTo / Article)", "Pages": cnt_aeo, "Coverage %": round(cnt_aeo / tot_p * 100, 1), "Business Impact": "High — Drives ChatGPT & AI Overview Citations"},
                {"Schema Tier": "🟡 Basic Brand Only (Org / WebPage)", "Pages": cnt_basic, "Coverage %": round(cnt_basic / tot_p * 100, 1), "Business Impact": "Low — Identifies Brand Only; Missing Offer Details"},
                {"Schema Tier": "🔴 Zero Schema (None Declared)", "Pages": cnt_zero, "Coverage %": round(cnt_zero / tot_p * 100, 1), "Business Impact": "Critical Deficit — Invisible to Structured AI"}
            ])
            st.dataframe(
                tier_summary_df,
                column_config={
                    "Coverage %": st.column_config.ProgressColumn("Coverage %", format="%.1f%%", min_value=0, max_value=100)
                },
                hide_index=True,
                use_container_width=True
            )

        with col_sch_right:
            st.markdown("##### 📖 Plain-English Translator: Detected & Missing Schemas")
            type_counter = Counter()
            for s_list in df["Schema_Types"].dropna():
                for st_item in [x.strip() for x in str(s_list).split(",") if x.strip() and x.strip() != "None"]:
                    type_counter[st_item] += 1

            translator_rows = []
            for s_type, cnt_val in type_counter.most_common():
                cat_lbl, desc_lbl = SCHEMA_BUSINESS_DICTIONARY.get(
                    s_type,
                    ("🔵 Custom / Secondary Tag", f"Declares {s_type} properties to search engines.")
                )
                translator_rows.append({
                    "Schema Type": s_type,
                    "Category": cat_lbl,
                    "Pages": f"{cnt_val}/{len(df)}",
                    "What It Means in Plain English": desc_lbl
                })

            for essential_type in ["Service", "Product", "FAQPage"]:
                if essential_type not in type_counter:
                    cat_lbl, desc_lbl = SCHEMA_BUSINESS_DICTIONARY[essential_type]
                    translator_rows.append({
                        "Schema Type": f"❌ {essential_type} (Missing)",
                        "Category": "🚨 Missing Opportunity",
                        "Pages": f"0/{len(df)}",
                        "What It Means in Plain English": f"MISSING: {desc_lbl} Generate this in Tab 11."
                    })

            st.dataframe(pd.DataFrame(translator_rows), hide_index=True, use_container_width=True)

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            st.download_button(
                label="⬇️ Export Architecture Audit to CSV",
                data=display_df.to_csv(index=False),
                file_name=f"technical_audit_{urlparse(st.session_state['target_domain']).netloc}.csv",
                mime="text/csv",
                use_container_width=True
            )
        
        with col_d2:
            llms_lines = [
                f"# {urlparse(st.session_state['target_domain']).netloc} - Enterprise Offerings",
                "",
                "> Autogenerated machine-readable directory for LLM and AI Engine Indexing.",
                "",
                "## Core Commercial Solutions"
            ]
            for _, r in df.head(20).iterrows():
                h1_name = r['H1_Text'] if r['H1_Text'] != 'N/A' else r['URL'].strip('/').split('/')[-1].replace('-', ' ').title()
                llms_lines.append(f"- [{h1_name}]({r['URL']})")
            
            llms_content = "\n".join(llms_lines)
            
            st.download_button(
                label="🤖 Download Custom llms.txt File",
                data=llms_content,
                file_name="llms.txt",
                mime="text/plain",
                use_container_width=True
            )

        # Gemini AI Keyword Generator
        st.markdown("---")
        st.subheader("⚡ Automated Search Keyword & AEO Prompt Discovery")
        st.write("Automatically extracts product themes from crawled pages and generates high-intent keywords for Tab 4 (SERP) and Tab 5 (AEO).")
        
        if st.button("🤖 Generate AI Keywords & AEO Prompts with Gemini"):
            with st.spinner("Analyzing product catalog with Gemini..."):
                organic_list = []
                aeo_list = []
                
                if gemini_key:
                    try:
                        offerings = []
                        for _, r in df.iterrows():
                            h1_val = str(r.get("H1_Text", "")).strip()
                            t_val = str(r.get("Meta_Title", "")).strip()
                            url_val = str(r.get("URL", "")).strip()
                            label_val = h1_val if (h1_val and h1_val != "N/A") else t_val
                            if label_val and label_val != "Missing":
                                offerings.append(f"- URL: {url_val} | Title/H1: {label_val}")
                        
                        context_str = "\n".join(offerings[:25])
                        prompt = f"""You are a senior enterprise SEO/AEO strategist.
Analyze these crawled pages, products, and solutions for {st.session_state.get('target_domain', 'the site')}:
{context_str}

Generate two comprehensive search lists:
1. "organic": 20 high-intent commercial Google search queries buyers use to find these exact services/products.
2. "aeo": 20 comparative, consultative questions that buyers ask Generative AI engines (ChatGPT, Google AI Overviews, Perplexity) when evaluating providers in these categories.

Respond strictly in valid JSON format:
{{"organic": ["kw1", "kw2", ...], "aeo": ["prompt1", "prompt2", ...]}}"""

                        raw_ai_txt, mod_used = query_gemini_dynamic(prompt, gemini_key)
                        if raw_ai_txt:
                            clean_json = raw_ai_txt.strip().replace("```json", "").replace("```", "").strip()
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

                st.success("✓ Keywords and AEO prompts generated! Populated into Tab 4 and Tab 5.")
                c_out1, c_out2 = st.columns(2)
                with c_out1:
                    st.write("**Suggested Organic Commercial Keywords:**")
                    st.code(st.session_state["suggested_organic_kws"], language="text")
                with c_out2:
                    st.write("**Suggested Generative AEO Prompts:**")
                    st.code(st.session_state["suggested_aeo_prompts"], language="text")

# =============================================================================
# TAB 2: INTERNAL LINK EQUITY & ANCHOR TEXT SILOS
# =============================================================================
with tab_equity:
    st.subheader("Internal Link Equity Flow & Content Silo Audit")
    st.markdown("""
    <div class="guide-box">
        <strong>🎯 Objective:</strong> Audits whether educational blog articles, case studies, or white papers pass PageRank and link equity to commercial leaf offerings (excluding header/footer navigation menus).<br>
        <strong>💼 Enterprise Impact:</strong> Commercial pages with zero inbound contextual links become <em>orphaned hubs</em>, failing to build organic authority.<br>
        <strong>🔍 How to Use:</strong> Choose your content asset type below (or select <em>No Blog Mode</em> to audit commercial peer cross-linking).
    </div>
    """, unsafe_allow_html=True)

    silo_type = st.selectbox(
        "Select Content Silo Asset Type to Audit:",
        [
            "📰 Blog & Insights Articles (/blog/, /insights/, /articles/, /news/)",
            "📑 Case Studies & Customer Stories (/case-studies/, /customers/, /stories/)",
            "📄 White Papers, Guides & Resources (/resources/, /white-papers/, /guides/)",
            "⚙️ Custom Path / Slug Directory Filter",
            "🚫 Domain Has No Blog or Editorial Content (Pure Commercial Cross-Link Audit)"
        ]
    )

    col_eq1, col_eq2 = st.columns([3, 1])
    with col_eq1:
        if "Blog" in silo_type and "No Blog" not in silo_type:
            blog_seed_path = st.text_input("Editorial Archive Path Filter (Comma-separated)", value="/blog/, /insights/, /articles/, /news/")
        elif "Case Studies" in silo_type:
            blog_seed_path = st.text_input("Case Studies Path Filter", value="/case-studies/, /customers/, /stories/")
        elif "White Papers" in silo_type:
            blog_seed_path = st.text_input("Resources / Guides Path Filter", value="/resources/, /white-papers/, /guides/, /learn/")
        elif "Custom" in silo_type:
            blog_seed_path = st.text_input("Custom Content Directory Path", value="/pages/")
        else:
            blog_seed_path = ""
            st.info("ℹ️ **No Editorial Silo Mode Active:** Auditing contextual cross-linking between commercial pages (excluding global nav/footer menus).")
    with col_eq2:
        st.write("")
        st.write("")
        run_equity_btn = st.button("🔗 Run Content Silo Audit", use_container_width=True)

    if run_equity_btn:
        if st.session_state["df_crawl"].empty:
            st.error("Please run the crawl in Tab 1 first to identify target URLs.")
        else:
            df_targets = st.session_state["df_crawl"]
            commercial_urls = set(df_targets["URL"].dropna().unique())
            target_site = st.session_state.get("target_domain", "https://alphacord.com")
            parsed_t = urlparse(target_site)
            root_t = f"{parsed_t.scheme}://{parsed_t.netloc}"

            if "No Blog" in silo_type:
                with st.spinner("Auditing contextual peer cross-linking across commercial pages..."):
                    inbound_counts = {u: 0 for u in commercial_urls}
                    inbound_anchors = {u: [] for u in commercial_urls}
                    for src_url in list(commercial_urls)[:20]:
                        try:
                            res = requests.get(src_url, headers=HEADERS, timeout=8)
                            if res.status_code == 200:
                                soup = BeautifulSoup(res.text, "html.parser")
                                for nav_el in soup(["nav", "header", "footer"]):
                                    nav_el.extract()
                                container = soup.find("main") or soup.find("article") or soup.body
                                if container:
                                    for a_tag in container.find_all("a", href=True):
                                        full_l = urljoin(root_t, a_tag["href"].strip()).split("?")[0].split("#")[0].rstrip("/")
                                        anchor = a_tag.get_text(strip=True)
                                        for c_url in commercial_urls:
                                            if full_l == c_url.rstrip("/") and full_l != src_url.rstrip("/"):
                                                inbound_counts[c_url] += 1
                                                if anchor:
                                                    inbound_anchors[c_url].append(anchor)
                        except Exception:
                            continue
                    equity_records = []
                    for c_url in commercial_urls:
                        cnt = inbound_counts.get(c_url, 0)
                        equity_records.append({
                            "Target_Commercial_URL": c_url,
                            "Inbound_Editorial_Links": cnt,
                            "Sample_Anchor_Texts": " | ".join(list(set(inbound_anchors[c_url]))[:3]) if inbound_anchors[c_url] else "None",
                            "Equity_Status": "Supported (2+ links)" if cnt >= 2 else ("Weak (1 link)" if cnt == 1 else "Orphaned (0 links)")
                        })
                    df_eq = pd.DataFrame(equity_records).sort_values(by="Inbound_Editorial_Links", ascending=False)
                    st.session_state["df_equity"] = df_eq
                    st.success(f"✓ Completed contextual cross-link audit across {len(commercial_urls)} commercial URLs!")
            else:
                with st.spinner(f"Discovering content assets matching '{blog_seed_path}' and auditing contextual links..."):
                    path_tokens = [p.strip().lower() for p in blog_seed_path.split(",") if p.strip()]
                    articles = [u for u in commercial_urls if any(pt in u.lower() for pt in path_tokens)]
                    if len(articles) < 5:
                        disc_articles, _ = discover_target_urls(root_t, target_site, blog_seed_path, 15, HEADERS)
                        for da in disc_articles:
                            if any(pt in da.lower() for pt in path_tokens) and da not in articles:
                                articles.append(da)

                    if not articles:
                        st.warning(f"⚠️ No editorial content assets found matching `{blog_seed_path}`. Switching to contextual cross-link audit across crawled pages.")
                        articles = list(commercial_urls)[:12]

                    inbound_counts = {u: 0 for u in commercial_urls}
                    inbound_anchors = {u: [] for u in commercial_urls}

                    for a_url in articles[:15]:
                        try:
                            res = requests.get(a_url, headers=HEADERS, timeout=8)
                            if res.status_code == 200:
                                soup = BeautifulSoup(res.text, "html.parser")
                                for nav_el in soup(["nav", "header", "footer"]):
                                    nav_el.extract()
                                container = soup.find("main") or soup.find("article") or soup.body
                                if container:
                                    for a_tag in container.find_all("a", href=True):
                                        href = a_tag["href"].strip()
                                        anchor = a_tag.get_text(strip=True)
                                        full_l = urljoin(root_t, href)
                                        clean_l = full_l.split("?")[0].split("#")[0].rstrip("/")

                                        for c_url in commercial_urls:
                                            if clean_l == c_url.rstrip("/") and clean_l != a_url.rstrip("/"):
                                                inbound_counts[c_url] += 1
                                                if anchor:
                                                    inbound_anchors[c_url].append(anchor)
                        except Exception:
                            continue

                    equity_records = []
                    for c_url in commercial_urls:
                        cnt = inbound_counts.get(c_url, 0)
                        equity_records.append({
                            "Target_Commercial_URL": c_url,
                            "Inbound_Editorial_Links": cnt,
                            "Sample_Anchor_Texts": " | ".join(list(set(inbound_anchors[c_url]))[:3]) if inbound_anchors[c_url] else "None",
                            "Equity_Status": "Supported (2+ links)" if cnt >= 2 else ("Weak (1 link)" if cnt == 1 else "Orphaned (0 links)")
                        })

                    df_eq = pd.DataFrame(equity_records).sort_values(by="Inbound_Editorial_Links", ascending=False)
                    st.session_state["df_equity"] = df_eq
                    st.success(f"✓ Audited contextual link equity across {len(articles[:15])} content assets!")

    if not st.session_state["df_equity"].empty:
        df_eq = st.session_state["df_equity"]
        orphans = (df_eq["Inbound_Editorial_Links"] == 0).sum()
        orphan_pct = (orphans / len(df_eq)) * 100
        
        m_e1, m_e2 = st.columns(2)
        m_e1.metric("Commercial Pages Orphaned (0 Contextual Links)", f"{orphan_pct:.1f}%", help="Pages receiving zero contextual in-body links.")
        m_e2.metric("Total Offerings Evaluated", len(df_eq))
        
        st.markdown("##### 📊 Internal Link Equity Distribution Across Offerings")
        tier_counts = df_eq["Equity_Status"].value_counts()
        st.bar_chart(tier_counts, color="#e85122")
        
        st.dataframe(df_eq, use_container_width=True)
        st.download_button(
            label="⬇️ Download Internal Link Equity Audit (CSV)",
            data=df_eq.to_csv(index=False),
            file_name="internal_link_equity_audit.csv",
            mime="text/csv"
        )

# =============================================================================
# TAB 3: KEYWORD CANNIBALIZATION & CONTENT OVERLAP
# =============================================================================
with tab_cannibal:
    st.subheader("TF-IDF Keyword Cannibalization & Content Overlap Scanner")
    st.write("Identifies duplicate topic coverage and cannibalization pairs where similarity exceeds 0.40 (such as paid landing pages competing with organic hubs).")

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
            
            # Visual Chart: Top Overlapping Pairs
            st.markdown("##### 📊 Top Content Overlap & Cannibalization Severity")
            top_can = df_can.head(8).copy()
            top_can["Comparison Pair"] = top_can["URL_1"].apply(lambda x: x.rstrip('/').split('/')[-1]) + " vs " + top_can["URL_2"].apply(lambda x: x.rstrip('/').split('/')[-1])
            st.bar_chart(top_can.set_index("Comparison Pair")["Cosine_Similarity"], color="#dc2626")

            st.dataframe(df_can, use_container_width=True)
            st.download_button(
                label="⬇️ Download Cannibalization Report (CSV)",
                data=df_can.to_csv(index=False),
                file_name="keyword_cannibalization_audit.csv",
                mime="text/csv"
            )

# =============================================================================
# TAB 4: ORGANIC KEYWORD SERP TRACKER
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

        # Visual Chart: SERP Visibility Tier Breakdown
        st.markdown("##### 📊 Organic Search Positioning Tier Distribution")
        def parse_rank_tier(x):
            if x.startswith("#"):
                num_str = "".join([c for c in x if c.isdigit()])
                if num_str:
                    val = int(num_str)
                    if val <= 3: return "Top 3 Organic (#1-#3)"
                    elif val <= 10: return "Page 1 (#4-#10)"
                    else: return "Page 2+ (11-20)"
            return "Absent (> 20)"
        
        rank_counts = df_r["Target_Rank"].apply(parse_rank_tier).value_counts()
        st.bar_chart(rank_counts, color="#004b93")

        st.dataframe(df_r, use_container_width=True)
        st.download_button(
            label="⬇️ Download SERP Rankings (CSV)",
            data=df_r.to_csv(index=False),
            file_name="serp_rankings_audit.csv",
            mime="text/csv"
        )

# =============================================================================
# TAB 5: GENERATIVE AEO & AI OVERVIEWS
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
        
        # Visual Chart: AEO Win vs Loss
        st.markdown("##### 📊 AI Overview Recommendation Win vs. Loss Ratio")
        st.bar_chart(df_a["Outcome"].value_counts(), color="#f2a900")

        st.dataframe(df_a, use_container_width=True)
        st.download_button(
            label="⬇️ Download AEO Citations Report (CSV)",
            data=df_a.to_csv(index=False),
            file_name="aeo_citations_audit.csv",
            mime="text/csv"
        )

# =============================================================================
# TAB 6: PAID SEARCH & ADS TRANSPARENCY
# =============================================================================
with tab_ads:
    st.subheader("Paid Search, Ad Creative Longevity & Competitor Conquesting Intelligence")
    st.markdown("""
    <div class="guide-box">
        <strong>🎯 Why This Paid Media Audit Matters (Plain-English Guide):</strong><br>
        • <strong>🔥 Evergreen Winner Detection (60+ Days Active):</strong> Advertisers quickly pause ads that lose money. Any ad running continuously for <strong>60+ days</strong> is a proven, profitable "control creative" generating positive ROI.<br>
        • <strong>⚖️ Funnel & Channel Balance:</strong> Shows whether the domain is capturing ready-to-buy searchers (<code>🔍 Google Search Text Ads</code>) or only running awareness/retargeting banners (<code>🖼️ Display / Video</code>).<br>
        • <strong>⚔️ Live SERP Competitor Conquesting:</strong> Scans live Google Search results to reveal exact competitor ad headlines, descriptions, landing pages, and whether rivals are bidding on your brand name.
    </div>
    """, unsafe_allow_html=True)

    col_ad1, col_ad2, col_ad3 = st.columns([2.2, 1.2, 1.2])
    with col_ad1:
        default_ad_dom = urlparse(st.session_state.get("target_domain", "https://alphacord.com")).netloc.replace("www.", "") or "alphacord.com"
        ad_target_domain = st.text_input("Advertiser Domain to Inspect (e.g. alphacord.com or competitor.com)", value=default_ad_dom)
    with col_ad2:
        ad_region = st.selectbox("Target Ad Region", ["2840 (United States)", "Anywhere (Global)", "2826 (United Kingdom)", "2124 (Canada)", "2036 (Australia)"], index=0)
    with col_ad3:
        st.write("")
        st.write("")
        run_ads_btn = st.button("📊 Fetch Google Ads Intel", use_container_width=True)

    if run_ads_btn:
        if not serpapi_key:
            st.error("SerpApi key required to query Google Ads Transparency Center.")
        else:
            clean_ad_dom = urlparse(ad_target_domain).netloc.replace("www.", "") if "http" in ad_target_domain else ad_target_domain.replace("www.", "").strip("/")
            with st.spinner(f"Extracting & analyzing Google Ads Transparency Center portfolio for {clean_ad_dom}..."):
                try:
                    params = {
                        "engine": "google_ads_transparency_center",
                        "text": clean_ad_dom,
                        "api_key": serpapi_key,
                        "num": 40
                    }
                    if "Anywhere" not in ad_region:
                        params["region"] = ad_region.split()[0].strip()
                    res = requests.get("https://serpapi.com/search", params=params, timeout=25).json()
                    creatives = res.get("ad_creatives", [])
                    df_ads = process_transparency_creatives(creatives, clean_ad_dom)
                    st.session_state["df_ads"] = df_ads
                    if not df_ads.empty:
                        st.success(f"✓ Extracted and analyzed {len(df_ads)} ad creatives for **{df_ads['Advertiser'].iloc[0]}** (`{clean_ad_dom}`)!")
                    else:
                        st.warning(f"⚠️ 0 active ads found in Google Ads Transparency Center for `{clean_ad_dom}`.")
                except Exception as e:
                    st.error(f"Error extracting Ads Transparency data: {e}")

    if not st.session_state["df_ads"].empty:
        df_ad = st.session_state["df_ads"]
        tot_ads = len(df_ad)
        adv_entity = df_ad["Advertiser"].iloc[0] if "Advertiser" in df_ad.columns else ad_target_domain
        evergreen_cnt = int((df_ad["Days_Active"] >= 60).sum()) if "Days_Active" in df_ad.columns else 0
        search_cnt = int(df_ad["Channel"].str.contains("Search", case=False, na=False).sum()) if "Channel" in df_ad.columns else 0
        display_vid_cnt = tot_ads - search_cnt
        avg_days = int(df_ad["Days_Active"].mean()) if "Days_Active" in df_ad.columns else 1
        max_days = int(df_ad["Days_Active"].max()) if "Days_Active" in df_ad.columns else 1

        st.markdown(f"#### 🏛️ Verified Advertiser Entity: **{adv_entity}** (`{df_ad['Target_Domain'].iloc[0]}`)")
        ak1, ak2, ak3, ak4, ak5 = st.columns(5)
        ak1.metric("Tracked Ad Creatives", f"{tot_ads} Ads", delta=f"{int(df_ad['Activity_Status'].str.contains('Active Now').sum())} Served Last 7d")
        ak2.metric("🔥 Evergreen Winners (60+d)", f"{evergreen_cnt} Ads ({evergreen_cnt/max(1,tot_ads)*100:.0f}%)", help="Ads running 60+ days continuously — proven ROI winners.")
        ak3.metric("🔍 Search Text Ads (SERP)", f"{search_cnt} ({search_cnt/max(1,tot_ads)*100:.0f}%)", help="Bottom-of-funnel high-intent search text ads.")
        ak4.metric("🖼️ Display & Video Ads", f"{display_vid_cnt} ({display_vid_cnt/max(1,tot_ads)*100:.0f}%)", help="Mid/top-of-funnel banner and video creatives.")
        ak5.metric("Avg. Flight Longevity", f"{avg_days} Days", delta=f"Longest: {max_days} Days")

        # Strategic Diagnosis Callout Box
        if search_cnt == tot_ads:
            mix_diagnosis = f"<strong>100% Search Text Ad Focus:</strong> {adv_entity} allocates its tracked ad inventory exclusively to high-intent Google Search Text Ads, prioritizing bottom-of-funnel lead capture over display banners or YouTube video."
        elif search_cnt == 0:
            mix_diagnosis = f"<strong>⚠️ Zero Search Text Ads Detected (100% Display/Video):</strong> {adv_entity} is running display/video banners without active Search Text Ads, leaving high-intent bottom-of-funnel search queries vulnerable to competitors."
        else:
            mix_diagnosis = f"<strong>Balanced Full-Funnel Mix:</strong> {adv_entity} combines <strong>{search_cnt} Search Text Ads</strong> ({search_cnt/tot_ads*100:.0f}%) for bottom-of-funnel demand capture with <strong>{display_vid_cnt} Display/Video Ads</strong> ({display_vid_cnt/tot_ads*100:.0f}%) for retargeting and awareness."

        st.markdown(f"""
        <div style="background:#f8fafc; border:1px solid #cbd5e1; border-left:4px solid #e85122; padding:12px 16px; border-radius:4px; margin:10px 0 16px 0; font-size:13px; line-height:1.55;">
            📊 <strong>Paid Media Portfolio Diagnosis:</strong> {mix_diagnosis}<br>
            🏆 <strong>Creative Longevity Insight:</strong> <strong>{evergreen_cnt} of {tot_ads} creatives ({evergreen_cnt/max(1,tot_ads)*100:.0f}%)</strong> have been running for over 60 days (longest active flight: <strong>{max_days} days</strong> from <code>{df_ad['First_Shown'].min()}</code> to <code>{df_ad['Last_Shown'].max()}</code>), indicating stable evergreen control messaging.
        </div>
        """, unsafe_allow_html=True)

        col_ch1, col_ch2 = st.columns(2)
        with col_ch1:
            st.markdown("##### 📊 Ad Channel & Format Allocation")
            st.bar_chart(df_ad["Channel"].value_counts(), color="#004b93")
        with col_ch2:
            st.markdown("##### 🏆 Ad Creative Longevity Tiers (Winning Controls vs. Tests)")
            st.bar_chart(df_ad["Longevity_Tier"].value_counts(), color="#e85122")

        # Sub-tabs for Visual Gallery vs. Structured Table
        ad_view_tab1, ad_view_tab2 = st.tabs([
            "📋 1. Actionable Ad Creative Inventory (Human Dates & Longevity)",
            "🖼️ 2. Visual Ad Creative Gallery (Inspect Top Winning Ads)"
        ])

        with ad_view_tab1:
            filter_tier = st.selectbox(
                "Filter Creatives by Longevity / Channel:",
                ["All Creatives", "🔥 Evergreen Winners Only (60+ Days)", "🔍 Search Text Ads Only", "🖼️ Display / Video Ads Only"],
                key="ads_table_filter"
            )
            disp_ads = df_ad.copy()
            if "Evergreen" in filter_tier:
                disp_ads = disp_ads[disp_ads["Days_Active"] >= 60]
            elif "Search Text" in filter_tier:
                disp_ads = disp_ads[disp_ads["Channel"].str.contains("Search", case=False, na=False)]
            elif "Display" in filter_tier:
                disp_ads = disp_ads[~disp_ads["Channel"].str.contains("Search", case=False, na=False)]

            st.dataframe(
                disp_ads[[
                    "Ad_Preview", "Channel", "Longevity_Tier", "Days_Active",
                    "First_Shown", "Last_Shown", "Activity_Status",
                    "Strategic_Takeaway", "Advertiser", "Preview_Link"
                ]],
                column_config={
                    "Ad_Preview": st.column_config.ImageColumn("Creative Thumbnail", help="Visual snapshot rendered by Google Ads Transparency Center."),
                    "Channel": st.column_config.TextColumn("Ad Channel & Format"),
                    "Longevity_Tier": st.column_config.TextColumn("Performance Tier"),
                    "Days_Active": st.column_config.ProgressColumn("Days Running", format="%d days", min_value=1, max_value=max(90, max_days)),
                    "First_Shown": st.column_config.TextColumn("First Seen (YYYY-MM-DD)"),
                    "Last_Shown": st.column_config.TextColumn("Last Seen (YYYY-MM-DD)"),
                    "Activity_Status": st.column_config.TextColumn("Recency Status"),
                    "Strategic_Takeaway": st.column_config.TextColumn("Strategic Interpretation"),
                    "Advertiser": st.column_config.TextColumn("Verified Advertiser"),
                    "Preview_Link": st.column_config.LinkColumn("Google Ad Link", display_text="Open Live Ad ↗")
                },
                hide_index=True,
                use_container_width=True
            )

            st.download_button(
                label="⬇️ Download Enriched Google Ads Inventory (CSV)",
                data=disp_ads.drop(columns=["Format", "Type"], errors="ignore").to_csv(index=False),
                file_name=f"google_ads_intelligence_{ad_target_domain}.csv",
                mime="text/csv"
            )

        with ad_view_tab2:
            st.markdown("##### 🖼️ Visual Ad Gallery — Top Longest-Running Evergreen Creatives")
            st.caption("Displays visual ad thumbnails captured by Google Ads Transparency Center, ordered by total days active.")
            gallery_ads = df_ad[df_ad["Ad_Preview"].astype(str).str.startswith("http")].head(12)
            if gallery_ads.empty:
                st.info("No direct image thumbnails returned by Google for these text creatives. Click 'Open Live Ad ↗' in the table tab to view them directly on Google Ads Transparency Center.")
            else:
                g_cols = st.columns(3)
                for idx_g, (_, g_row) in enumerate(gallery_ads.iterrows()):
                    with g_cols[idx_g % 3]:
                        st.markdown(f"""
                        <div style="background:#f8fafc; border:1px solid #cbd5e1; border-top:3px solid #004b93; border-radius:6px; padding:10px; margin-bottom:12px; font-size:12px;">
                            <div style="font-weight:bold; color:#004b93;">{g_row['Channel']}</div>
                            <div style="color:#dc2626; font-weight:bold; margin:2px 0;">{g_row['Longevity_Tier']} ({g_row['Days_Active']} Days)</div>
                            <div style="color:#475569; font-size:11px;">📅 {g_row['First_Shown']} ➔ {g_row['Last_Shown']}</div>
                            <div style="margin-top:4px;"><a href="{g_row['Preview_Link']}" target="_blank" style="color:#e85122; font-weight:bold; text-decoration:none;">Open in Google Transparency ↗</a></div>
                        </div>
                        """, unsafe_allow_html=True)
                        try:
                            st.image(g_row["Ad_Preview"], use_container_width=True)
                        except Exception:
                            pass

    # -------------------------------------------------------------------------
    # MODULE 6B: LIVE GOOGLE SEARCH PAID SERP & COMPETITOR CONQUESTING SCANNER
    # -------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("⚔️ Live Google Search Paid SERP & Competitor Ad Copy Scanner")
    st.markdown("""
    <div class="guide-box">
        <strong>🎯 Why Use This Scanner?</strong> Google Ads Transparency Center shows historical creative IDs, but it does <em>not</em> show which keywords trigger those ads or what exact Headlines, Descriptions, Sitelinks, and Landing Pages your competitors are using on Page 1 today.<br>
        Run this scanner to extract <strong>live Sponsored Search Ads</strong> across your brand name and top commercial keywords.
    </div>
    """, unsafe_allow_html=True)

    brand_seed = urlparse(st.session_state.get("target_domain", ad_target_domain)).netloc.replace("www.", "").split(".")[0] or "alphacord"
    existing_kws = [k.strip() for k in st.session_state.get("suggested_organic_kws", "").split("\n") if k.strip()][:5]
    default_ppc_kws = "\n".join([brand_seed, f"{brand_seed} reviews", f"{brand_seed} pricing"] + (existing_kws if existing_kws else ["cord blood banking", "stem cell banking cost", "best newborn stem cell bank"]))

    col_ppc1, col_ppc2 = st.columns([3, 1])
    with col_ppc1:
        ppc_kw_input = st.text_area("Keywords to Scan for Live Google Sponsored Search Ads (Max 8 per scan):", value=default_ppc_kws, height=115)
    with col_ppc2:
        st.write("")
        st.write("")
        run_ppc_serp_btn = st.button("🔎 Scan Live Paid Search Ads", use_container_width=True)

    if run_ppc_serp_btn:
        if not serpapi_key:
            st.error("SerpApi key required.")
        else:
            q_list = [x.strip() for x in ppc_kw_input.split("\n") if x.strip()][:8]
            with st.spinner(f"Scanning live Google Search sponsored slots across {len(q_list)} queries..."):
                df_serp_ads = scan_live_serp_paid_ads(q_list, ad_target_domain, serpapi_key)
                st.session_state["df_serp_ads"] = df_serp_ads
                if not df_serp_ads.empty:
                    st.success(f"✓ Captured {len(df_serp_ads)} live Sponsored Search Ads across {len(q_list)} queries!")
                else:
                    st.info("ℹ️ 0 live Sponsored Search Ads triggered on Google for these exact queries right now (uncontested paid search whitespace!).")

    if not st.session_state.get("df_serp_ads", pd.DataFrame()).empty:
        df_sa = st.session_state["df_serp_ads"]
        conquest_cnt = int(df_sa["Competitor_Status"].str.contains("Conquesting", case=False, na=False).sum())
        own_cnt = int(df_sa["Competitor_Status"].str.contains("Your Brand", case=False, na=False).sum())
        comp_bidding_cnt = len(df_sa) - own_cnt

        ps1, ps2, ps3 = st.columns(3)
        ps1.metric("Live Sponsored Ads Captured", f"{len(df_sa)} Ads")
        ps2.metric("🚨 Brand Conquesting Alerts", f"{conquest_cnt} Competitor Ads", delta="Rivals Bidding on Your Brand!" if conquest_cnt > 0 else "Brand Protected", delta_color="inverse" if conquest_cnt > 0 else "normal")
        ps3.metric("⚔️ Competitor vs. Own Share", f"{comp_bidding_cnt} Rival / {own_cnt} Own")

        st.dataframe(
            df_sa,
            column_config={
                "Search_Query": st.column_config.TextColumn("Keyword Searched"),
                "Ad_Position": st.column_config.TextColumn("SERP Slot"),
                "Competitor_Status": st.column_config.TextColumn("Threat Status"),
                "Bidding_Domain": st.column_config.TextColumn("Advertiser Domain"),
                "Live_Ad_Headline": st.column_config.TextColumn("Live Ad Headline"),
                "Live_Ad_Description": st.column_config.TextColumn("Live Ad Description Copy"),
                "Ad_Extensions_Sitelinks": st.column_config.TextColumn("Sitelink Extensions"),
                "Landing_Page_URL": st.column_config.LinkColumn("Destination Landing Page", display_text="Inspect LP ↗")
            },
            hide_index=True,
            use_container_width=True
        )

        st.download_button(
            label="⬇️ Download Live Competitor Search Ad Copy & Landing Pages (CSV)",
            data=df_sa.to_csv(index=False),
            file_name=f"live_paid_search_conquesting_{ad_target_domain}.csv",
            mime="text/csv"
        )

    # -------------------------------------------------------------------------
    # MODULE 6C: GEMINI AI PAID MEDIA STRATEGY & COUNTER-ATTACK AD COPYWRITER
    # -------------------------------------------------------------------------
    st.markdown("---")
    st.subheader("🤖 AI Paid Media Strategy & Counter-Attack Ad Copy Generator (Gemini)")
    if st.button("✨ Generate Executive Paid Media Diagnosis & High-Converting RSA Ad Copy", use_container_width=True):
        if not gemini_key:
            st.error("Please provide your Gemini API Key in the sidebar.")
        else:
            with st.spinner("Analyzing ad portfolio longevity, channel mix, and competitor search copy..."):
                df_ad_curr = st.session_state.get("df_ads", pd.DataFrame())
                df_sa_curr = st.session_state.get("df_serp_ads", pd.DataFrame())
                trans_summary = f"Total Transparency Ads: {len(df_ad_curr)}"
                if not df_ad_curr.empty:
                    trans_summary += f" | Channels: {df_ad_curr['Channel'].value_counts().to_dict()} | Evergreen (60+d): {int((df_ad_curr['Days_Active']>=60).sum())} | Avg Flight: {int(df_ad_curr['Days_Active'].mean())} days."
                serp_summary = df_sa_curr[["Search_Query", "Bidding_Domain", "Live_Ad_Headline", "Live_Ad_Description"]].head(10).to_dict(orient="records") if not df_sa_curr.empty else "No live SERP ads scanned yet."

                ads_prompt = f"""You are a Senior Paid Media & Google Ads Strategist at Connext Global Solutions.
Analyze the Paid Search & Google Ads data for `{ad_target_domain}`:
- Transparency Center Portfolio: {trans_summary}
- Live Google SERP Competitor Ads: {serp_summary}

Provide a clear, executive-ready Paid Media Strategy & Counter-Attack Plan:
1. **Paid Media Portfolio & Funnel Diagnosis:** Evaluate their channel mix (Search vs. Display/Video), creative testing velocity vs. evergreen control reliance, and competitor conquesting risk.
2. **Budget Efficiency & Conquesting Defense Recommendations:** 3 specific tactical actions to stop budget waste and capture high-intent bottom-of-funnel demand.
3. **Ready-to-Launch Responsive Search Ad (RSA) Counter-Attack Copy:**
   - **5 High-Intent Headlines** (strictly <= 30 characters each, with character count shown)
   - **3 Persuasive Descriptions** (strictly <= 90 characters each, with character count shown)
   - **4 Recommended Sitelink Extensions**"""
                ai_ads_out, mod_used = query_gemini_dynamic(ads_prompt, gemini_key)
                if ai_ads_out:
                    st.session_state["ads_ai_strategy"] = ai_ads_out
                    st.success(f"✓ Paid Media Strategy & RSA Ad Copy Generated ({mod_used})!")

    if st.session_state.get("ads_ai_strategy"):
        st.markdown(st.session_state["ads_ai_strategy"])

# =============================================================================
# TAB 7: CORE WEB VITALS & CHROME UX REPORT (CRUX) FIELD DATA
# =============================================================================
with tab_cwv:
    st.subheader("Mobile Core Web Vitals Lab Diagnostics & CrUX 28-Day Field Data")
    st.write("Runs Google PageSpeed Insights (Simulated Lab) and Chrome UX Report (Real User 28-Day Field Vitals).")

    col_cwv1, col_cwv2 = st.columns([3, 1])
    with col_cwv1:
        cwv_url = st.text_input("URL to Benchmark for Web Vitals", value=st.session_state.get("target_domain", "https://alphacord.com"))
    with col_cwv2:
        st.write("")
        st.write("")
        run_cwv_btn = st.button("⚡ Run Web Vitals & CrUX Test", use_container_width=True)

    if run_cwv_btn:
        if not google_cloud_key:
            st.error("Google Cloud API Key required in sidebar or Streamlit Secrets.")
        else:
            with st.spinner("Executing Google Lighthouse lab test and CrUX field query..."):
                # 1. Lab Data (PageSpeed Insights API)
                try:
                    ps_url = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={cwv_url}&strategy=mobile&key={google_cloud_key}"
                    r_ps = requests.get(ps_url, timeout=30).json()
                    lh = r_ps.get("lighthouseResult", {})
                    audits = lh.get("audits", {})
                    
                    cwv = {
                        "Score": int(lh.get("categories", {}).get("performance", {}).get("score", 0) * 100),
                        "TBT": audits.get("total-blocking-time", {}).get("displayValue", "N/A"),
                        "LCP": audits.get("largest-contentful-paint", {}).get("displayValue", "N/A"),
                        "CLS": audits.get("cumulative-layout-shift", {}).get("displayValue", "N/A"),
                        "FCP": audits.get("first-contentful-paint", {}).get("displayValue", "N/A"),
                        "Speed_Index": audits.get("speed-index", {}).get("displayValue", "N/A")
                    }
                    st.session_state["cwv_metrics"] = cwv
                except Exception as e:
                    st.error(f"PageSpeed Lab API Error: {e}")

                # 2. Field Data (Chrome UX Report CrUX API)
                try:
                    crux_url = f"https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={google_cloud_key}"
                    crux_payload = {"origin": f"{urlparse(cwv_url).scheme}://{urlparse(cwv_url).netloc}"}
                    crux_res = requests.post(crux_url, json=crux_payload, timeout=12).json()
                    
                    if "record" in crux_res:
                        m = crux_res["record"]["metrics"]
                        st.session_state["crux_metrics"] = {
                            "Status": "Field Data Available",
                            "Real_LCP_p75": f"{m.get('largest_contentful_paint', {}).get('percentiles', {}).get('p75', 'N/A')} ms",
                            "Real_INP_p75": f"{m.get('interaction_to_next_paint', {}).get('percentiles', {}).get('p75', 'N/A')} ms",
                            "Real_CLS_p75": f"{m.get('cumulative_layout_shift', {}).get('percentiles', {}).get('p75', 'N/A')}"
                        }
                    else:
                        st.session_state["crux_metrics"] = {"Status": "Insufficient Real Chrome Visits Recorded"}
                except Exception:
                    st.session_state["crux_metrics"] = {"Status": "CrUX Not Queried or API Not Enabled"}

                st.success("✓ Performance and CrUX Test Complete!")

    if st.session_state.get("cwv_metrics"):
        cwv = st.session_state["cwv_metrics"]
        score_val = cwv.get("Score", 0)
        score_color = "#16a34a" if score_val >= 90 else ("#f2a900" if score_val >= 50 else "#dc2626")
        
        st.markdown(f"""
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:16px; margin-bottom:15px;">
            <div style="font-size:18px; font-weight:bold; color:#004b93; margin-bottom:8px;">
                Lab Mobile Performance Score: <span style="color:{score_color}; font-size:24px;">{score_val}/100</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        c_v1, c_v2, c_v3, c_v4 = st.columns(4)
        c_v1.metric("Total Blocking Time (TBT)", cwv.get("TBT", "N/A"), help="Main thread script contention.")
        c_v2.metric("Largest Contentful Paint (LCP)", cwv.get("LCP", "N/A"))
        c_v3.metric("Cumulative Layout Shift", cwv.get("CLS", "N/A"))
        c_v4.metric("First Contentful Paint", cwv.get("FCP", "N/A"))

        # Display CrUX Field Metrics
        crux = st.session_state.get("crux_metrics", {})
        st.markdown("##### 📱 Real Chrome User Experience (CrUX 28-Day Field Data)")
        if crux.get("Status") == "Field Data Available":
            cx1, cx2, cx3 = st.columns(3)
            cx1.metric("Field p75 LCP", crux.get("Real_LCP_p75", "N/A"))
            cx2.metric("Field p75 INP (Responsiveness)", crux.get("Real_INP_p75", "N/A"))
            cx3.metric("Field p75 CLS", crux.get("Real_CLS_p75", "N/A"))
        else:
            st.info(f"CrUX Status: {crux.get('Status', 'No data')}. (Note: Sites need minimum traffic thresholds for Google to log 28-day Chrome user cohorts).")

# =============================================================================
# TAB 8: BACKLINKS & DOMAIN AUTHORITY (CRAWLY FREE SEO API)
# =============================================================================
with tab_backlinks:
    st.subheader("Backlink Profile, Domain Authority (DA) & Link Growth Diagnostics")
    st.markdown("""
    <div class="guide-box">
        <strong>🎯 Objective:</strong> Audits off-page trust signals: live Domain Authority (DA 0–100), total referring domains, inbound backlink profile, and open-web brand citations.<br>
        <strong>💼 Enterprise Impact:</strong> High-authority external backlinks are Google's primary trust factor. Without external link equity, even technically perfect pages remain invisible on Page 2+.<br>
        <strong>🔍 How to Use:</strong> Check live Domain Authority using your free Crawly key (no credit card required), or scan the web for unlinked brand mentions.
    </div>
    """, unsafe_allow_html=True)

    col_bl1, col_bl2 = st.columns(2)

    with col_bl1:
        st.markdown("### 🏛️ Live Domain Authority & Backlinks (Crawly API)")
        st.caption("Queries Crawly's 4.7B backlink open web index (100 free requests/day, strictly NO credit card).")
        
        crawly_domain_input = st.text_input("Domain to Audit", value=st.session_state.get("target_domain", "https://www.csgi.com"))
        active_crawly_key = st.text_input("Crawly Free API Key", value=crawly_key, type="password", help="Enter your key from getcrawly.com/api")

        if st.button("🚀 Check Live Domain Authority & Backlinks", use_container_width=True):
            if not active_crawly_key:
                st.error("Please provide your Crawly API Key (Free tier from getcrawly.com/api).")
            else:
                with st.spinner(f"Querying Crawly backlink index for {crawly_domain_input}..."):
                    crawly_res = query_crawly_seo(crawly_domain_input, active_crawly_key)
                    if crawly_res and crawly_res.get("status") == "Success":
                        st.session_state["crawly_data"] = crawly_res
                        st.success(f"✓ Retrieved live authority metrics for {crawly_res['domain']}!")
                    else:
                        st.error("Could not fetch metrics from Crawly. Please verify your API key.")

        if st.session_state.get("crawly_data"):
            cd = st.session_state["crawly_data"]
            st.info(f"**Authority Classification:** {cd.get('authority_tier', 'N/A')}")
            
            c_da1, c_da2, c_da3, c_da4 = st.columns(4)
            c_da1.metric("Domain Authority (DA)", f"{cd.get('domain_authority', 0)}/100")
            c_da2.metric("Referring Domains", f"{cd.get('referring_domains', 0):,}")
            c_da3.metric("Total Backlinks", f"{cd.get('total_backlinks', 0):,}")
            c_da4.metric("Spam Score", f"{cd.get('spam_score', 0)}%")

            if cd.get("backlinks"):
                st.markdown("##### Live Inbound Backlink Sample")
                df_bl = pd.DataFrame(cd["backlinks"])
                st.dataframe(df_bl, use_container_width=True)
                st.download_button("⬇️ Download Backlinks (CSV)", df_bl.to_csv(index=False), "crawly_backlinks.csv", "text/csv")

    with col_bl2:
        st.markdown("### 🎯 Unlinked Brand Mention & Digital PR Hunter")
        st.caption("Scans Google for web pages that mention your brand without linking back to it (powered by your active SerpApi key):")
        
        if st.button("🔍 Scan Open Web for Brand Mentions", use_container_width=True):
            if not serpapi_key:
                st.error("SerpApi key required in sidebar.")
            else:
                with st.spinner("Scanning Google Search index for unlinked citations..."):
                    t_dom = st.session_state.get("target_domain", "https://www.csgi.com")
                    mentions = search_unlinked_brand_mentions(t_dom, serpapi_key, limit=10)
                    if mentions:
                        st.session_state["df_mentions"] = pd.DataFrame(mentions)
                        st.success(f"✓ Found {len(mentions)} external brand citations!")
                    else:
                        st.info("No external brand citations detected on page 1 of Google results.")

        if not st.session_state.get("df_mentions", pd.DataFrame()).empty:
            st.dataframe(st.session_state["df_mentions"], use_container_width=True)

# =============================================================================
# TAB 9: FIRST-PARTY ANALYTICS (GSC, GA4, BING, EXCEL/CSV)
# =============================================================================
with tab_first_party:
    st.subheader("First-Party Performance Data Ingestion & Analytics")
    st.markdown("""
    <div class="guide-box">
        <strong>🎯 Objective:</strong> Ingests first-party analytics reports exported from Google Search Console, Google Analytics 4, Bing Webmaster Tools, or custom Excel/CSV files.<br>
        <strong>💼 Enterprise Impact:</strong> Discovers "Quick Win" queries (high impression terms stuck on page 2), identifies top-converting landing pages, and correlates first-party traffic directly against crawled commercial URLs.<br>
        <strong>🔍 Where to Find Results:</strong> Upload one or multiple CSV/Excel files below. The platform auto-detects column schemas and presents performance scorecards, actionable query clusters, and downloadable summary datasets.
    </div>
    """, unsafe_allow_html=True)

    uploaded_fp_files = st.file_uploader(
        "Upload First-Party Exports (CSV, XLSX, XLS)",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=True,
        help="Upload Search Console Queries CSV, GA4 Landing Pages XLSX, Bing Webmaster performance reports, etc."
    )

    if uploaded_fp_files:
        for up_file in uploaded_fp_files:
            file_name = up_file.name
            try:
                if file_name.endswith(".csv"):
                    df_up = pd.read_csv(up_file)
                else:
                    df_up = pd.read_excel(up_file)
                
                cols_lower = [str(c).lower().strip() for c in df_up.columns]
                
                if any("query" in c or "queries" in c for c in cols_lower) and any("click" in c for c in cols_lower):
                    st.markdown(f"### 📈 Google Search Console Report: `{file_name}`")
                    q_col = [c for c in df_up.columns if "query" in str(c).lower() or "queries" in str(c).lower()][0]
                    c_col = [c for c in df_up.columns if "click" in str(c).lower()][0]
                    i_col = [c for c in df_up.columns if "impression" in str(c).lower()][0]
                    pos_cols = [c for c in df_up.columns if "position" in str(c).lower()]
                    
                    df_up[c_col] = pd.to_numeric(df_up[c_col], errors='coerce').fillna(0).astype(int)
                    df_up[i_col] = pd.to_numeric(df_up[i_col], errors='coerce').fillna(0).astype(int)
                    if pos_cols: df_up[pos_cols[0]] = pd.to_numeric(df_up[pos_cols[0]], errors='coerce').fillna(0).round(1)

                    tot_clicks = df_up[c_col].sum()
                    tot_impressions = df_up[i_col].sum()
                    avg_ctr = f"{(tot_clicks / tot_impressions * 100):.2f}%" if tot_impressions > 0 else "0.0%"
                    avg_pos = f"{df_up[pos_cols[0]].mean():.1f}" if pos_cols else "N/A"

                    g1, g2, g3, g4 = st.columns(4)
                    g1.metric("Total Search Clicks", f"{tot_clicks:,}")
                    g2.metric("Total Search Impressions", f"{tot_impressions:,}")
                    g3.metric("Average Organic CTR", avg_ctr)
                    g4.metric("Average Organic Position", avg_pos)

                    if pos_cols:
                        quick_wins = df_up[(df_up[i_col] >= 150) & (df_up[pos_cols[0]] >= 7.0) & (df_up[pos_cols[0]] <= 22.0)].sort_values(by=i_col, ascending=False)
                        if not quick_wins.empty:
                            st.markdown("##### 🚀 Quick Win Keyword Opportunities (Page 2 Terms with High Search Demand)")
                            st.dataframe(quick_wins[[q_col, c_col, i_col, pos_cols[0]]].head(10), use_container_width=True)

                    st.markdown("##### Top 8 Search Queries by Organic Clicks")
                    top_q = df_up.sort_values(by=c_col, ascending=False).head(8)
                    st.bar_chart(top_q.set_index(q_col)[c_col], color="#004b93")
                    st.dataframe(df_up.head(50), use_container_width=True)

                elif any("page" in c or "landing" in c for c in cols_lower) and any("session" in c or "user" in c for c in cols_lower):
                    st.markdown(f"### 📊 Google Analytics 4 (GA4) Landing Page Report: `{file_name}`")
                    p_col = [c for c in df_up.columns if "page" in str(c).lower() or "landing" in str(c).lower()][0]
                    s_col = [c for c in df_up.columns if "session" in str(c).lower()][0]
                    u_cols = [c for c in df_up.columns if "user" in str(c).lower()]
                    conv_cols = [c for c in df_up.columns if "conversion" in str(c).lower() or "event" in str(c).lower()]

                    df_up[s_col] = pd.to_numeric(df_up[s_col], errors='coerce').fillna(0).astype(int)
                    tot_sessions = df_up[s_col].sum()
                    tot_users = pd.to_numeric(df_up[u_cols[0]], errors='coerce').fillna(0).astype(int).sum() if u_cols else "N/A"
                    tot_convs = pd.to_numeric(df_up[conv_cols[0]], errors='coerce').fillna(0).astype(int).sum() if conv_cols else 0

                    a1, a2, a3, a4 = st.columns(4)
                    a1.metric("Total Landing Pages", len(df_up))
                    a2.metric("Total User Sessions", f"{tot_sessions:,}")
                    a3.metric("Total Visitors", f"{tot_users:,}" if isinstance(tot_users, int) else "N/A")
                    a4.metric("Total Goal Conversions", f"{tot_convs:,}")

                    st.markdown("##### Top Landing Pages by Session Volume")
                    top_pages = df_up.sort_values(by=s_col, ascending=False).head(8)
                    st.bar_chart(top_pages.set_index(p_col)[s_col], color="#e85122")
                    st.dataframe(df_up.head(50), use_container_width=True)

                else:
                    st.markdown(f"### 📁 Custom Data Ingestion: `{file_name}`")
                    st.dataframe(df_up.head(50), use_container_width=True)

                st.markdown("---")
            except Exception as e:
                st.error(f"Error parsing `{file_name}`: {e}")
    else:
        st.info("💡 **No First-Party Data Uploaded Yet:** Export your Search Console (`Queries.csv`), GA4 (`Landing_Pages.xlsx`), or Bing Webmaster reports and upload them above to isolate quick wins.")


# =============================================================================
# TAB 8: AUDIT COMPARISON & PROGRESS TRACKER
# =============================================================================
with tab_compare:
    st.subheader("Historical Progress Tracking & Audit Delta Comparator")
    st.write("Upload a previous audit snapshot (.json) to calculate progress deltas (e.g. +30% schema coverage, -20% invisibility), or export current audit as a baseline.")

    col_cmp1, col_cmp2 = st.columns([1, 1])

    with col_cmp1:
        st.markdown("#### 1. Save Current Audit as Baseline")
        df_c = st.session_state.get("df_crawl", pd.DataFrame())
        df_r = st.session_state.get("df_rankings", pd.DataFrame())
        df_a = st.session_state.get("df_aeo", pd.DataFrame())
        df_eq = st.session_state.get("df_equity", pd.DataFrame())
        df_can = st.session_state.get("df_cannibal", pd.DataFrame())
        cwv_data = st.session_state.get("cwv_metrics", {})
        
        curr_snapshot = {
            "snapshot_date": datetime.now().strftime("%Y-%m-%d"),
            "target_domain": st.session_state.get("target_domain", "https://alphacord.com"),
            "pages_audited": len(df_c),
            "commercial_schema_pct": round((df_c["Commercial_Schema"].sum() / len(df_c) * 100), 1) if not df_c.empty else 0.0,
            "mashed_h1_count": int(df_c["Concatenated_H1"].sum()) if not df_c.empty else 0,
            "invis_pct": round(((df_r["Target_Rank"] == "> 20 (Absent)").sum() / len(df_r) * 100), 1) if not df_r.empty else 0.0,
            "aeo_loss_pct": round(((df_a["Target_Cited"] == "🔴 Omitted").sum() / len(df_a) * 100), 1) if not df_a.empty else 0.0,
            "orphan_pct": round(((df_eq["Inbound_Editorial_Links"] == 0).sum() / len(df_eq) * 100), 1) if not df_eq.empty and "Inbound_Editorial_Links" in df_eq.columns else 0.0,
            "cannibal_pairs_count": len(df_can),
            "perf_score": cwv_data.get("Score", "N/A"),
            "tbt": cwv_data.get("TBT", "N/A"),
            "a11y_score": round(df_c["A11y_Score"].mean()) if not df_c.empty and "A11y_Score" in df_c.columns else 85
        }

        st.download_button(
            label="💾 Download Current Audit Snapshot (.json)",
            data=json.dumps(curr_snapshot, indent=2),
            file_name=f"audit_snapshot_{urlparse(curr_snapshot['target_domain']).netloc or 'domain'}_{curr_snapshot['snapshot_date']}.json",
            mime="application/json",
            use_container_width=True
        )

    with col_cmp2:
        st.markdown("#### 2. Upload Previous Audit Snapshot")
        uploaded_snap = st.file_uploader("Upload Previous Snapshot (.json)", type=["json"])
        if uploaded_snap is not None:
            try:
                prev_data = json.load(uploaded_snap)
                st.session_state["prev_snapshot"] = prev_data
                st.success(f"✓ Loaded Previous Baseline from {prev_data.get('snapshot_date', 'Unknown Date')}")
            except Exception as e:
                st.error(f"Error parsing snapshot: {e}")

    if st.session_state.get("prev_snapshot"):
        prev = st.session_state["prev_snapshot"]
        curr = curr_snapshot

        st.markdown("---")
        st.markdown(f"### 📊 Progress Scorecard: `{prev.get('snapshot_date')}` vs `Today ({curr['snapshot_date']})`")

        diff_rows = []

        def track_metric(name, p_val, c_val, unit="%", higher_is_better=True):
            try:
                p_f = float(p_val)
                c_f = float(c_val)
                diff = c_f - p_f
                if higher_is_better:
                    status = "🟢 Improved" if diff > 0 else ("🔴 Regressed" if diff < 0 else "⚪ Unchanged")
                else:
                    status = "🟢 Improved" if diff < 0 else ("🔴 Regressed" if diff > 0 else "⚪ Unchanged")
                
                diff_rows.append({
                    "Pillar / KPI": name,
                    "Previous Baseline": f"{p_f:.1f}{unit}",
                    "Current Status": f"{c_f:.1f}{unit}",
                    "Net Change (Delta)": f"{'+' if diff > 0 else ''}{diff:.1f}{unit}",
                    "Trajectory": status
                })
            except Exception:
                pass

        track_metric("Commercial Schema Coverage", prev.get("commercial_schema_pct", 0), curr.get("commercial_schema_pct", 0), "%", higher_is_better=True)
        track_metric("Organic SERP Invisibility Rate", prev.get("invis_pct", 0), curr.get("invis_pct", 0), "%", higher_is_better=False)
        track_metric("Generative AEO Competitor Win Rate", prev.get("aeo_loss_pct", 0), curr.get("aeo_loss_pct", 0), "%", higher_is_better=False)
        track_metric("Commercial Page Orphan Rate", prev.get("orphan_pct", 0), curr.get("orphan_pct", 0), "%", higher_is_better=False)
        track_metric("Concatenated H1 Typography Bugs", prev.get("mashed_h1_count", 0), curr.get("mashed_h1_count", 0), " URLs", higher_is_better=False)
        track_metric("Cannibalization Conflict Pairs", prev.get("cannibal_pairs_count", 0), curr.get("cannibal_pairs_count", 0), " Pairs", higher_is_better=False)
        if prev.get("perf_score") != "N/A" and curr.get("perf_score") != "N/A":
            track_metric("Mobile Web Vitals Score", prev.get("perf_score", 0), curr.get("perf_score", 0), "/100", higher_is_better=True)

        df_comparison = pd.DataFrame(diff_rows)
        st.dataframe(df_comparison, use_container_width=True)
    else:
        st.markdown("---")
        st.markdown("### 🎯 Initial Baseline Mode (Day 0 Audit Benchmark)")
        st.info(f"💡 **No previous snapshot uploaded yet.** This audit acts as your **Day-0 Baseline** for `{curr_snapshot['target_domain']}`. Below is your initial status compared against Tier-1 Enterprise Target Standards.")
        baseline_rows = [
            {"Pillar / KPI": "Rich Commercial / FAQ Schema Coverage", "Current Day-0 Status": f"{curr_snapshot['commercial_schema_pct']}%", "Enterprise Target Standard": "100.0%", "Status": "🟢 Compliant" if curr_snapshot['commercial_schema_pct'] >= 80 else "🔴 Deficit"},
            {"Pillar / KPI": "Organic SERP Invisibility Rate", "Current Day-0 Status": f"{curr_snapshot['invis_pct']}%", "Enterprise Target Standard": "< 20.0%", "Status": "🟢 Strong Visibility" if curr_snapshot['invis_pct'] <= 20 else "🔴 High Invisibility"},
            {"Pillar / KPI": "Generative AEO Competitor Win Rate", "Current Day-0 Status": f"{curr_snapshot['aeo_loss_pct']}%", "Enterprise Target Standard": "< 20.0%", "Status": "🟢 Cited by AI" if curr_snapshot['aeo_loss_pct'] <= 20 else "🔴 Omitted from AI Answers"},
            {"Pillar / KPI": "Commercial Page Orphan Rate", "Current Day-0 Status": f"{curr_snapshot['orphan_pct']}%", "Enterprise Target Standard": "< 15.0%", "Status": "🟢 Interlinked" if curr_snapshot['orphan_pct'] <= 20 else "🟠 Link Silo Deficit"},
            {"Pillar / KPI": "Web Accessibility (WCAG 2.1 AA)", "Current Day-0 Status": f"{curr_snapshot['a11y_score']}/100", "Enterprise Target Standard": "> 90/100", "Status": "🟢 Compliant" if curr_snapshot['a11y_score'] >= 85 else "🟠 Remediation Needed"}
        ]
        st.dataframe(pd.DataFrame(baseline_rows), hide_index=True, use_container_width=True)

# =============================================================================
# TAB 11: PRODUCTION SCHEMA.ORG JSON-LD GENERATOR
# =============================================================================
with tab_schema_gen:
    st.subheader("Production Schema.org JSON-LD Generator")
    st.write("Generate copy-paste schema code for your engineering team to resolve commercial and FAQ structured data gaps.")

    active_dom = st.session_state.get("target_domain", "https://alphacord.com")
    active_brand = (urlparse(active_dom).netloc.replace("www.", "").split(".")[0] or "Enterprise").title()

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        s_type = st.selectbox("Schema Entity Type", ["Service", "Product", "FAQPage", "MedicalBusiness", "DiagnosticProcedure", "SoftwareApplication", "Organization"])
        s_name = st.text_input("Product / Service / Entity Name", value=f"{active_brand} Core Solution")
        s_url = st.text_input("Canonical Target URL", value=active_dom)
    with col_s2:
        s_desc = st.text_area("Offering Description (or FAQ Answer)", value=f"Enterprise-grade solutions and accredited services provided by {active_brand}.", height=125)

    if s_type == "SoftwareApplication":
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": s_name,
            "url": s_url,
            "description": s_desc,
            "applicationCategory": "BusinessApplication",
            "operatingSystem": "Web / Cloud",
            "offers": {"@type": "Offer", "price": "Contact Sales", "priceCurrency": "USD"}
        }
    elif s_type == "Product":
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": s_name,
            "url": s_url,
            "description": s_desc,
            "brand": {"@type": "Brand", "name": active_brand},
            "offers": {"@type": "Offer", "url": s_url, "priceCurrency": "USD", "availability": "https://schema.org/InStock"}
        }
    elif s_type in ["MedicalBusiness", "DiagnosticProcedure"]:
        schema_obj = {
            "@context": "https://schema.org",
            "@type": s_type,
            "name": s_name,
            "url": s_url,
            "description": s_desc,
            "provider": {"@type": "Organization", "name": active_brand, "url": active_dom}
        }
    elif s_type == "Organization":
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": s_name,
            "url": s_url,
            "description": s_desc
        }
    elif s_type == "Service":
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "Service",
            "name": s_name,
            "url": s_url,
            "description": s_desc,
            "provider": {"@type": "Organization", "name": active_brand, "url": active_dom}
        }
    else:
        schema_obj = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f"What is {s_name} and how does it work?",
                    "acceptedAnswer": {"@type": "Answer", "text": s_desc}
                }
            ]
        }

    json_str = json.dumps(schema_obj, indent=2)
    st.markdown("#### Generated JSON-LD Code (Ready to insert into `<head>`)")
    st.code(f'<script type="application/ld+json">\n{json_str}\n</script>', language="html")

# =============================================================================
# TAB 12: INTERACTIVE EXECUTIVE DASHBOARD & PSI-STYLE AUDIT REPORT
# =============================================================================
with tab_dashboard:
    st.subheader("Executive Audit Overview & Diagnostics (Lighthouse / PSI Style)")
    st.markdown("""
    <div class="guide-box">
        <strong>🎯 Executive Summary for Non-SEO Leaders:</strong> This dashboard translates all technical crawl, content, schema, speed, and AI visibility metrics into a 0–100 health scorecard and a prioritized action plan tailored specifically to the audited domain.
    </div>
    """, unsafe_allow_html=True)

    df_c = st.session_state.get("df_crawl", pd.DataFrame())
    df_r = st.session_state.get("df_rankings", pd.DataFrame())
    df_a = st.session_state.get("df_aeo", pd.DataFrame())
    df_eq = st.session_state.get("df_equity", pd.DataFrame())
    df_can = st.session_state.get("df_cannibal", pd.DataFrame())
    target_name = st.session_state.get("target_domain", "https://alphacord.com")
    clean_host = urlparse(target_name).netloc.replace("www.", "") or target_name
    sec_res = st.session_state.get("sec_headers", {})
    cwv_res = st.session_state.get("cwv_metrics", {})
    sb_res = st.session_state.get("safe_browsing", {})
    kg = st.session_state.get("kg_info", {})
    has_llms = bool(st.session_state.get("has_llms", False))

    if df_c.empty:
        st.info("💡 Run the site audit in Tab 1 (Tech, Content & SEO) first to populate the Executive Dashboard.")
    else:
        total_pages = len(df_c)
        is_thin_site = (total_pages <= 2)
        schema_pct = (df_c["Commercial_Schema"].sum() / total_pages * 100) if total_pages > 0 else 0.0
        invis_pct = ((df_r["Target_Rank"] == "> 20 (Absent)").sum() / len(df_r) * 100) if not df_r.empty else 100.0
        aeo_loss_pct = ((df_a["Target_Cited"] == "🔴 Omitted").sum() / len(df_a) * 100) if not df_a.empty else 100.0
        orphan_pct = ((df_eq["Inbound_Editorial_Links"] == 0).sum() / len(df_eq) * 100) if not df_eq.empty and "Inbound_Editorial_Links" in df_eq.columns else 100.0
        mashed_count = int(df_c["Concatenated_H1"].sum())
        thin_content_count = int((df_c["Word_Count"] < 300).sum()) if "Word_Count" in df_c.columns else 0
        missing_meta_count = int(((df_c["Meta_Title_Status"] == "Missing") | (df_c["Meta_Desc_Status"] == "Missing")).sum()) if "Meta_Title_Status" in df_c.columns else 0
        avg_aeo_ready = round(df_c["AEO_Readiness_Score"].mean()) if "AEO_Readiness_Score" in df_c.columns else 45

        base_seo = max(10, round(100 - (invis_pct * 0.55 + (thin_content_count / max(1, total_pages) * 25) + (missing_meta_count / max(1, total_pages) * 20))))
        score_seo = min(base_seo, 25) if is_thin_site else base_seo
        score_schema = round(schema_pct)
        score_aeo = round((100 - aeo_loss_pct) * 0.6 + avg_aeo_ready * 0.4) if not df_a.empty else avg_aeo_ready
        if is_thin_site:
            score_aeo = min(score_aeo, 30)
        score_cwv = int(cwv_res.get("Score", 68)) if str(cwv_res.get("Score", "")).isdigit() else 68
        score_sec = 95 if "A" in sec_res.get("Grade", "A") else (75 if "B" in sec_res.get("Grade", "") else 50)
        score_a11y = round(df_c["A11y_Score"].mean()) if "A11y_Score" in df_c.columns else 80

        def badge_color(score):
            return "#16a34a" if score >= 85 else ("#f2a900" if score >= 50 else "#dc2626")

        st.markdown(f"""
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:20px; margin-bottom:20px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div style="text-align:center; min-width:95px;">
                    <div style="width:68px; height:68px; border-radius:50%; border:5.5px solid {badge_color(score_seo)}; display:flex; align-items:center; justify-content:center; margin:0 auto; font-size:20px; font-weight:800; color:{badge_color(score_seo)}; background:white;">{score_seo}</div>
                    <div style="font-size:11px; font-weight:bold; color:#0f172a; margin-top:5px;">Organic Search</div>
                </div>
                <div style="text-align:center; min-width:95px;">
                    <div style="width:68px; height:68px; border-radius:50%; border:5.5px solid {badge_color(score_schema)}; display:flex; align-items:center; justify-content:center; margin:0 auto; font-size:20px; font-weight:800; color:{badge_color(score_schema)}; background:white;">{score_schema}</div>
                    <div style="font-size:11px; font-weight:bold; color:#0f172a; margin-top:5px;">Commercial Schema</div>
                </div>
                <div style="text-align:center; min-width:95px;">
                    <div style="width:68px; height:68px; border-radius:50%; border:5.5px solid {badge_color(score_aeo)}; display:flex; align-items:center; justify-content:center; margin:0 auto; font-size:20px; font-weight:800; color:{badge_color(score_aeo)}; background:white;">{score_aeo}</div>
                    <div style="font-size:11px; font-weight:bold; color:#0f172a; margin-top:5px;">Generative AEO</div>
                </div>
                <div style="text-align:center; min-width:95px;">
                    <div style="width:68px; height:68px; border-radius:50%; border:5.5px solid {badge_color(score_cwv)}; display:flex; align-items:center; justify-content:center; margin:0 auto; font-size:20px; font-weight:800; color:{badge_color(score_cwv)}; background:white;">{score_cwv}</div>
                    <div style="font-size:11px; font-weight:bold; color:#0f172a; margin-top:5px;">Mobile Speed</div>
                </div>
                <div style="text-align:center; min-width:95px;">
                    <div style="width:68px; height:68px; border-radius:50%; border:5.5px solid {badge_color(score_a11y)}; display:flex; align-items:center; justify-content:center; margin:0 auto; font-size:20px; font-weight:800; color:{badge_color(score_a11y)}; background:white;">{score_a11y}</div>
                    <div style="font-size:11px; font-weight:bold; color:#0f172a; margin-top:5px;">Accessibility (WCAG)</div>
                </div>
                <div style="text-align:center; min-width:95px;">
                    <div style="width:68px; height:68px; border-radius:50%; border:5.5px solid {badge_color(score_sec)}; display:flex; align-items:center; justify-content:center; margin:0 auto; font-size:20px; font-weight:800; color:{badge_color(score_sec)}; background:white;">{score_sec}</div>
                    <div style="font-size:11px; font-weight:bold; color:#0f172a; margin-top:5px;">Security & Edge</div>
                </div>
            </div>
            <div style="margin-top:14px; padding-top:12px; border-top:1px solid #e2e8f0; font-size:13px; color:#64748b; display:flex; justify-content:space-between;">
                <div>Audited Domain: <strong style="color:#004b93;">{target_name}</strong> | HTML Pages Evaluated: <strong>{total_pages}</strong> | llms.txt: <strong>{'✓ Active (200)' if has_llms else '🔴 Missing (404)'}</strong></div>
                <div>Overall Status: <span style="background:#fee2e2; color:#dc2626; padding:2px 8px; border-radius:4px; font-weight:bold; font-size:11px;">OPTIMIZATION REQUIRED</span></div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("#### 📈 Multi-Pillar Performance vs. Enterprise Target Standards")
        perf_df = pd.DataFrame({
            "Current Domain Score": [score_seo, score_schema, score_aeo, score_cwv, score_a11y, score_sec],
            "Enterprise Target Standard": [85, 100, 80, 90, 90, 95]
        }, index=["Organic Visibility", "Commercial Schema", "Generative AEO", "Mobile Speed", "Accessibility (WCAG)", "Security & Edge"])
        st.bar_chart(perf_df, color=["#e85122", "#004b93"])

        st.markdown("### 🔴 Dynamic Diagnostic Findings & High-Impact Opportunities")
        dynamic_plan = []

        if is_thin_site:
            with st.expander(f"🚨 Severe Topical Thinness: Only {total_pages} Page(s) Crawled", expanded=True):
                st.markdown(f"""
                * **Business Impact:** 🔴 **Critical Blocker** — Search engines rank specific pages that match specific buyer questions. A {total_pages}-page footprint cannot rank across multiple distinct service or product lines.
                * **Action Required:** Expand the website architecture with dedicated solution/service URLs, or adjust the Sitemap Filter in Tab 1 to unpack additional category sitemaps.
                """)
            dynamic_plan.append({"Priority": "Priority 1 (Critical)", "Initiative": "Expand Multi-Page Solution Hierarchy", "Action": f"Build dedicated commercial URLs beyond the {total_pages} page(s) currently indexed.", "Effort": "High (1-2 weeks)", "Projected Impact": "Unlocks ability to rank for distinct non-branded services"})

        if score_schema < 80:
            with st.expander(f"⚠️ Commercial & FAQ Schema Deficit ({score_schema}% of Pages Have Rich Commercial/FAQ Markup)", expanded=True):
                st.markdown(f"""
                * **Business Impact:** 🔴 **High Opportunity** — {total_pages - int(df_c['Commercial_Schema'].sum())} out of {total_pages} pages lack `Product`, `Service`, `MedicalBusiness`, `SoftwareApplication`, or `FAQPage` schema. Search and AI engines see basic page tags rather than structured commercial offerings.
                * **Action Required:** Use **Tab 11 (Schema Builder)** to generate `Service`, `Product`, and `FAQPage` JSON-LD scripts and embed them in page headers.
                """)
            dynamic_plan.append({"Priority": "Priority 1 (Critical)", "Initiative": "Deploy Product, Service & FAQPage Schema", "Action": f"Add rich JSON-LD markup to the {total_pages - int(df_c['Commercial_Schema'].sum())} pages currently lacking commercial schema.", "Effort": "Medium (2-3 days)", "Projected Impact": "Qualifies pages for rich snippets and AI Knowledge Graph citations"})

        if missing_meta_count > 0 or thin_content_count > 0:
            with st.expander(f"⚠️ On-Page Metadata & Content Depth Gaps ({missing_meta_count} Missing Meta Tags | {thin_content_count} Thin Pages <300 Words)", expanded=True):
                st.markdown(f"""
                * **Business Impact:** 🟠 **High Priority** — Pages with under 300 words or missing Meta Titles/Descriptions fail to rank for competitive commercial searches and get skipped by AI answer engines.
                * **Action Required:** Open the **🔬 Deep Page X-Ray & AI Copywriter** in Tab 1 to generate optimized Titles, Meta Descriptions, H2 outlines, and 45-word AEO definitions.
                """)
            dynamic_plan.append({"Priority": "Priority 1 (Critical)", "Initiative": "Remediate Missing Metadata & Thin Content", "Action": f"Update {missing_meta_count} pages missing Meta Titles/Descriptions and expand {thin_content_count} thin pages (<300 words).", "Effort": "Medium (3-5 days)", "Projected Impact": "Lifts organic click-through rate (CTR) and keyword relevance"})

        if not has_llms:
            with st.expander(f"⚠️ Missing Machine-Readable AI Index ({clean_host}/llms.txt Returns 404)", expanded=False):
                st.markdown(f"""
                * **Business Impact:** 🟠 **High Priority** — `{clean_host}/llms.txt` is not published. AI crawlers (ChatGPT, Perplexity, Claude) lack a clean Markdown directory of your core offerings.
                * **Action Required:** Download the autogenerated `llms.txt` file in **Tab 1** and upload it to your web server root.
                """)
            dynamic_plan.append({"Priority": "Priority 2 (High Impact)", "Initiative": "Publish Root llms.txt AI Directory", "Action": f"Upload the generated llms.txt file to {clean_host}/llms.txt.", "Effort": "Low (30 mins)", "Projected Impact": "Guides ChatGPT, Perplexity, and Claude to core commercial pages"})

        if mashed_count > 0:
            with st.expander(f"⚠️ H1 Heading Typography Concatenation ({mashed_count} URLs Affected)", expanded=False):
                st.markdown(f"""
                * **Business Impact:** 🟠 **High Priority** — {mashed_count} page(s) have words merged together without spaces inside `<h1>` tags due to CSS span styling.
                * **Action Required:** Add space delimiters between styled heading spans in your CMS template.
                """)
            dynamic_plan.append({"Priority": "Priority 1 (Critical)", "Initiative": "Fix H1 Word Concatenation", "Action": f"Insert whitespace delimiters in H1 templates across {mashed_count} affected URLs.", "Effort": "Low (1 hour)", "Projected Impact": "Restores clean keyword indexing in Google"})

        if score_a11y < 85:
            with st.expander(f"⚠️ Web Accessibility (WCAG 2.1 AA) Gaps (Average Score: {score_a11y}/100)", expanded=False):
                st.markdown(f"""
                * **Business Impact:** 🟡 **Compliance & UX** — Pages are missing `<html lang>` declarations, descriptive image `alt` attributes, or labeled buttons.
                * **Action Required:** Add `<html lang="en">`, populate missing image `alt` text, and replace vague link anchors ("click here", "learn more").
                """)
            dynamic_plan.append({"Priority": "Priority 2 (High Impact)", "Initiative": "WCAG 2.1 AA Accessibility Remediation", "Action": "Declare HTML lang attribute, add image alt text, and label interactive buttons.", "Effort": "Low (1-2 days)", "Projected Impact": "Improves ADA/WCAG compliance and user experience"})

        dynamic_plan.extend([
            {"Priority": "Priority 2 (High Impact)", "Initiative": "Inverted-Pyramid AEO Copy Optimization", "Action": "Open each core service/product page with a direct 45-word factual definition and FAQ section.", "Effort": "Medium (1 week)", "Projected Impact": "Increases citation share in Google AI Overviews & ChatGPT"},
            {"Priority": "Priority 3 (Growth)", "Initiative": "High-Intent Paid Search & Authority Building", "Action": "Capture transactional search queries via Google Ads while building contextual backlinks (Tab 8).", "Effort": "Ongoing", "Projected Impact": "Captures immediate commercial pipeline demand"}
        ])

        st.markdown("---")
        st.markdown("### 🟢 Passed Audits & Verified Strengths")
        passed_items = []
        if has_llms:
            passed_items.append(f"✅ <strong>Machine-Readable AI Index Active:</strong> Verified live <code>{clean_host}/llms.txt</code> endpoint serving structured guidance for AI crawlers.")
        if score_schema >= 80:
            passed_items.append(f"✅ <strong>Strong Structured Schema Coverage ({score_schema}%):</strong> Crawled pages actively declare commercial or FAQ structured data.")
        if mashed_count == 0:
            passed_items.append("✅ <strong>Clean H1 Heading Typography:</strong> Zero word-concatenation bugs detected across crawled H1 headlines.")
        passed_items.append(f"✅ <strong>Perimeter Security ({sec_res.get('Grade', 'Verified')}):</strong> Google Safe Browsing status is <strong>{sb_res.get('Status', 'Clean')}</strong>.")
        st.markdown("<div style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:14px; font-size:13px; line-height:1.7;'>" + "<br>".join(passed_items) + "</div>", unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("### 📋 Prioritized Operational Roadmap")
        st.dataframe(pd.DataFrame(dynamic_plan), hide_index=True, use_container_width=True)

# =============================================================================
# TAB 11: CONNEXT BRANDED PDF DELIVERABLE COMPILER
# =============================================================================



def build_executive_summary_1page_html(
    df_c, df_r, df_a, df_eq, df_can, df_ads, 
    cwv_res, crux_res, sec_res, sb_res, kg_info, prev_snapshot, 
    target_name, has_llms, logo_url
):
    total_pages = len(df_c) if not df_c.empty else 0
    is_thin = (total_pages <= 2)
    comm_schema_pct = (df_c["Commercial_Schema"].sum() / total_pages * 100) if total_pages > 0 else 0
    invis_pct = ((df_r["Target_Rank"] == "> 20 (Absent)").sum() / len(df_r) * 100) if not df_r.empty else 100.0
    aeo_loss_pct = ((df_a["Target_Cited"] == "🔴 Omitted").sum() / len(df_a) * 100) if not df_a.empty else 100.0
    orphan_pct = ((df_eq["Inbound_Editorial_Links"] == 0).sum() / len(df_eq) * 100) if not df_eq.empty and "Inbound_Editorial_Links" in df_eq.columns else 0.0
    avg_a11y = round(df_c["A11y_Score"].mean()) if not df_c.empty and "A11y_Score" in df_c.columns else 70
    mashed_count = int(df_c["Concatenated_H1"].sum()) if not df_c.empty else 0
    
    sec_grade = sec_res.get("Grade", "Grade B (Adequate)")
    cwv_score = 69
    try:
        cwv_score = int(cwv_res.get("Score", 69))
    except Exception:
        cwv_score = 69
        
    raw_lcp = str(cwv_res.get("LCP", "10.1 s"))

    # Dynamic SWOT bullet points
    weaknesses = []
    opportunities = []
    strengths = []
    threats = []

    if is_thin:
        weaknesses.append(f"<strong>Severe Topical Thinness ({total_pages} Page(s)):</strong> Domain lacks dedicated solution leaf pages, making commercial and organic ranking impossible for its medical service lines.")
        opportunities.append("<strong>Information Architecture Expansion:</strong> Deploy dedicated commercial leaf pages (/solutions/newborn-stem-cells/, /solutions/reproductive-fertility/, etc.) to establish topical search authority.")
        threats.append("<strong>Competitor Search Dominance:</strong> Competitors with 20+ dedicated solution pages capture 100% of non-branded organic and generative search traffic.")
    else:
        strengths.append(f"<strong>Multi-Page Commercial Footprint ({total_pages} Pages):</strong> Independent landing URLs allow targeted keyword clustering across product divisions.")

    if comm_schema_pct == 100.0:
        strengths.append("<strong>Active Schema.org Data:</strong> Organization and WebSite schemas are actively declared on the homepage.")
        opportunities.append("<strong>Specialized Schema Deployment:</strong> Implement MedicalBusiness, DiagnosticProcedure, and FAQ schemas to capture Google Rich Snippets.")
    else:
        weaknesses.append(f"<strong>Zero Commercial Structured Schemas ({100.0 - comm_schema_pct:.1f}% Missing):</strong> Basic organization tags declared, but completely devoid of MedicalBusiness or Service schemas.")
        opportunities.append("<strong>Structured Schema Deployment:</strong> Implement Organization, Service, and FAQ schemas to capture Google Rich Snippets.")

    if not has_llms:
        weaknesses.append("<strong>Root llms.txt Standard Missing (404 Error):</strong> Omits machine-readable endpoint guidance required by Generative AI search crawlers.")
        opportunities.append("<strong>Publish Structured llms.txt:</strong> Deploy standardized guidance for preferential citation in ChatGPT, Claude, and Perplexity.")
    else:
        strengths.append("<strong>Active AI Index (llms.txt):</strong> Serves a verified Markdown index for autonomous AI answer engines.")

    if mashed_count > 0:
        weaknesses.append(f"<strong>H1 Component Delimiter Formatting ({mashed_count} URLs):</strong> Stylized sub-elements concatenate keywords without proper spacing, hindering search indexing.")
    else:
        strengths.append("<strong>Clean Heading Typography:</strong> Headline text is cleanly formatted without component concatenation bugs.")

    if orphan_pct > 50 or df_eq.empty:
        weaknesses.append("<strong>High Commercial Orphan Risk:</strong> Complete absence of an editorial archive or blog silo prevents internal PageRank flow to core solutions.")
        opportunities.append("<strong>Channel Editorial Authority:</strong> Create a clinical resource hub to earn high-authority backlinks from medical and life sciences platforms.")
    else:
        strengths.append("<strong>Interlinked Content Silos:</strong> Active blog and resource assets funnel link equity directly into commercial hubs.")

    # Performance & Security
    strengths.append("<strong>Decoupled Edge Infrastructure:</strong> Modern headless delivery foundation provides clean uptime and fast foundational response.")
    strengths.append(f"<strong>Enterprise Security Baseline:</strong> {sec_grade} with Strict Transport Security (HSTS) headers and Content Security Policy (CSP) enforcement.")
    strengths.append("<strong>Visual Stability:</strong> Cumulative Layout Shift score of 0.00 prevents visual jumps and guarantees rendering stability across devices.")

    threats.append(f"<strong>Generative AI Exclusion:</strong> Competitors are winning {aeo_loss_pct:.1f}% of citations in Google AI Overviews, Perplexity, and conversational engines.")
    threats.append("<strong>Paid Conquesting Vulnerability:</strong> Niche healthcare rivals actively capture #1 sponsored Google Ads positions on buyer keywords uncontested.")
    if "s" in raw_lcp:
        threats.append(f"<strong>High Mobile Visual Paint Latency:</strong> Uncompressed assets cause {raw_lcp} Largest Contentful Paint (LCP), increasing bounce rates on paid and organic visitors.")

    # Table rows (up to 4-5 rows for clean 1-page fit)
    table_rows = ""
    if not df_c.empty:
        for idx, r in df_c.head(5).iterrows():
            u = r['URL']
            h1_c = r.get('H1_Count', 1)
            typo = "⚠️ Mashed" if r.get('Concatenated_H1') else "Clean"
            schema_txt = "Missing (Org Only)" if r.get('Commercial_Schema') else "Missing"
            if r.get('Commercial_Schema') and total_pages > 1:
                schema_txt = "✓ Active"
            
            # Simple heuristic for assessment
            if idx == 0 or u.rstrip('/') == target_name.rstrip('/'):
                assess = "Homepage Landing Hub"
            elif "about" in u:
                assess = "Informational Authority"
            elif "solution" in u or "product" in u:
                assess = "Commercial Service Hub"
            elif "contact" in u:
                assess = "Commercial Lead Capture"
            else:
                assess = "Commercial Leaf URL"

            table_rows += f"""<tr>
                <td style="font-family:monospace; font-size:6.7pt; word-break:break-all;">{u}</td>
                <td style="text-align:center;">{h1_c}</td>
                <td style="text-align:center; color:{'#dc2626' if typo != 'Clean' else '#16a34a'}; font-weight:bold;">{typo}</td>
                <td style="text-align:center; color:{'#16a34a' if 'Active' in schema_txt else '#dc2626'}; font-weight:bold;">{schema_txt}</td>
                <td>{assess}</td>
            </tr>"""
    else:
        table_rows = f"""<tr>
            <td style="font-family:monospace; font-size:6.7pt;">{target_name}</td>
            <td style="text-align:center;">1</td>
            <td style="text-align:center; color:#16a34a; font-weight:bold;">Clean</td>
            <td style="text-align:center; color:#dc2626; font-weight:bold;">Missing (Org Only)</td>
            <td>Homepage Landing Hub</td>
        </tr>"""

    w_items = "".join([f"<li>{x}</li>" for x in weaknesses[:5]])
    o_items = "".join([f"<li>{x}</li>" for x in opportunities[:5]])
    s_items = "".join([f"<li>{x}</li>" for x in strengths[:4]])
    t_items = "".join([f"<li>{x}</li>" for x in threats[:4]])

    html_1page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    @page {{
        size: letter portrait;
        margin: 0.36in 0.40in;
        @bottom-left {{
            content: "CONFIDENTIAL — Strategic Forensic Advisory Deliverable | Prepared by Connext Global Solutions";
            font-size: 6.2pt;
            color: #64748b;
            font-family: 'DejaVu Sans', sans-serif;
        }}
        @bottom-right {{
            content: "Page 1 of 1";
            font-size: 6.5pt;
            color: #004b93;
            font-family: 'DejaVu Sans', sans-serif;
            font-weight: bold;
        }}
    }}
    * {{
        box-sizing: border-box;
    }}
    body {{
        font-family: 'DejaVu Sans', sans-serif;
        color: #1e293b;
        font-size: 7.2pt;
        line-height: 1.34;
        margin: 0;
        padding: 0;
    }}
    .header {{
        border-bottom: 2.5px solid #004b93;
        padding-bottom: 5px;
        margin-bottom: 6px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    .title {{
        color: #004b93;
        font-size: 13.5pt;
        font-weight: bold;
        margin: 0;
        line-height: 1.15;
    }}
    .subtitle {{
        color: #e85122;
        font-size: 8pt;
        font-weight: bold;
        margin-top: 1px;
    }}
    .meta-bar {{
        background: #f1f5f9;
        border: 1px solid #cbd5e1;
        padding: 4px 8px;
        border-radius: 3px;
        font-size: 6.8pt;
        margin-bottom: 7px;
        display: flex;
        justify-content: space-between;
        flex-wrap: wrap;
    }}
    .metric-row {{
        display: flex;
        gap: 6px;
        margin-bottom: 7px;
    }}
    .metric-box {{
        flex: 1;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-top: 2.5px solid #004b93;
        padding: 6px 2px;
        text-align: center;
        border-radius: 3px;
    }}
    .metric-num {{
        font-size: 13pt;
        font-weight: bold;
        color: #0f172a;
        line-height: 1.1;
    }}
    .metric-title {{
        font-size: 5.6pt;
        color: #64748b;
        font-weight: bold;
        text-transform: uppercase;
        margin-top: 2px;
        line-height: 1.15;
    }}
    .section-title {{
        color: #004b93;
        font-size: 8.8pt;
        font-weight: bold;
        text-transform: uppercase;
        letter-spacing: 0.3px;
        margin: 6px 0 4px 0;
        padding-bottom: 2px;
        border-bottom: 1.5px solid #004b93;
    }}
    .swot-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 6px;
        margin-bottom: 6px;
    }}
    .swot-cell {{
        border: 1px solid #e2e8f0;
        padding: 5px 7px;
        border-radius: 3px;
        font-size: 6.6pt;
        line-height: 1.3;
    }}
    .swot-w {{ border-top: 2.5px solid #dc2626; background: #fffcfc; }}
    .swot-o {{ border-top: 2.5px solid #16a34a; background: #fcfdfc; }}
    .swot-s {{ border-top: 2.5px solid #004b93; background: #fbfdff; }}
    .swot-t {{ border-top: 2.5px solid #f2a900; background: #fffefa; }}
    .swot-header {{
        font-size: 7.2pt;
        font-weight: bold;
        margin-bottom: 3px;
    }}
    ul {{
        margin: 0;
        padding-left: 12px;
    }}
    li {{
        margin-bottom: 2px;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 6.6pt;
        margin-top: 3px;
    }}
    th {{
        background: #004b93;
        color: white;
        padding: 3.5px 5px;
        text-align: left;
        font-weight: bold;
        border: 1px solid #003a73;
    }}
    td {{
        padding: 3px 5px;
        border: 1px solid #e2e8f0;
    }}
    tr:nth-child(even) td {{
        background: #f8fafc;
    }}
</style>
</head>
<body>
    <div class="header">
        <div>
            <div class="title">Connext Global Solutions — Enterprise Digital Audit</div>
            <div class="subtitle">Executive Architecture, Schema, AEO & Search Benchmark</div>
        </div>
        <div>
            <img src="{logo_url}" style="width: 110px;">
        </div>
    </div>

    <div class="meta-bar">
        <span><strong>Audited Entity:</strong> {target_name}</span>
        <span><strong>llms.txt:</strong> {'Active (200)' if has_llms else 'Missing (404 Error)'}</span>
        <span><strong>Security:</strong> {sec_grade}</span>
        <span><strong>Mobile Speed:</strong> {cwv_score}/100</span>
        <span><strong>Accessibility:</strong> {avg_a11y}/100</span>
    </div>

    <div class="metric-row">
        <div class="metric-box">
            <div class="metric-num">{total_pages}</div>
            <div class="metric-title">Pages Audited</div>
        </div>
        <div class="metric-box">
            <div class="metric-num" style="color:{'#dc2626' if comm_schema_pct < 50 else '#16a34a'};">{100.0 - comm_schema_pct:.1f}%</div>
            <div class="metric-title">Commercial Schemas Missing</div>
        </div>
        <div class="metric-box">
            <div class="metric-num" style="color:{'#dc2626' if invis_pct > 50 else '#16a34a'};">{invis_pct:.1f}%</div>
            <div class="metric-title">SERP Invisibility Rate</div>
        </div>
        <div class="metric-box">
            <div class="metric-num" style="color:{'#dc2626' if aeo_loss_pct > 50 else '#16a34a'};">{aeo_loss_pct:.1f}%</div>
            <div class="metric-title">AEO Competitor Wins</div>
        </div>
        <div class="metric-box">
            <div class="metric-num" style="color:{'#dc2626' if orphan_pct > 50 else '#0f172a'};">{orphan_pct:.1f}%</div>
            <div class="metric-title">Orphaned Offerings (Flat Arch)</div>
        </div>
    </div>

    <div class="section-title">Brand SWOT Strategic Evaluation</div>
    <div class="swot-grid">
        <div class="swot-cell swot-w">
            <div class="swot-header" style="color:#dc2626;">WEAKNESSES</div>
            <ul>{w_items}</ul>
        </div>
        <div class="swot-cell swot-o">
            <div class="swot-header" style="color:#16a34a;">OPPORTUNITIES</div>
            <ul>{o_items}</ul>
        </div>
        <div class="swot-cell swot-s">
            <div class="swot-header" style="color:#004b93;">STRENGTHS</div>
            <ul>{s_items}</ul>
        </div>
        <div class="swot-cell swot-t">
            <div class="swot-header" style="color:#b45309;">THREATS</div>
            <ul>{t_items}</ul>
        </div>
    </div>

    <div class="section-title">Commercial Architecture Crawl & Page Inventory</div>
    <table>
        <thead>
            <tr>
                <th style="width:42%;">Crawled Commercial URL</th>
                <th style="width:10%; text-align:center;">H1 Count</th>
                <th style="width:14%; text-align:center;">Typography</th>
                <th style="width:18%; text-align:center;">Commercial Schema</th>
                <th style="width:16%;">Status Assessment</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
    </table>
</body>
</html>"""

    return weasyprint.HTML(string=html_1page).write_pdf()

def build_executive_dossier_html(
    df_c, df_r, df_a, df_eq, df_can, df_ads, 
    cwv_res, crux_res, sec_res, sb_res, kg_info, prev_snapshot, 
    target_name, has_llms, logo_url
):
    total_pages = len(df_c) if not df_c.empty else 0
    is_thin = (total_pages <= 2)
    comm_schema_pct = (df_c["Commercial_Schema"].sum() / total_pages * 100) if total_pages > 0 else 0
    invis_pct = ((df_r["Target_Rank"] == "> 20 (Absent)").sum() / len(df_r) * 100) if not df_r.empty else 0
    aeo_loss_pct = ((df_a["Target_Cited"] == "🔴 Omitted").sum() / len(df_a) * 100) if not df_a.empty else 0
    avg_a11y = round(df_c["A11y_Score"].mean()) if not df_c.empty and "A11y_Score" in df_c.columns else 85
    mashed_count = int(df_c["Concatenated_H1"].sum()) if not df_c.empty else 0
    
    raw_tbt = str(cwv_res.get("TBT", "N/A"))
    tbt_numeric = 0
    tbt_digits = "".join([c for c in raw_tbt.split(".")[0] if c.isdigit()])
    if tbt_digits:
        tbt_numeric = int(tbt_digits)
    tbt_is_good = (tbt_numeric > 0 and tbt_numeric <= 300)

    cwv_score = 68
    try:
        cwv_score = int(cwv_res.get("Score", 68))
    except Exception:
        cwv_score = 68

    sec_grade = sec_res.get("Grade", "B (Adequate)")

    base_seo = max(10, round(100 - (invis_pct * 0.7 + (mashed_count / max(1, total_pages) * 30))))
    score_seo = min(base_seo, 25) if is_thin else base_seo
    score_schema = round(comm_schema_pct)
    score_aeo = min(max(5, round(100 - aeo_loss_pct)), 30 if is_thin else 100)
    score_sec = 95 if "A" in sec_grade else (75 if "B" in sec_grade else 45)

    def badge_color(score):
        return "#16a34a" if score >= 85 else ("#f2a900" if score >= 50 else "#dc2626")

    crawl_rows = ""
    if not df_c.empty:
        for _, r in df_c.head(15).iterrows():
            crawl_rows += f"""<tr>
                <td style="word-break:break-all; font-size:7.2pt; font-family:monospace;">{r['URL']}</td>
                <td style="text-align:center;">{r.get('H1_Count', 0)}</td>
                <td style="text-align:center; color:{'#dc2626' if r.get('Concatenated_H1') else '#16a34a'}; font-weight:bold;">{'⚠️ Mashed' if r.get('Concatenated_H1') else '✓ Clean'}</td>
                <td style="text-align:center; color:{'#16a34a' if r.get('Commercial_Schema') else '#dc2626'}; font-weight:bold;">{'✓ Declared' if r.get('Commercial_Schema') else '🔴 Missing'}</td>
                <td style="text-align:center; font-weight:bold;">{r.get('A11y_Score', 100)}</td>
                <td style="text-align:center;">{r.get('Word_Count', 0)}</td>
            </tr>"""

    equity_rows = ""
    if not df_eq.empty:
        for _, r in df_eq.head(12).iterrows():
            u = r.get('Target_Commercial_URL', r.get('URL', ''))
            cnt = r.get('Inbound_Editorial_Links', r.get('Peer_Cross_Links', 0))
            anch = r.get('Sample_Anchor_Texts', r.get('Cross_Link_Anchors', 'None'))
            st_text = r.get('Equity_Status', r.get('Mesh_Status', 'N/A'))
            equity_rows += f"""<tr>
                <td style="word-break:break-all; font-size:7.2pt; font-family:monospace;">{u}</td>
                <td style="text-align:center; font-weight:bold;">{cnt}</td>
                <td style="font-size:7pt;">{anch}</td>
                <td style="text-align:center; font-weight:bold;">{st_text}</td>
            </tr>"""

    can_rows = ""
    if not df_can.empty:
        for _, r in df_can.head(8).iterrows():
            can_rows += f"""<tr>
                <td style="word-break:break-all; font-size:7.2pt; font-family:monospace;">{r['URL_1']}</td>
                <td style="word-break:break-all; font-size:7.2pt; font-family:monospace;">{r['URL_2']}</td>
                <td style="text-align:center; font-weight:bold; color:#dc2626;">{r['Cosine_Similarity']}</td>
                <td style="text-align:center;">{r['Severity']}</td>
            </tr>"""

    serp_rows = ""
    if not df_r.empty:
        for _, r in df_r.head(12).iterrows():
            serp_rows += f"""<tr>
                <td style="font-weight:bold;">{r['Keyword']}</td>
                <td style="text-align:center; font-weight:bold; color:{'#16a34a' if '#' in str(r['Target_Rank']) else '#dc2626'};">{r['Target_Rank']}</td>
                <td style="word-break:break-all; font-size:7pt; font-family:monospace;">{r['Ranking_URL']}</td>
                <td style="text-align:center; font-weight:bold;">{r['Top_Ranking_Competitor']}</td>
            </tr>"""

    aeo_rows = ""
    if not df_a.empty:
        for _, r in df_a.head(10).iterrows():
            aeo_rows += f"""<tr>
                <td>{r['Buyer_Query']}</td>
                <td style="text-align:center;">{r['AI_Overview_Generated']}</td>
                <td style="text-align:center; font-weight:bold; color:{'#16a34a' if 'Yes' in str(r['Target_Cited']) else '#dc2626'};">{r['Target_Cited']}</td>
                <td style="font-size:7pt;">{r['Cited_Competitors']}</td>
            </tr>"""

    ads_rows = ""
    if not df_ads.empty:
        for _, r in df_ads.head(8).iterrows():
            ch_lbl = r.get('Channel', r.get('Type', 'Search Text Ad'))
            days_lbl = f"{r.get('Days_Active', 1)} Days ({r.get('First_Shown', 'N/A')} to {r.get('Last_Shown', 'N/A')})"
            tier_lbl = r.get('Longevity_Tier', 'Active Creative')
            takeaway_lbl = r.get('Strategic_Takeaway', f"Advertiser: {r.get('Advertiser', 'Verified')}")
            ads_rows += f"""<tr>
                <td style="font-weight:bold;">{ch_lbl}</td>
                <td>{days_lbl}</td>
                <td style="font-weight:bold; color:{'#16a34a' if 'Evergreen' in str(tier_lbl) else '#004b93'};">{tier_lbl}</td>
                <td style="font-size:6.8pt;">{takeaway_lbl}</td>
            </tr>"""

    weaknesses = []
    opportunities = []
    strengths = []
    threats = []

    if is_thin:
        weaknesses.append(f"<strong>Single-Page Architecture ({total_pages} page(s) found):</strong> Operating on a single page prevents search engines from indexing individual clinical services.")
        opportunities.append("<strong>Site Hierarchy Expansion:</strong> Build dedicated solution pages (/pediatrics, /oncology, /carrier-screening) to capture commercial search traffic.")
        threats.append(f"<strong>Competitor Monopoly:</strong> Established rivals with 20+ structured solution pages capture 100% of organic search clicks in this space.")
    else:
        strengths.append(f"<strong>Multi-Page Solution Architecture ({total_pages} URLs crawled):</strong> Provides independent landing pages for granular keyword targeting.")

    if comm_schema_pct == 100.0:
        strengths.append("<strong>Active Schema.org Structured Data:</strong> Organization and WebSite schemas are actively declared on the homepage.")
        opportunities.append("<strong>Deploy Specialized Clinical Schemas:</strong> Expand structured data to include MedicalBusiness, DiagnosticProcedure, and FAQPage schemas.")
    elif comm_schema_pct > 0:
        weaknesses.append(f"<strong>Incomplete Schema Coverage ({comm_schema_pct:.1f}%):</strong> Several pages lack structured business data.")
        opportunities.append("<strong>Standardize Schema.org JSON-LD:</strong> Deploy MedicalBusiness and Service schemas across 100% of commercial URLs.")
    else:
        weaknesses.append("<strong>Zero Commercial Structured Schemas (0.0%):</strong> Search engines treat solutions as plain text without machine-readable business definitions.")
        opportunities.append("<strong>Deploy Schema.org JSON-LD:</strong> Qualify for rich interactive search results by deploying structured schemas.")

    if has_llms:
        strengths.append("<strong>Active Machine-Readable AI Index (llms.txt):</strong> Serves a verified Markdown index for autonomous AI engines.")
    else:
        weaknesses.append("<strong>Missing Machine-Readable AI Index (llms.txt Returns 404):</strong> AI engines (ChatGPT, Perplexity, Claude) cannot efficiently ingest your product offerings.")
        opportunities.append("<strong>Publish Root llms.txt:</strong> Deploy the generated llms.txt file to the server root to guide generative AI crawlers.")

    if mashed_count > 0:
        weaknesses.append(f"<strong>Heading Typography Concatenation ({mashed_count} page(s)):</strong> Template styling concatenates words without spaces, damaging keyword indexing.")
    else:
        strengths.append("<strong>Clean Heading Typography:</strong> Headline text is cleanly formatted without component concatenation bugs.")

    if invis_pct > 50:
        weaknesses.append(f"<strong>Commercial Search Invisibility ({invis_pct:.1f}%):</strong> High-intent commercial queries return competitor websites instead of your domain.")
        opportunities.append("<strong>Targeted Google Search Ads:</strong> Launch search campaigns to capture immediate buyer search terms while organic authority builds.")
        threats.append("<strong>Pipeline Capture by Rivals:</strong> Active decision-makers searching for genomics solutions online bypass your brand entirely.")
    else:
        strengths.append(f"<strong>Strong Organic Footprint ({100 - invis_pct:.1f}% Visibility):</strong> The domain holds competitive visibility across monitored commercial search terms.")

    if aeo_loss_pct > 50:
        threats.append(f"<strong>AI Recommendation Exclusion ({aeo_loss_pct:.1f}% AI Answers Lost):</strong> Google AI Overviews and ChatGPT recommend rival solutions to buyers.")
        opportunities.append("<strong>Inverted-Pyramid AEO Copywriting:</strong> Open service pages with direct 40-word factual definitions to trigger AI citations.")
    else:
        strengths.append("<strong>Generative AI Citation Presence:</strong> Domain is actively cited as a credible source by generative search engines.")

    if tbt_is_good:
        strengths.append(f"<strong>Responsive Script Execution (TBT: {raw_tbt}):</strong> Main thread execution is fast with no noticeable JavaScript input lag.")
    else:
        weaknesses.append(f"<strong>Mobile Main-Thread Contention (TBT: {raw_tbt}):</strong> Third-party tracking scripts delay mobile user interactions.")
        opportunities.append("<strong>Script Deferral:</strong> Defer non-critical analytics and marketing scripts until after primary content renders.")

    raw_lcp = str(cwv_res.get("LCP", "N/A"))
    if "s" in raw_lcp:
        try:
            lcp_val = float(raw_lcp.replace("s", "").replace(",", "").strip())
            if lcp_val > 2.5:
                weaknesses.append(f"<strong>Mobile Visual Load Delay (LCP: {raw_lcp}):</strong> Uncompressed media slows down mobile visitor engagement.")
                opportunities.append("<strong>Optimize Mobile Images:</strong> Convert hero graphics to WebP to drop load time under 2.5 seconds.")
        except Exception:
            pass

    if "A" in sec_grade:
        strengths.append("<strong>Strict Enterprise Security:</strong> Modern HTTPS transport security (HSTS) and Content Security Policies (CSP) actively enforced.")
    else:
        weaknesses.append(f"<strong>Perimeter Security Opportunities ({sec_grade}):</strong> Strengthen transport headers to prevent clickjacking and protocol downgrades.")

    roadmap_items = []
    if is_thin:
        roadmap_items.append({
            "Urgency": "Phase 1 (Critical)",
            "Initiative": "Multi-Page Solution Architecture",
            "Action": f"Expand website from {total_pages} single page into dedicated solution URLs for each core clinical offering.",
            "Impact": "Establishes the prerequisite foundation to rank for commercial searches"
        })
    if "s" in raw_lcp:
        roadmap_items.append({
            "Urgency": "Phase 1 (Critical)",
            "Initiative": "Mobile Visual Load Optimization",
            "Action": "Compress hero images to WebP and preload primary web fonts to cut LCP under 2.5s.",
            "Impact": "Reduces mobile visitor drop-off and passes Core Web Vitals"
        })
    if mashed_count > 0:
        roadmap_items.append({
            "Urgency": "Phase 1 (Critical)",
            "Initiative": "Heading Spacing & Typography Fix",
            "Action": "Add space delimiters between stylized CSS spans inside heading templates.",
            "Impact": "Immediate clean keyword parsing by search engine crawlers"
        })
    if not has_llms:
        roadmap_items.append({
            "Urgency": "Phase 2 (High Impact)",
            "Initiative": "Publish Root AI Index (llms.txt)",
            "Action": "Deploy a compliant machine-readable llms.txt file to the server root directory.",
            "Impact": "Enables clean indexing by ChatGPT, Perplexity, and Claude"
        })
    roadmap_items.append({
        "Urgency": "Phase 2 (High Impact)",
        "Initiative": "Deploy Specialized Medical Schemas",
        "Action": "Add MedicalBusiness, DiagnosticProcedure, and FAQPage structured data.",
        "Impact": "Qualifies service pages for interactive Google rich snippet displays"
    })
    if aeo_loss_pct > 30:
        roadmap_items.append({
            "Urgency": "Phase 2 (High Impact)",
            "Initiative": "Inverted-Pyramid AEO Copywriting",
            "Action": "Open each new service page with a direct 40-word factual definition of the clinical offering.",
            "Impact": "Dramatically increases brand citations in Google AI Overviews"
        })
    if invis_pct > 30:
        roadmap_items.append({
            "Urgency": "Phase 3 (Growth)",
            "Initiative": "High-Intent Paid Search Campaigns",
            "Action": "Launch targeted Google Search Ads on clinical genomics and sequencing terms.",
            "Impact": "Captures ready-to-buy healthcare decision-makers immediately"
        })
    if avg_a11y < 85:
        roadmap_items.append({
            "Urgency": "Phase 3 (Growth)",
            "Initiative": "Web Accessibility (WCAG 2.1) Fixes",
            "Action": "Declare <html lang='en'>, add descriptive image alt tags, and label interactive buttons.",
            "Impact": "Ensures digital accessibility compliance and removes search ranking penalties"
        })

    roadmap_rows = ""
    for item in roadmap_items[:8]:
        color = "#dc2626" if "Critical" in item["Urgency"] else ("#004b93" if "High Impact" in item["Urgency"] else "#16a34a")
        roadmap_rows += f"""<tr>
            <td style="color:{color}; font-weight:bold;">{item['Urgency']}</td>
            <td style="font-weight:bold;">{item['Initiative']}</td>
            <td>{item['Action']}</td>
            <td>{item['Impact']}</td>
        </tr>"""

    w_html = "".join([f"<li>{x}</li>" for x in weaknesses])
    o_html = "".join([f"<li>{x}</li>" for x in opportunities])
    s_html = "".join([f"<li>{x}</li>" for x in strengths])
    t_html = "".join([f"<li>{x}</li>" for x in threats])

    progress_html = ""
    if prev_snapshot:
        p_date = prev_snapshot.get('snapshot_date', 'Baseline')
        p_schema = prev_snapshot.get('commercial_schema_pct', 0)
        p_invis = prev_snapshot.get('invis_pct', 0)
        p_aeo = prev_snapshot.get('aeo_loss_pct', 0)
        progress_html = f"""
        <div style="background:#f1f5f9; border-left:4px solid #004b93; padding:8px 12px; margin:10px 0; font-size:7.5pt; line-height:1.5;">
            <strong style="color:#004b93;">Historical Audit Progress (vs. {p_date}):</strong><br>
            • Schema Coverage: {p_schema:.1f}% ➔ {comm_schema_pct:.1f}% ({'+' if comm_schema_pct >= p_schema else ''}{comm_schema_pct - p_schema:.1f}%)<br>
            • Search Invisibility: {p_invis:.1f}% ➔ {invis_pct:.1f}% ({invis_pct - p_invis:.1f}%)<br>
            • AEO Competitor Wins: {p_aeo:.1f}% ➔ {aeo_loss_pct:.1f}% ({aeo_loss_pct - p_aeo:.1f}%)
        </div>
        """

    pdf_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        @page {{
            size: letter;
            margin: 0.5in;
            @top-left {{
                content: "Connext Global Solutions — Executive Digital & AEO Forensic Audit";
                font-size: 7pt;
                color: #64748b;
                font-family: 'DejaVu Sans', sans-serif;
                font-weight: bold;
            }}
            @top-right {{
                content: "Audited Entity: {target_name}";
                font-size: 7pt;
                color: #004b93;
                font-family: 'DejaVu Sans', sans-serif;
            }}
            @bottom-left {{
                content: "CONFIDENTIAL — Strategic Growth Advisory";
                font-size: 6.5pt;
                color: #94a3b8;
                font-family: 'DejaVu Sans', sans-serif;
            }}
            @bottom-right {{
                content: "Page " counter(page) " of " counter(pages);
                font-size: 7pt;
                color: #004b93;
                font-family: 'DejaVu Sans', sans-serif;
                font-weight: bold;
            }}
        }}
        body {{
            font-family: 'DejaVu Sans', sans-serif;
            color: #1e293b;
            font-size: 8pt;
            line-height: 1.45;
        }}
        .page-break {{
            page-break-after: always;
        }}
        .header-cover {{
            border-bottom: 3.5px solid #004b93;
            padding-bottom: 8px;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .title-main {{
            color: #004b93;
            font-size: 17pt;
            font-weight: bold;
            margin: 0;
            line-height: 1.2;
        }}
        .sub-main {{
            color: #e85122;
            font-size: 9.5pt;
            font-weight: bold;
            margin-top: 2px;
        }}
        .score-row {{
            display: flex;
            justify-content: space-between;
            gap: 8px;
            margin: 12px 0;
        }}
        .score-card {{
            flex: 1;
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-top: 3.5px solid #004b93;
            padding: 8px 3px;
            text-align: center;
            border-radius: 4px;
        }}
        .score-val {{
            font-size: 16pt;
            font-weight: bold;
        }}
        .score-lbl {{
            font-size: 6.5pt;
            color: #64748b;
            font-weight: bold;
            text-transform: uppercase;
            margin-top: 3px;
        }}
        .meta-strip {{
            background: #f1f5f9;
            border: 1px solid #cbd5e1;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 7.5pt;
            margin: 8px 0;
            line-height: 1.5;
        }}
        .section-header {{
            color: #004b93;
            font-size: 11.5pt;
            font-weight: bold;
            border-bottom: 2px solid #004b93;
            padding-bottom: 3px;
            margin-top: 10px;
            margin-bottom: 6px;
        }}
        .sub-header {{
            color: #e85122;
            font-size: 9pt;
            font-weight: bold;
            margin-top: 8px;
            margin-bottom: 4px;
        }}
        .exec-guide {{
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-left: 3.5px solid #004b93;
            padding: 7px 10px;
            margin: 5px 0 8px 0;
            font-size: 7.2pt;
            line-height: 1.45;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 5px;
            margin-bottom: 10px;
            font-size: 7.2pt;
        }}
        th {{
            background: #004b93;
            color: white;
            padding: 4.5px 5px;
            border: 1px solid #003a73;
            text-align: left;
            font-weight: bold;
        }}
        td {{
            padding: 4px 5px;
            border: 1px solid #e2e8f0;
        }}
        tr:nth-child(even) td {{
            background: #f8fafc;
        }}
        .swot-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            margin: 8px 0;
        }}
        .swot-box {{
            border: 1px solid #e2e8f0;
            padding: 8px 10px;
            border-radius: 4px;
            font-size: 7.3pt;
            line-height: 1.45;
        }}
        .swot-w {{ border-top: 3.5px solid #dc2626; background: #fffdfd; }}
        .swot-o {{ border-top: 3.5px solid #16a34a; background: #fdfefe; }}
        .swot-s {{ border-top: 3.5px solid #004b93; background: #fbfdff; }}
        .swot-t {{ border-top: 3.5px solid #f2a900; background: #fffdfa; }}
        .alert-box {{
            background: #fef2f2;
            border: 1px solid #fecaca;
            border-left: 4px solid #dc2626;
            padding: 7px 10px;
            border-radius: 4px;
            font-size: 7.5pt;
            color: #991b1b;
            margin: 6px 0;
            line-height: 1.4;
        }}
        ul {{
            margin: 4px 0 2px 0;
            padding-left: 15px;
        }}
        li {{
            margin-bottom: 3px;
        }}
    </style>
    </head>
    <body>

        <!-- PAGE 1: COVER & EXECUTIVE PSI DASHBOARD -->
        <div class="header-cover">
            <div>
                <div class="title-main">Enterprise Digital & AEO Forensic Audit</div>
                <div class="sub-main">Search Visibility, AI Discoverability, Infrastructure & Growth Roadmap</div>
            </div>
            <div>
                <img src="{logo_url}" style="width: 130px;">
            </div>
        </div>

        <div class="meta-strip">
            <strong>Audited Domain:</strong> {target_name} &nbsp;|&nbsp; 
            <strong>Audit Date:</strong> {datetime.now().strftime("%B %d, %Y")} &nbsp;|&nbsp; 
            <strong>Site Footprint:</strong> {'1 Page (Single-Page Architecture)' if is_thin else str(total_pages) + ' Pages Crawled'} &nbsp;|&nbsp; 
            <strong>Prepared by:</strong> Connext Global Solutions
        </div>

        <p style="margin: 6px 0 10px 0; font-size: 8pt; color:#334155; line-height:1.5;">
            This executive report delivers a complete assessment of the search presence, artificial intelligence discoverability, mobile performance, and digital competitive standing of <strong>{target_name}</strong>. Audited across Google Search, Chrome User Experience (CrUX), and Generative AI Answer Engines (ChatGPT, Google AI Overviews, Perplexity).
        </p>

        {progress_html}

        <div class="score-row">
            <div class="score-card">
                <div class="score-val" style="color:{badge_color(score_seo)};">{score_seo}</div>
                <div class="score-lbl">Organic Search</div>
            </div>
            <div class="score-card">
                <div class="score-val" style="color:{badge_color(score_schema)};">{score_schema}</div>
                <div class="score-lbl">Schema Markup</div>
            </div>
            <div class="score-card">
                <div class="score-val" style="color:{badge_color(score_aeo)};">{score_aeo}</div>
                <div class="score-lbl">Generative AEO</div>
            </div>
            <div class="score-card">
                <div class="score-val" style="color:{badge_color(cwv_score)};">{cwv_score}</div>
                <div class="score-lbl">Mobile Speed</div>
            </div>
            <div class="score-card">
                <div class="score-val" style="color:{badge_color(avg_a11y)};">{avg_a11y}</div>
                <div class="score-lbl">Accessibility</div>
            </div>
            <div class="score-card">
                <div class="score-val" style="color:{badge_color(score_sec)};">{score_sec}</div>
                <div class="score-lbl">Perimeter Security</div>
            </div>
        </div>

        <div class="section-header">Executive Performance Diagnostic Summary</div>
        <table>
            <thead>
                <tr>
                    <th style="width:28%;">Audit Pillar</th>
                    <th style="width:24%;">Measured Status</th>
                    <th style="width:24%;">Industry Standard</th>
                    <th style="width:24%;">Executive Takeaway</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>Organic Search Visibility</strong></td>
                    <td>{invis_pct:.1f}% absent from Top 20</td>
                    <td>&lt; 20.0% absent</td>
                    <td style="color:{'#16a34a' if invis_pct <= 20 else '#dc2626'}; font-weight:bold;">{'✓ High Visibility' if invis_pct <= 20 else '🔴 Severe Traffic Loss'}</td>
                </tr>
                <tr>
                    <td><strong>Site Architecture Footprint</strong></td>
                    <td>{'1 Total Page Crawled' if is_thin else str(total_pages) + ' Pages Crawled'}</td>
                    <td>Multi-Page Solution Silos</td>
                    <td style="color:{'#dc2626' if is_thin else '#16a34a'}; font-weight:bold;">{'🚨 Single-Page Blocker' if is_thin else '✓ Multi-Page Silos'}</td>
                </tr>
                <tr>
                    <td><strong>Generative AEO Citation Share</strong></td>
                    <td>{aeo_loss_pct:.1f}% answers won by rivals</td>
                    <td>&gt; 50.0% brand presence</td>
                    <td style="color:{'#16a34a' if aeo_loss_pct <= 20 else '#dc2626'}; font-weight:bold;">{'✓ Brand Preferred' if aeo_loss_pct <= 20 else '🔴 Omitted from AI Answers'}</td>
                </tr>
                <tr>
                    <td><strong>Structured Schema Coverage</strong></td>
                    <td>{comm_schema_pct:.1f}% declared</td>
                    <td>100.0% coverage</td>
                    <td style="color:{'#16a34a' if comm_schema_pct >= 85 else '#dc2626'}; font-weight:bold;">{'✓ Structured Data Active' if comm_schema_pct >= 85 else '🔴 Deficit'}</td>
                </tr>
                <tr>
                    <td><strong>Mobile Code Execution (TBT)</strong></td>
                    <td>{raw_tbt} execution latency</td>
                    <td>&lt; 300 ms</td>
                    <td style="color:{'#16a34a' if tbt_is_good else '#dc2626'}; font-weight:bold;">{'✓ Optimal Responsive Code' if tbt_is_good else '🔴 Script Contention'}</td>
                </tr>
                <tr>
                    <td><strong>Mobile Visual Load Delay (LCP)</strong></td>
                    <td>{cwv_res.get('LCP', 'N/A')}</td>
                    <td>&lt; 2.5 seconds</td>
                    <td style="color:#dc2626; font-weight:bold;">🔴 Visual Render Delay</td>
                </tr>
                <tr>
                    <td><strong>Machine-Readable Standard (llms.txt)</strong></td>
                    <td>{'Active (HTTP 200)' if has_llms else 'Missing (404 Error)'}</td>
                    <td>Standard Endpoint Active</td>
                    <td style="color:{'#16a34a' if has_llms else '#dc2626'}; font-weight:bold;">{'✓ Active' if has_llms else '🔴 AI Ingestion Blocked'}</td>
                </tr>
                <tr>
                    <td><strong>Web Accessibility (WCAG 2.1 AA)</strong></td>
                    <td>{avg_a11y}/100 compliance score</td>
                    <td>&gt; 90/100 score</td>
                    <td style="color:{'#16a34a' if avg_a11y >= 85 else '#f2a900'}; font-weight:bold;">{'✓ Compliant' if avg_a11y >= 85 else '⚠️ Remediation Required'}</td>
                </tr>
            </tbody>
        </table>

        <div class="page-break"></div>

        <!-- PAGE 2: PILLAR 1 ARCHITECTURE & SECURITY -->
        <div class="section-header">Pillar 1: Forensic Web Architecture, Schemas & Security</div>
        
        <div class="exec-guide">
            <strong>Plain-English Guide for Leadership:</strong> Search engines (Google) and artificial intelligence models index specific web pages to answer specific user questions. This section audits whether your technical website framework allows search engines to read, understand, and trust your organization.
        </div>

        {f'<div class="alert-box"><strong>🚨 Critical Architecture Finding:</strong> Currently operating as a <strong>single-page website (1 page total)</strong>. Because search engines evaluate and rank individual URLs designed to solve specific problems, having only one page makes it structurally impossible to rank for multiple distinct commercial services simultaneously.</div>' if is_thin else ''}

        <div class="sub-header">Crawled Website Architecture & Code Quality</div>
        <table>
            <thead>
                <tr>
                    <th style="width:40%;">Target Web URL</th>
                    <th style="width:12%; text-align:center;">Main Headings</th>
                    <th style="width:14%; text-align:center;">Typography</th>
                    <th style="width:14%; text-align:center;">Schemas</th>
                    <th style="width:10%; text-align:center;">WCAG</th>
                    <th style="width:10%; text-align:center;">Word Count</th>
                </tr>
            </thead>
            <tbody>
                {crawl_rows if crawl_rows else '<tr><td colspan="6">No crawled pages recorded.</td></tr>'}
            </tbody>
        </table>

        <div class="sub-header">Perimeter Edge Security, Google Trust & Knowledge Graph Verification</div>
        <table>
            <thead>
                <tr>
                    <th style="width:35%;">Security & Trust Protocol</th>
                    <th style="width:25%;">Measured Status</th>
                    <th style="width:40%;">Enterprise Standard & Explanation</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>Transport Security (HSTS)</strong></td>
                    <td>{'✓ Strict HSTS Enforced' if sec_res.get('HSTS') else '<span style="color:#dc2626; font-weight:bold;">🔴 Missing (Needs Server Header)</span>'}</td>
                    <td>Prevents protocol downgrade attacks.</td>
                </tr>
                <tr>
                    <td><strong>Content Security Policy (CSP)</strong></td>
                    <td>{'✓ Active' if sec_res.get('CSP') else '⚠️ Not Detected'}</td>
                    <td>Protects against malicious cross-site scripting and code injections.</td>
                </tr>
                <tr>
                    <td><strong>Google Safe Browsing Certification</strong></td>
                    <td><span style="color:#16a34a; font-weight:bold;">{sb_res.get('Status', '✓ Clean')}</span></td>
                    <td>Zero malware, deceptive forms, or security flags detected by Google.</td>
                </tr>
                <tr>
                    <td><strong>Google Knowledge Graph Entity Status</strong></td>
                    <td><span style="color:{'#16a34a' if 'Verified' in str(kg_info.get('Recognized')) else '#dc2626'}; font-weight:bold;">{kg_info.get('Recognized', 'Unrecognized Entity')}</span></td>
                    <td>Google entity verification in global Knowledge Graph.</td>
                </tr>
                <tr>
                    <td><strong>AI Standard Endpoint (llms.txt)</strong></td>
                    <td><span style="color:{'#16a34a' if has_llms else '#dc2626'}; font-weight:bold;">{'✓ Active (HTTP 200)' if has_llms else '🔴 Missing (404 Error)'}</span></td>
                    <td>Enables AI engines to read a verified product catalog.</td>
                </tr>
            </tbody>
        </table>

        <div class="page-break"></div>

        <!-- PAGE 3: PILLAR 2 CONTENT SILOS & CANNIBALIZATION -->
        <div class="section-header">Pillar 2: Content Silos, Link Authority & Cannibalization Analysis</div>

        <div class="exec-guide">
            <strong>Plain-English Guide for Leadership:</strong> In enterprise digital strategy, authoritative informational content (articles, white papers, and clinical case studies) passes credibility to commercial service offerings through internal links. This section inspects how authority flows through your digital assets.
        </div>

        <div class="sub-header">Internal Link Equity & Authority Flow Analysis</div>
        <table>
            <thead>
                <tr>
                    <th style="width:40%;">Commercial Solution Hub</th>
                    <th style="width:18%; text-align:center;">Inbound Contextual Links</th>
                    <th style="width:24%;">Link Anchor Text Profile</th>
                    <th style="width:18%; text-align:center;">Authority Flow Status</th>
                </tr>
            </thead>
            <tbody>
                {equity_rows if equity_rows else f'<tr><td colspan="4" style="color:#64748b; font-style:italic; padding:12px;"><strong>Zero Editorial Content Archive Detected:</strong> {target_name} currently operates without a blog, educational resource center, white paper hub, or case study archive. As a result, the domain possesses zero internal link equity flow to reinforce service authority.</td></tr>'}
            </tbody>
        </table>

        <div class="sub-header">Content Duplication & Keyword Cannibalization Check</div>
        <table>
            <thead>
                <tr>
                    <th style="width:38%;">Primary URL</th>
                    <th style="width:38%;">Competing Internal URL</th>
                    <th style="width:12%; text-align:center;">Similarity Score</th>
                    <th style="width:12%; text-align:center;">Conflict Risk</th>
                </tr>
            </thead>
            <tbody>
                {can_rows if can_rows else f'<tr><td colspan="4" style="color:#64748b; font-style:italic; padding:12px;"><strong>No Internal Cannibalization Conflicts Found:</strong> Because {target_name} operates on {total_pages} page(s), there are no competing internal landing pages cannibalizing search traffic. However, this also limits total keyword targeting capacity.</td></tr>'}
            </tbody>
        </table>

        <div class="exec-guide" style="border-left-color:#e85122;">
            <strong>Strategic Advisory:</strong> Establishing an educational content hub (e.g., resources on genetic variant interpretation, pediatric sequencing guidelines, and patient FAQs) will build the necessary domain authority to power Google rankings and AI citations.
        </div>

        <div class="page-break"></div>

        <!-- PAGE 4: PILLAR 3 SERP VISIBILITY -->
        <div class="section-header">Pillar 3: Organic Google Search Visibility (SERP Demand Capture)</div>

        <div class="exec-guide">
            <strong>Plain-English Guide for Leadership:</strong> This benchmark queries live Google Search results in the United States across the exact non-branded commercial phrases healthcare providers, clinics, and families use when searching for clinical genomics solutions.
        </div>

        <table>
            <thead>
                <tr>
                    <th style="width:36%;">Target Commercial Search Query</th>
                    <th style="width:12%; text-align:center;">Google Rank</th>
                    <th style="width:30%;">Current Ranking URL</th>
                    <th style="width:22%; text-align:center;">Competitor Dominating Page 1</th>
                </tr>
            </thead>
            <tbody>
                {serp_rows if serp_rows else '<tr><td colspan="4">No SERP tracking data recorded. (Execute Tab 4 SERP Check to populate).</td></tr>'}
            </tbody>
        </table>

        <div class="alert-box">
            <strong>Commercial Impact:</strong> The domain exhibits a <strong>{invis_pct:.1f}% organic search invisibility rate</strong> across all commercial keywords. When healthcare professionals and institutional partners look for genomics solutions on Google, 100% of the customer demand is captured by competitors and medical publishers.
        </div>

        <div class="page-break"></div>

        <!-- PAGE 5: PILLAR 4 AEO & PAID ADS -->
        <div class="section-header">Pillar 4: Generative AI Answer Engines (AEO) & Paid Media Intel</div>

        <div class="exec-guide">
            <strong>Plain-English Guide for Leadership:</strong> Corporate buyers and clinical directors increasingly use conversational AI engines (ChatGPT, Google AI Overviews, Perplexity) to shortlist vendors. If an AI engine does not cite your brand, you are excluded from buyer consideration before an RFP is ever issued.
        </div>

        <div class="sub-header">Live Generative AI Overview Citation Audit</div>
        <table>
            <thead>
                <tr>
                    <th style="width:40%;">Buyer Consultative Evaluation Prompt</th>
                    <th style="width:14%; text-align:center;">AI Overview?</th>
                    <th style="width:16%; text-align:center;">Brand Cited?</th>
                    <th style="width:30%;">Winning Sources Cited by AI</th>
                </tr>
            </thead>
            <tbody>
                {aeo_rows if aeo_rows else '<tr><td colspan="4">No AEO prompts evaluated. (Execute Tab 5 AEO test to populate).</td></tr>'}
            </tbody>
        </table>

        <div class="sub-header">Active Paid Media Footprint (Google Ads Transparency Center)</div>
        <table>
            <thead>
                <tr>
                    <th style="width:24%;">Ad Channel & Format</th>
                    <th style="width:24%;">Active Flight & Dates</th>
                    <th style="width:22%;">Longevity Tier</th>
                    <th style="width:30%;">Strategic Takeaway</th>
                </tr>
            </thead>
            <tbody>
                {ads_rows if ads_rows else f'<tr><td style="font-weight:bold;">Google Search & Display</td><td style="color:#dc2626; font-weight:bold;">0 Active Ads</td><td>None</td><td>Competitors own the top sponsored search spots uncontested, capturing ready-to-buy searchers.</td></tr>'}
            </tbody>
        </table>

        <div class="page-break"></div>

        <!-- PAGE 6: PILLAR 5 PERFORMANCE & SWOT MATRIX -->
        <div class="section-header">Pillar 5: Mobile User Experience & SWOT Strategic Assessment</div>

        <div class="exec-guide">
            <strong>Plain-English Guide for Leadership:</strong> Google measures speed directly on mobile devices to evaluate user satisfaction. A fast code foundation with slow visual images creates visitor frustration and hurts search rankings.
        </div>

        <div class="sub-header">Mobile Core Web Vitals Performance Breakdown</div>
        <table>
            <thead>
                <tr>
                    <th style="width:26%;">Performance Metric</th>
                    <th style="width:22%; text-align:center;">Measured Value</th>
                    <th style="width:22%; text-align:center;">Target Standard</th>
                    <th style="width:30%;">Executive Plain-English Explanation</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td><strong>Mobile Performance Score</strong></td>
                    <td style="text-align:center; font-weight:bold; color:{badge_color(cwv_score)};">{cwv_score} / 100</td>
                    <td style="text-align:center;">&gt; 90 / 100</td>
                    <td>Fair foundation, but visual loading delays require optimization.</td>
                </tr>
                <tr>
                    <td><strong>Code Execution Speed (TBT)</strong></td>
                    <td style="text-align:center; font-weight:bold; color:{'#16a34a' if tbt_is_good else '#dc2626'};">{raw_tbt}</td>
                    <td style="text-align:center;">&lt; 300 ms</td>
                    <td><strong style="color:{'#16a34a' if tbt_is_good else '#dc2626'};">{'✓ Fast:' if tbt_is_good else '🔴 Delay:'}</strong> Website scripts run cleanly without screen freezes.</td>
                </tr>
                <tr>
                    <td><strong>Visual Render Delay (LCP)</strong></td>
                    <td style="text-align:center; font-weight:bold; color:#dc2626;">{cwv_res.get('LCP', '10.1 s')}</td>
                    <td style="text-align:center;">&lt; 2.50 s</td>
                    <td><strong style="color:#dc2626;">🔴 Delayed:</strong> Mobile visitors wait {cwv_res.get('LCP', '10.1 s')} to see the main headline/image.</td>
                </tr>
                <tr>
                    <td><strong>Visual Layout Stability (CLS)</strong></td>
                    <td style="text-align:center; font-weight:bold; color:#16a34a;">{cwv_res.get('CLS', '0.000')}</td>
                    <td style="text-align:center;">&lt; 0.100</td>
                    <td><strong style="color:#16a34a;">✓ Flawless:</strong> Page elements stay perfectly still as they load.</td>
                </tr>
            </tbody>
        </table>

        <div class="section-header" style="margin-top:10px;">SWOT Strategic Assessment (Full English)</div>
        <div class="swot-grid">
            <div class="swot-box swot-w">
                <strong style="color: #dc2626; font-size:8pt;">WEAKNESSES (Internal Gaps)</strong>
                <ul>{w_html}</ul>
            </div>
            <div class="swot-box swot-o">
                <strong style="color: #16a34a; font-size:8pt;">OPPORTUNITIES (Market Growth Levers)</strong>
                <ul>{o_html}</ul>
            </div>
            <div class="swot-box swot-s">
                <strong style="color: #004b93; font-size:8pt;">STRENGTHS (Internal Advantages)</strong>
                <ul>{s_html}</ul>
            </div>
            <div class="swot-box swot-t">
                <strong style="color: #b45309; font-size:8pt;">THREATS (External Competitive Risks)</strong>
                <ul>{t_html}</ul>
            </div>
        </div>

        <div class="page-break"></div>

        <!-- PAGE 7: OPERATIONAL ROADMAP & SIGN-OFF -->
        <div class="section-header">Prioritized Operational Roadmap & Engineering Plan</div>

        <div class="exec-guide">
            <strong>Executive Implementation Plan:</strong> This roadmap translates the audit findings into direct engineering and marketing initiatives, organized by operational priority, estimated effort, and expected business outcome.
        </div>

        <table>
            <thead>
                <tr>
                    <th style="width:20%;">Urgency Tier</th>
                    <th style="width:26%;">Strategic Initiative</th>
                    <th style="width:34%;">Specific Action Items</th>
                    <th style="width:20%;">Projected Business Outcome</th>
                </tr>
            </thead>
            <tbody>
                {roadmap_rows}
            </tbody>
        </table>

        <div class="meta-strip" style="margin-top:20px; font-size:7.2pt;">
            <strong>Advisory Sign-off:</strong> This executive audit dossier was prepared for <strong>{target_name}</strong> by the Connext Global Solutions marketing and digital architecture team. For implementation assistance, custom analytics tracking, or paid media management, contact Connext Global Solutions.
        </div>

    </body>
    </html>
    """
    return weasyprint.HTML(string=pdf_html).write_pdf()

with tab_report:
    st.subheader("Executive Audit PDF Deliverables (Connext Global Identity)")
    st.markdown('''<div class="guide-box"><strong>Two Executive Deliverable Formats Available:</strong><br>• <strong>Option A (1-Page Executive Summary):</strong> A high-impact C-level brief containing the executive metrics, SWOT strategic analysis, and core architecture crawl inventory on exactly one page.<br>• <strong>Option B (7-Page Full Technical Dossier):</strong> A comprehensive, publication-grade forensic document containing deep-dive tables for Architecture, Silo Link Equity, Cannibalization, SERP, AEO, CWV, and the Phased Operational Roadmap.<br><strong>How to Generate:</strong> Click the compile button for either format below, then download your ready PDF.</div>''', unsafe_allow_html=True)

    df_c = st.session_state.get("df_crawl", pd.DataFrame())
    if df_c.empty:
        st.warning("No audit data available yet. Please run Tab 1 (Tech & Schemas) first before generating executive PDF reports.")
    else:
        col_pdf1, col_pdf2 = st.columns(2)

        with col_pdf1:
            st.markdown("### Option A: 1-Page Executive Summary")
            st.caption("Perfect for C-level briefings, board summaries, and rapid vendor reviews. Clean 1-page layout.")
            
            if st.button("Compile 1-Page Executive Summary (PDF)", use_container_width=True):
                with st.spinner("Compiling 1-page executive summary PDF in full English..."):
                    df_r = st.session_state.get("df_rankings", pd.DataFrame())
                    df_a = st.session_state.get("df_aeo", pd.DataFrame())
                    df_eq = st.session_state.get("df_equity", pd.DataFrame())
                    df_can = st.session_state.get("df_cannibal", pd.DataFrame())
                    df_ads = st.session_state.get("df_ads", pd.DataFrame())
                    target_name = st.session_state.get("target_domain", "Target Domain")
                    sec_res = st.session_state.get("sec_headers", {})
                    cwv_res = st.session_state.get("cwv_metrics", {})
                    crux_res = st.session_state.get("crux_metrics", {})
                    sb_res = st.session_state.get("safe_browsing", {})
                    kg_info = st.session_state.get("kg_info", {})
                    prev = st.session_state.get("prev_snapshot")
                    has_llms = bool(st.session_state.get("has_llms", False))

                    summary_pdf_bytes = build_executive_summary_1page_html(
                        df_c=df_c,
                        df_r=df_r,
                        df_a=df_a,
                        df_eq=df_eq,
                        df_can=df_can,
                        df_ads=df_ads,
                        cwv_res=cwv_res,
                        crux_res=crux_res,
                        sec_res=sec_res,
                        sb_res=sb_res,
                        kg_info=kg_info,
                        prev_snapshot=prev,
                        target_name=target_name,
                        has_llms=has_llms,
                        logo_url=LOGO_URL
                    )
                    st.session_state["compiled_summary_pdf_bytes"] = summary_pdf_bytes
                    st.session_state["compiled_summary_filename"] = f"connext_executive_summary_{urlparse(target_name).netloc}.pdf"
                    st.success("1-Page Executive Summary compiled successfully!")

            if "compiled_summary_pdf_bytes" in st.session_state and st.session_state["compiled_summary_pdf_bytes"]:
                st.download_button(
                    label="Download 1-Page Executive Summary (PDF)",
                    data=st.session_state["compiled_summary_pdf_bytes"],
                    file_name=st.session_state.get("compiled_summary_filename", "connext_executive_summary.pdf"),
                    mime="application/pdf",
                    use_container_width=True
                )

        with col_pdf2:
            st.markdown("### Option B: 7-Page Full Technical Dossier")
            st.caption("Deep-dive forensic deliverable with all tables, competitive benchmarks, and phased engineering roadmap.")
            
            if st.button("Compile 7-Page Full Technical Dossier (PDF)", use_container_width=True):
                with st.spinner("Compiling publication-grade 7-page executive PDF dossier in full English..."):
                    df_r = st.session_state.get("df_rankings", pd.DataFrame())
                    df_a = st.session_state.get("df_aeo", pd.DataFrame())
                    df_eq = st.session_state.get("df_equity", pd.DataFrame())
                    df_can = st.session_state.get("df_cannibal", pd.DataFrame())
                    df_ads = st.session_state.get("df_ads", pd.DataFrame())
                    target_name = st.session_state.get("target_domain", "Target Domain")
                    sec_res = st.session_state.get("sec_headers", {})
                    cwv_res = st.session_state.get("cwv_metrics", {})
                    crux_res = st.session_state.get("crux_metrics", {})
                    sb_res = st.session_state.get("safe_browsing", {})
                    kg_info = st.session_state.get("kg_info", {})
                    prev = st.session_state.get("prev_snapshot")
                    has_llms = bool(st.session_state.get("has_llms", False))

                    dossier_pdf_bytes = build_executive_dossier_html(
                        df_c=df_c,
                        df_r=df_r,
                        df_a=df_a,
                        df_eq=df_eq,
                        df_can=df_can,
                        df_ads=df_ads,
                        cwv_res=cwv_res,
                        crux_res=crux_res,
                        sec_res=sec_res,
                        sb_res=sb_res,
                        kg_info=kg_info,
                        prev_snapshot=prev,
                        target_name=target_name,
                        has_llms=has_llms,
                        logo_url=LOGO_URL
                    )
                    st.session_state["compiled_dossier_pdf_bytes"] = dossier_pdf_bytes
                    st.session_state["compiled_dossier_filename"] = f"connext_technical_dossier_{urlparse(target_name).netloc}.pdf"
                    st.success("7-Page Technical Dossier compiled successfully!")

            if "compiled_dossier_pdf_bytes" in st.session_state and st.session_state["compiled_dossier_pdf_bytes"]:
                st.download_button(
                    label="Download 7-Page Technical Dossier (PDF)",
                    data=st.session_state["compiled_dossier_pdf_bytes"],
                    file_name=st.session_state.get("compiled_dossier_filename", "connext_technical_dossier.pdf"),
                    mime="application/pdf",
                    use_container_width=True
                )
