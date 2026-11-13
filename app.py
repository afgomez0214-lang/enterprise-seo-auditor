import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import weasyprint
import xml.etree.ElementTree as ET
import html
import base64
from urllib.parse import urlparse, urljoin, parse_qs, urlencode, urlunparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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

def discover_target_urls(root, target_site, filter_term, crawl_limit, headers, whole_website=False):
    """
    Recursively unpacks XML Sitemap Indexes (including Shopify/CMS child sitemaps with ?from=... query strings),
    extracts inner HTML pages, applies category/slug filtering, and spiders internal category links as fallback.
    Supports both custom page caps (up to 500) and full 'Whole Website' mode.
    """
    root_netloc = urlparse(root).netloc.replace("www.", "")
    filter_clean = filter_term.strip().lower()
    effective_cap = 2500 if whole_website else crawl_limit
    
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
        if not whole_website and len(discovered_html_urls) >= effective_cap * 4 and not child_sitemaps_queue:
            break

    # Step B: Recursively unpack child sitemaps (supports nested sitemap indexes & Whole Website mode)
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

        max_child_sitemaps = 120 if (whole_website or effective_cap > 80) else 25
        idx_cs = 0
        while idx_cs < len(ordered_children) and len(visited_sitemaps) < max_child_sitemaps:
            cs = ordered_children[idx_cs]
            idx_cs += 1
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
            for nested_sm in sub_sms:
                if nested_sm not in visited_sitemaps and nested_sm not in ordered_children:
                    ordered_children.append(nested_sm)
            for pu in sub_pages:
                if is_valid_html_page_url(pu, root_netloc):
                    discovered_html_urls.append((pu, cs))
            if not whole_website and len(discovered_html_urls) >= effective_cap * 6:
                break

    # Step C: Filter discovered HTML pages by category / slug / keyword filter
    filtered_urls = []
    seen_u = set()
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

    # Step D: Live HTML Spider Fallback (Breadth-first spidering across internal pages when needed)
    if len(filtered_urls) < effective_cap:
        spider_queue = [target_site, root]
        if filter_clean:
            for t in [x.strip().strip("/") for x in filter_clean.split(",") if x.strip()]:
                if not t.startswith("http"):
                    spider_queue.insert(0, f"{root}/{t}")
        for f_u in list(filtered_urls)[:10]:
            if f_u not in spider_queue:
                spider_queue.append(f_u)

        visited_spider = set()
        max_spider_pages = 45 if (whole_website or effective_cap > 80) else 8
        q_idx = 0
        while q_idx < len(spider_queue) and len(visited_spider) < max_spider_pages and len(filtered_urls) < effective_cap:
            s_url = spider_queue[q_idx]
            q_idx += 1
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
                                    if len(spider_queue) < max_spider_pages * 2:
                                        spider_queue.append(full_u)
                                else:
                                    terms = [t.strip() for t in filter_clean.split(",") if t.strip()]
                                    if any(t in full_u.lower() for t in terms):
                                        seen_u.add(norm_u)
                                        filtered_urls.append(full_u)
                                        if len(spider_queue) < max_spider_pages * 2:
                                            spider_queue.append(full_u)
            except Exception:
                continue

    if not filtered_urls:
        filtered_urls = [target_site]

    return filtered_urls[:effective_cap], unpacked_sitemaps_log

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

    # 3. Also check RDFa typeof attributes (vocab="https://schema.org/")
    for tag in soup.find_all(attrs={"typeof": True}):
        vocab = str(tag.get("vocab", "")).lower()
        parent_vocab = tag.find_parent(attrs={"vocab": True})
        p_vocab_str = str(parent_vocab.get("vocab", "")).lower() if parent_vocab else ""
        if "schema.org" in vocab or "schema.org" in p_vocab_str:
            for tv in str(tag.get("typeof", "")).split():
                clean_tv = tv.split("/")[-1].strip()
                if clean_tv and clean_tv[0].isupper():
                    block_count += 1
                    found_types.append(clean_tv)

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

TECH_FINGERPRINTS = [
    {"name": "Shopify", "category": "Ecommerce / CMS", "weight": "Medium", "html": [r"cdn\.shopify\.com", r"Shopify\.theme", r"shopify-section"], "headers": ["x-shopify-stage", "x-sorting-hat-shopid"]},
    {"name": "WordPress", "category": "CMS", "weight": "Medium", "html": [r"/wp-content/", r"/wp-includes/"], "meta_gen": r"WordPress\s*([\d\.]+)?"},
    {"name": "Webflow", "category": "CMS", "weight": "Light", "html": [r"assets\.website-files\.com", r"assets-global\.website-files\.com", r"data-wf-site"], "meta_gen": r"Webflow"},
    {"name": "HubSpot CMS", "category": "CMS & Marketing", "weight": "Heavy", "html": [r"hs-scripts\.com", r"hubspot\.com", r"hs-banner"], "meta_gen": r"HubSpot"},
    {"name": "Wix", "category": "CMS", "weight": "Heavy", "html": [r"static\.wixstatic\.com", r"wix-code"], "meta_gen": r"Wix\.com"},
    {"name": "Squarespace", "category": "CMS", "weight": "Medium", "html": [r"static1\.squarespace\.com", r"Static\.SQUARESPACE_CONTEXT"]},
    {"name": "Next.js", "category": "Web Framework", "weight": "Light", "html": [r"__NEXT_DATA__", r"/_next/static/"], "headers": ["x-nextjs-cache", "x-vercel-id"]},
    {"name": "React", "category": "JS Framework", "weight": "Medium", "html": [r"react-dom", r"data-reactroot", r"__REACT_DEVTOOLS"]},
    {"name": "Vue.js", "category": "JS Framework", "weight": "Light", "html": [r"data-v-[a-f0-9]+", r"vue\.runtime", r"__VUE__"]},
    {"name": "jQuery", "category": "JS Library", "weight": "Medium", "html": [r"jquery(?:-[\d\.]+)?(?:\.min)?\.js"]},
    {"name": "Google Tag Manager", "category": "Tag Manager", "weight": "Medium", "html": [r"googletagmanager\.com/gtm\.js\?id=(GTM-[A-Z0-9]+)"]},
    {"name": "Google Analytics 4 (GA4)", "category": "Analytics", "weight": "Medium", "html": [r"googletagmanager\.com/gtag/js\?id=(G-[A-Z0-9]+)", r"gtag\(\s*['\"]config['\"]\s*,\s*['\"](G-[A-Z0-9]+)['\"]"]},
    {"name": "Microsoft Clarity", "category": "Heatmaps & Session Replay", "weight": "Medium", "html": [r"clarity\.ms/tag/"]},
    {"name": "Hotjar", "category": "Heatmaps & Session Replay", "weight": "Heavy", "html": [r"static\.hotjar\.com", r"hjSetting"]},
    {"name": "Contentsquare", "category": "Heatmaps & Session Replay", "weight": "Heavy", "html": [r"contentsquare\.net"]},
    {"name": "Segment", "category": "Customer Data Platform", "weight": "Medium", "html": [r"cdn\.segment\.com/analytics\.js"]},
    {"name": "Meta (Facebook) Pixel", "category": "Advertising Pixel", "weight": "Medium", "html": [r"connect\.facebook\.net/.*/fbevents\.js", r"fbq\(\s*['\"]init['\"]"]},
    {"name": "LinkedIn Insight Tag", "category": "Advertising Pixel", "weight": "Medium", "html": [r"snap\.licdn\.com/li\.lms-analytics", r"_linkedin_partner_id"]},
    {"name": "Google Ads Conversion / Remarketing", "category": "Advertising Pixel", "weight": "Medium", "html": [r"googleadservices\.com/pagead/conversion", r"AW-[0-9]+"]},
    {"name": "TikTok Pixel", "category": "Advertising Pixel", "weight": "Medium", "html": [r"analytics\.tiktok\.com"]},
    {"name": "Klaviyo", "category": "Email & Popups", "weight": "Heavy", "html": [r"static\.klaviyo\.com", r"klaviyo\.js"]},
    {"name": "Intercom", "category": "Live Chat", "weight": "Heavy", "html": [r"widget\.intercom\.io", r"intercomSettings"]},
    {"name": "Drift", "category": "Live Chat", "weight": "Heavy", "html": [r"js\.driftt\.com", r"drift\.load"]},
    {"name": "Zendesk Chat", "category": "Live Chat", "weight": "Heavy", "html": [r"static\.zdassets\.com", r"zE\("]},
    {"name": "Yotpo Reviews", "category": "Reviews & UGC", "weight": "Heavy", "html": [r"staticw2\.yotpo\.com"]},
    {"name": "Judge.me Reviews", "category": "Reviews & UGC", "weight": "Light", "html": [r"cdn\.judge\.me"]},
    {"name": "Recharge Subscriptions", "category": "Subscriptions", "weight": "Medium", "html": [r"rechargeapps\.com"]},
    {"name": "Yoast SEO", "category": "SEO Plugin", "weight": "Light", "html": [r"yoast-schema-graph", r"This site is optimized with the Yoast SEO"]},
    {"name": "Rank Math SEO", "category": "SEO Plugin", "weight": "Light", "html": [r"rank-math-schema", r"Rank Math SEO"]},
    {"name": "Cloudflare", "category": "CDN & Security", "weight": "Light", "headers": ["cf-ray", "cf-cache-status"]},
    {"name": "Vercel", "category": "Cloud Hosting / Edge", "weight": "Light", "headers": ["x-vercel-id", "x-vercel-cache"]},
    {"name": "CloudFront (AWS)", "category": "CDN", "weight": "Light", "headers": ["x-amz-cf-id", "x-amz-cf-pop"]}
]

def detect_tech_stack_and_weaknesses(raw_html, resp_headers, soup):
    """Wappalyzer-style passive technology detector + automated SEO, Speed, Tracking & Hygiene weakness analyzer."""
    headers_lower = {str(k).lower(): str(v) for k, v in (resp_headers or {}).items()}
    meta_gen_tag = soup.find("meta", attrs={"name": re.compile(r"^generator$", re.I)})
    meta_gen_val = meta_gen_tag.get("content", "").strip() if meta_gen_tag else ""

    detected_tools = []
    seen_names = set()
    for fp in TECH_FINGERPRINTS:
        matched = False
        version_or_id = ""
        for h_key in fp.get("headers", []):
            if h_key in headers_lower:
                matched = True
                break
        if not matched and fp.get("meta_gen") and meta_gen_val:
            m_gen = re.search(fp["meta_gen"], meta_gen_val, re.I)
            if m_gen:
                matched = True
                if m_gen.groups() and m_gen.group(1):
                    version_or_id = m_gen.group(1)
        if not matched and fp.get("html"):
            for pat in fp["html"]:
                m_html = re.search(pat, raw_html, re.I)
                if m_html:
                    matched = True
                    if m_html.groups() and m_html.group(1):
                        version_or_id = m_html.group(1)
                    break
        if matched and fp["name"] not in seen_names:
            seen_names.add(fp["name"])
            detected_tools.append({
                "Tool": fp["name"],
                "Category": fp["category"],
                "Performance_Weight": fp["weight"],
                "Version_or_ID": version_or_id or "Detected"
            })

    blocking_head_scripts = 0
    if soup.head:
        for sc in soup.head.find_all("script", src=True):
            if not sc.has_attr("async") and not sc.has_attr("defer") and sc.get("type") != "module":
                blocking_head_scripts += 1

    ga4_ids = set(re.findall(r"G-[A-Z0-9]{6,}", raw_html))
    jq_versions = re.findall(r"jquery[-/]([12]\.\d+(?:\.\d+)?|3\.[0-4]\.\d+)", raw_html, re.I)

    weaknesses = []
    tool_names = {t["Tool"] for t in detected_tools}
    heavy_tools = [t["Tool"] for t in detected_tools if t["Performance_Weight"] == "Heavy"]
    heatmap_tools = [t["Tool"] for t in detected_tools if t["Category"] == "Heatmaps & Session Replay"]
    ad_pixels = [t["Tool"] for t in detected_tools if t["Category"] == "Advertising Pixel"]

    if blocking_head_scripts >= 4:
        weaknesses.append({"Area": "⚡ Speed & Core Web Vitals", "Severity": "High", "Finding": f"{blocking_head_scripts} render-blocking scripts in <head> without async/defer.", "Recommendation": "Add 'defer' or load third-party scripts via GTM after page interaction to cut mobile LCP/TBT."})
    if len(heavy_tools) >= 2:
        weaknesses.append({"Area": "⚡ Third-Party App Bloat", "Severity": "High", "Finding": f"Multiple heavy scripts active simultaneously ({', '.join(heavy_tools)}).", "Recommendation": "Audit app ROI and delay chat/popup widgets by 3–5 seconds so they do not compete with mobile rendering."})
    if len(heatmap_tools) >= 2:
        weaknesses.append({"Area": "📊 Redundant Analytics Tools", "Severity": "Medium", "Finding": f"Overlapping session-recording tools detected ({', '.join(heatmap_tools)}).", "Recommendation": "Consolidate to a single heatmap platform to reduce CPU overhead."})
    if "Google Tag Manager" not in tool_names and "Google Analytics 4 (GA4)" not in tool_names:
        weaknesses.append({"Area": "📈 Analytics & Attribution Blind Spot", "Severity": "Critical", "Finding": "Neither Google Tag Manager (GTM) nor GA4 was detected in the page source.", "Recommendation": "Deploy GTM and GA4 immediately to track organic/paid conversions."})
    if len(ga4_ids) > 1:
        weaknesses.append({"Area": "📈 Duplicate Analytics Tracking", "Severity": "High", "Finding": f"Multiple GA4 Measurement IDs firing on the same page ({', '.join(sorted(ga4_ids))}).", "Recommendation": "Remove duplicate gtag snippets so sessions and conversions are not double-counted."})
    if not ad_pixels:
        weaknesses.append({"Area": "🎯 Paid Retargeting Gap", "Severity": "Medium", "Finding": "No Meta, LinkedIn, or Google Ads remarketing pixels detected.", "Recommendation": "Install conversion and remarketing pixels via GTM to build retargeting audiences."})
    if "Shopify" in tool_names:
        weaknesses.append({"Area": "🔍 Shopify SEO Architecture", "Severity": "Medium", "Finding": "Shopify detected — default themes often generate duplicate /collections/*/products/* URLs.", "Recommendation": "Verify canonical tags point to root /products/ URLs and enrich JSON-LD with FAQPage and AggregateRating schema."})
    if "React" in tool_names and "Next.js" not in tool_names:
        weaknesses.append({"Area": "🤖 AI & Search Crawlability", "Severity": "High", "Finding": "Client-side React detected without Next.js SSR indicators.", "Recommendation": "Ensure server-side rendering (SSR) or static pre-rendering is active so AI crawlers see full text."})
    if meta_gen_val and re.search(r"\d+\.\d+", meta_gen_val):
        weaknesses.append({"Area": "🛡️ Defensive Hygiene", "Severity": "Low", "Finding": f"CMS version exposed in meta generator tag ('{meta_gen_val}').", "Recommendation": "Hide public CMS generator version strings in template settings."})
    if jq_versions:
        weaknesses.append({"Area": "🛡️ Frontend Library Maintenance", "Severity": "Medium", "Finding": f"Legacy jQuery version detected (v{jq_versions[0]}).", "Recommendation": "Upgrade to jQuery 3.7+ (or native vanilla JS) to eliminate deprecated DOM APIs."})

    return detected_tools, weaknesses

def analyze_brand_serp_top_pages(brand_query, target_domain, serpapi_key):
    """
    Queries live Google Search for the brand name to see:
    1) Which pages of the brand Google ranks at the top (#1 organic + Google Sitelinks = most valuable pages)
    2) Whether competitors or third-party review sites steal top branded positions
    3) Whether a Google Knowledge Graph Panel appears on the right-hand side
    """
    if not serpapi_key or not brand_query:
        return pd.DataFrame(), [], {}
    target_host = urlparse(target_domain).netloc.replace("www.", "").lower() if "http" in target_domain else target_domain.replace("www.", "").lower()
    params = {"engine": "google", "q": brand_query, "location": "United States", "hl": "en", "gl": "us", "num": 15, "api_key": serpapi_key}
    res, _ = fetch_serpapi_with_retry(params, max_retries=2, timeout=28)
    if not res:
        return pd.DataFrame(), [], {}

    kg_raw = res.get("knowledge_graph", {})
    kg_summary = {
        "Has_Panel": bool(kg_raw),
        "Title": kg_raw.get("title", "None"),
        "Type": kg_raw.get("type", "None"),
        "Website": kg_raw.get("website", "None"),
        "Description": kg_raw.get("description", "No Knowledge Graph panel displayed on branded SERP.")
    }

    sitelinks_extracted = []
    rows = []
    for pos, item in enumerate(res.get("organic_results", [])[:12], 1):
        link = item.get("link", "")
        clean_l = clean_display_url(link)
        dom = urlparse(link).netloc.replace("www.", "").lower()
        is_own = (target_host in dom) if target_host else False

        # Extract Google Sitelinks (these are the exact pages Google deems most valuable to users!)
        sl_titles = []
        sitelinks_obj = item.get("sitelinks", {})
        if isinstance(sitelinks_obj, dict):
            for grp in sitelinks_obj.values():
                if isinstance(grp, list):
                    for sl in grp:
                        if isinstance(sl, dict) and sl.get("title"):
                            sl_titles.append(sl["title"])
                            if is_own:
                                sitelinks_extracted.append({
                                    "Sitelink_Title": sl.get("title", ""),
                                    "Sitelink_URL": clean_display_url(sl.get("link", "")),
                                    "Sitelink_Snippet": sl.get("snippet", "Primary Google Sitelink")
                                })

        if is_own and pos == 1:
            val_tier = "👑 #1 Brand Anchor + Sitelinks Hub" if sl_titles else "👑 #1 Primary Brand Result"
        elif is_own:
            val_tier = "🟢 Secondary Brand Page (High Authority)"
        else:
            val_tier = "⚠️ Third-Party / Competitor on Brand SERP"

        rows.append({
            "SERP_Pos": f"#{pos}",
            "Ownership": "🟢 Your Website" if is_own else "🌐 External / Competitor",
            "Valuable_Page_Tier": val_tier,
            "Page_Title_Shown_in_Google": item.get("title", "N/A"),
            "Displayed_URL": clean_l,
            "Google_Sitelinks_Awarded": " | ".join(sl_titles[:6]) if sl_titles else "None"
        })

    return pd.DataFrame(rows), sitelinks_extracted, kg_summary

def audit_robots_and_ai_bots(root_url, target_urls, headers):
    """Parses robots.txt to audit AI crawler governance (GPTBot, ClaudeBot, PerplexityBot, Google-Extended) and Disallow conflicts."""
    robots_url = f"{root_url.rstrip('/')}/robots.txt"
    result = {
        "exists": False,
        "status_code": 0,
        "sitemaps_declared": [],
        "ai_bots": [],
        "blocked_target_urls": [],
        "raw_preview": ""
    }
    ai_agents = [
        ("GPTBot", "OpenAI Search & Training Crawler"),
        ("OAI-SearchBot", "ChatGPT Live Web Search Bot"),
        ("ChatGPT-User", "ChatGPT User-Triggered Browsing"),
        ("ClaudeBot", "Anthropic Claude Crawler"),
        ("PerplexityBot", "Perplexity AI Answer Engine"),
        ("Google-Extended", "Google Gemini / AI Grounding Control"),
        ("Googlebot", "Google Search Primary Indexer"),
        ("Bingbot", "Microsoft Bing & Copilot Indexer")
    ]
    try:
        r = requests.get(robots_url, headers=headers, timeout=8)
        result["status_code"] = r.status_code
        if r.status_code == 200 and "<html" not in r.text.lower():
            result["exists"] = True
            txt = r.text
            result["raw_preview"] = txt[:2500]
            rules_by_agent = {}
            current_agents = []
            for raw_line in txt.splitlines():
                line = raw_line.split("#", 1)[0].strip()
                if not line or ":" not in line:
                    continue
                k, v = [x.strip() for x in line.split(":", 1)]
                kl = k.lower()
                if kl == "sitemap" and v:
                    result["sitemaps_declared"].append(v)
                elif kl == "user-agent":
                    agent_key = v.lower()
                    if current_agents and rules_by_agent.get(current_agents[-1]) is not None and len(rules_by_agent[current_agents[-1]]) > 0:
                        current_agents = [agent_key]
                    else:
                        current_agents.append(agent_key)
                    rules_by_agent.setdefault(agent_key, [])
                elif kl in ("disallow", "allow") and current_agents:
                    for ag in current_agents:
                        rules_by_agent.setdefault(ag, []).append((kl, v))

            wildcard_rules = rules_by_agent.get("*", [])
            for bot_name, bot_desc in ai_agents:
                specific = rules_by_agent.get(bot_name.lower())
                active_rules = specific if specific is not None else wildcard_rules
                disallows = [val for directive, val in active_rules if directive == "disallow" and val]
                allows = [val for directive, val in active_rules if directive == "allow" and val]
                if "/" in disallows and "/" not in allows:
                    status_lbl = "🔴 Blocked (Disallow: /)"
                elif disallows:
                    status_lbl = f"🟡 Restricted ({len(disallows)} path rule(s))"
                else:
                    status_lbl = "🟢 Allowed (Full Access)"
                result["ai_bots"].append({
                    "Crawler_Bot": bot_name,
                    "Platform_Role": bot_desc,
                    "Governance_Status": status_lbl,
                    "Rule_Source": f"Explicit ({bot_name})" if specific is not None else "Inherited (*)"
                })

            check_rules = (rules_by_agent.get("googlebot") or []) + wildcard_rules
            disallow_prefixes = [val for directive, val in check_rules if directive == "disallow" and val and "*" not in val]
            for tu in target_urls:
                path_u = urlparse(tu).path or "/"
                for pref in disallow_prefixes:
                    if path_u.startswith(pref):
                        result["blocked_target_urls"].append({"Blocked_URL": tu, "Matched_Rule": f"Disallow: {pref}"})
                        break
        else:
            for bot_name, bot_desc in ai_agents:
                result["ai_bots"].append({
                    "Crawler_Bot": bot_name,
                    "Platform_Role": bot_desc,
                    "Governance_Status": "🟢 Allowed (No robots.txt)",
                    "Rule_Source": "Default Open"
                })
    except Exception:
        pass
    return result

def verify_internal_links_health(df_crawl, headers, max_links=60):
    """Checks internal links extracted across crawled pages for 404/5xx broken targets and 301/302 redirect chains."""
    if df_crawl.empty or "Internal_Link_Targets_JSON" not in df_crawl.columns:
        return pd.DataFrame()
    link_sources = {}
    for _, r in df_crawl.iterrows():
        src = r.get("URL", "")
        try:
            items = json.loads(r.get("Internal_Link_Targets_JSON", "[]"))
            for it in items:
                t_u = it.get("url", "").strip()
                anc = it.get("anchor", "").strip() or "(Image / No Text)"
                if t_u.startswith("http") and t_u not in link_sources:
                    link_sources[t_u] = (src, anc)
        except Exception:
            continue

    known_200 = set(df_crawl[df_crawl["Status"] == 200]["URL"].tolist())
    candidates = [u for u in link_sources.keys() if u not in known_200][:max_links]

    def probe_url(u):
        src_u, anc_txt = link_sources[u]
        try:
            resp = requests.get(u, headers=headers, timeout=8, allow_redirects=True)
            code_st = resp.status_code
            hops = len(resp.history)
            if code_st >= 400:
                issue = f"🔴 Broken Link (HTTP {code_st})"
            elif hops >= 2:
                issue = f"⚠️ Redirect Chain ({hops} hops)"
            elif hops == 1:
                issue = f"🟡 301/302 Redirect -> {clean_display_url(resp.url)}"
            else:
                issue = "🟢 Healthy (200 OK)"
            return {
                "Status_Flag": issue,
                "HTTP_Code": code_st,
                "Target_URL": u,
                "Final_Destination": clean_display_url(resp.url),
                "Anchor_Text": anc_txt[:60],
                "Found_On_Page": src_u
            }
        except Exception:
            return {
                "Status_Flag": "🔴 Connection / Timeout Error",
                "HTTP_Code": 0,
                "Target_URL": u,
                "Final_Destination": "Unreachable",
                "Anchor_Text": anc_txt[:60],
                "Found_On_Page": src_u
            }

    if not candidates:
        return pd.DataFrame()
    with ThreadPoolExecutor(max_workers=12) as ex:
        probed = list(ex.map(probe_url, candidates))
    df_probed = pd.DataFrame(probed)
    df_probed["sort_pri"] = df_probed["Status_Flag"].apply(lambda x: 0 if "Broken" in x or "Error" in x else (1 if "Redirect" in x else 2))
    df_probed = df_probed.sort_values(by=["sort_pri", "HTTP_Code"]).drop(columns=["sort_pri"]).reset_index(drop=True)
    return df_probed

def analyze_url_structure_and_keywords(df_crawl):
    """
    Audits every crawled URL's folder hierarchy, slug hygiene, and keyword alignment against H1/Title:
    - Detects redundant/duplicate path segments (e.g. /blogs/blog/..., /products/product/..., /services/service/...)
    - Detects CMS wrapper bloat (/pages/..., /collections/all/products/...) and excessive depth (>3 levels)
    - Flags uppercase letters, underscores (_), date stamps (/2023/05/), dynamic parameters, and >90 char URLs
    - Compares URL slug tokens against H1/Meta Title to detect generic slugs (/page-1, /post-123) or missing core keywords
    - Generates an exact Recommended Clean URL, Recommended Target Keywords, and 301 Redirect migration guidance
    """
    if df_crawl is None or df_crawl.empty:
        return pd.DataFrame()

    stop_words = {
        "the", "and", "for", "with", "that", "this", "from", "your", "are", "was",
        "were", "will", "have", "has", "had", "what", "when", "where", "why", "how",
        "who", "which", "can", "could", "should", "would", "into", "about", "after",
        "before", "between", "under", "over", "again", "further", "then", "once",
        "here", "there", "all", "any", "both", "each", "few", "more", "most", "other",
        "some", "such", "only", "own", "same", "than", "too", "very", "just", "actually",
        "really", "says", "said", "complete", "guide", "overview", "home", "page"
    }

    def stem_segment(seg):
        s = seg.lower().strip()
        if s.endswith("ies") and len(s) > 4:
            return s[:-3] + "y"
        if s.endswith("es") and len(s) > 3 and not s.endswith(("ses", "xes", "zes", "ches", "shes")):
            return s[:-1]
        if s.endswith("s") and len(s) > 3 and not s.endswith("ss"):
            return s[:-1]
        return s

    rows = []
    for _, r in df_crawl.iterrows():
        raw_url = str(r.get("URL", "")).strip()
        if not raw_url:
            continue
        parsed = urlparse(raw_url)
        scheme_host = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        path_raw = parsed.path or "/"
        segments_raw = [p for p in path_raw.split("/") if p]
        depth = len(segments_raw)

        h1_txt = str(r.get("H1_Text", "")).strip()
        title_txt = str(r.get("Meta_Title", "")).strip()
        ref_heading = h1_txt if (h1_txt and h1_txt != "N/A") else (title_txt if (title_txt and title_txt != "Missing") else "")
        # Strip trailing "| Brand" or "- Brand" from Meta Title if used
        if ref_heading == title_txt and any(sep in ref_heading for sep in [" | ", " - ", " — "]):
            ref_heading = re.split(r"\s+[\|\-\—]\s+", ref_heading)[0].strip()

        # Extract meaningful topic keywords from H1 / Title
        heading_tokens = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", ref_heading) if len(w) >= 3 and w.lower() not in stop_words]
        # Deduplicate while preserving order
        seen_ht = set()
        core_topic_tokens = []
        for tk in heading_tokens:
            if tk not in seen_ht:
                seen_ht.add(tk)
                core_topic_tokens.append(tk)

        issues = []
        score = 100
        cleaned_segments = list(segments_raw)

        # 1. Redundant / Duplicate Path Segments (e.g. /blogs/blog/..., /products/product/...)
        if len(segments_raw) >= 2:
            deduped = []
            prev_stem = None
            dup_found_pairs = []
            all_stems_seen = {}
            for idx_s, seg in enumerate(segments_raw):
                st_s = stem_segment(seg)
                if prev_stem and st_s == prev_stem:
                    dup_found_pairs.append(f"/{segments_raw[idx_s-1]}/{seg}/")
                    # Keep the cleaner singular or first folder (e.g. prefer 'blog' or 'blogs' consistently)
                    continue
                if st_s in all_stems_seen and idx_s < len(segments_raw) - 1:
                    dup_found_pairs.append(f"repeated '{seg}'")
                    continue
                all_stems_seen[st_s] = idx_s
                deduped.append(seg)
                prev_stem = st_s

            if dup_found_pairs:
                issues.append(f"🔴 Redundant Folder Structure ({', '.join(dup_found_pairs)}) — collapses duplicate hierarchy")
                score -= 28
                cleaned_segments = deduped

        # 2. CMS Wrapper Prefix Bloat (e.g. /pages/about -> /about, /collections/all/products/x -> /products/x)
        if len(cleaned_segments) >= 2 and cleaned_segments[0].lower() == "pages":
            issues.append("🟡 CMS '/pages/' Wrapper Prefix — flattens URL hierarchy for cleaner authority")
            score -= 10
            cleaned_segments = cleaned_segments[1:]
        elif len(cleaned_segments) >= 4 and cleaned_segments[0].lower() == "collections" and cleaned_segments[2].lower() == "products":
            issues.append("🟡 Nested Shopify Collection Path (/collections/*/products/*) — canonicalize to /products/")
            score -= 15
            cleaned_segments = ["products", cleaned_segments[-1]]

        # 3. Excessive Folder Depth (> 3 levels)
        if depth >= 4:
            issues.append(f"⚠️ Deep Folder Nesting ({depth} levels) — keep key pages within 2–3 levels of root")
            score -= 12

        # 4. Uppercase Characters in Path
        if any(c.isupper() for c in path_raw):
            issues.append("🟡 Uppercase Characters in URL — enforce lowercase to prevent duplicate URL splits")
            score -= 10

        # 5. Underscores or Encoded Spaces
        if "_" in path_raw or "%20" in path_raw:
            issues.append("🟡 Underscores/Spaces in Slug — replace '_' with hyphens '-' for Google word parsing")
            score -= 12

        # 6. Date Stamps in Evergreen URLs (/2022/08/ or /2023/)
        if re.search(r"/(?:19|20)\d{2}/(?:0[1-9]|1[0-2])/", path_raw):
            issues.append("🟡 Date Stamp in URL Path — evergreen posts should omit year/month folders")
            score -= 10

        # 7. Query Parameters or Legacy File Extensions (.html, .php, .aspx)
        if parsed.query:
            issues.append(f"🟡 Query Parameters in URL (?{parsed.query[:25]}) — use clean static path")
            score -= 10
        if re.search(r"\.(?:html|htm|php|aspx|jsp)$", path_raw, re.I):
            issues.append("🟡 Legacy File Extension (.html/.php) — strip file extensions for clean URLs")
            score -= 8

        # 8. Slug Analysis (Generic Slugs, Conversational Filler, & Keyword Alignment)
        leaf_slug = cleaned_segments[-1] if cleaned_segments else ""
        leaf_slug_clean = re.sub(r"\.(?:html|htm|php|aspx|jsp)$", "", leaf_slug, flags=re.I)
        leaf_slug_clean = leaf_slug_clean.replace("_", "-").replace("%20", "-").lower()
        slug_words = [w for w in re.findall(r"[a-z0-9]+", leaf_slug_clean) if w]

        generic_slug_patterns = re.compile(r"^(?:page|post|article|product|service|item|untitled|home|test|new-page|copy-of)(?:-?\d+)?$", re.I)
        if leaf_slug_clean and generic_slug_patterns.match(leaf_slug_clean):
            issues.append(f"🔴 Generic Non-Descriptive Slug ('/{leaf_slug_clean}') — contains zero target keywords")
            score -= 25
            if core_topic_tokens:
                leaf_slug_clean = "-".join(core_topic_tokens[:5])
        elif leaf_slug_clean and core_topic_tokens:
            slug_set = set(slug_words)
            overlap = [t for t in core_topic_tokens[:4] if t in slug_set]
            if len(overlap) == 0 and len(core_topic_tokens) >= 2:
                issues.append("🟠 Slug-to-H1 Keyword Mismatch — URL slug lacks primary page topic keywords")
                score -= 16
                leaf_slug_clean = "-".join(core_topic_tokens[:5])
            elif len(slug_words) >= 7:
                # Trim conversational filler words while preserving core keyword phrase
                concise_words = [w for w in slug_words if w not in stop_words]
                if len(concise_words) >= 3 and len(concise_words) < len(slug_words):
                    issues.append(f"🟡 Long Conversational Slug ({len(slug_words)} words) — trim stop-words for higher SERP CTR")
                    score -= 8
                    leaf_slug_clean = "-".join(concise_words[:6])

        if len(raw_url) > 95:
            issues.append(f"🟡 Long URL ({len(raw_url)} chars) — keep under 75–90 chars to avoid SERP truncation")
            score -= 6

        # Assemble Recommended Clean URL
        if cleaned_segments:
            rec_segs = [re.sub(r"[^a-z0-9\-]+", "-", s.lower().replace("_", "-")).strip("-") for s in cleaned_segments[:-1]]
            rec_leaf = re.sub(r"[^a-z0-9\-]+", "-", leaf_slug_clean).strip("-")
            rec_path = "/" + "/".join([s for s in rec_segs + [rec_leaf] if s])
        else:
            rec_path = "/"
        recommended_url = f"{scheme_host}{rec_path}" if scheme_host else rec_path

        # Generate Keyword Recommendations for this page
        if core_topic_tokens:
            primary_kw = " ".join(core_topic_tokens[:4])
            secondary_kws = []
            if len(core_topic_tokens) >= 3:
                secondary_kws.append(" ".join(core_topic_tokens[:3]))
            if any(b in path_raw.lower() for b in ["/blog", "/article", "/news", "/guide"]):
                secondary_kws.append(f"{primary_kw} guide")
                secondary_kws.append(f"is {primary_kw} worth it" if "worth" in ref_heading.lower() else f"how {primary_kw} works")
            else:
                secondary_kws.append(f"{primary_kw} services")
                secondary_kws.append(f"best {primary_kw}")
            kw_recs_str = f"Primary: {primary_kw} | Secondary: {', '.join(list(dict.fromkeys(secondary_kws))[:2])}"
        else:
            primary_kw = "Homepage / Brand Root" if depth == 0 else " ".join(slug_words[:4]) or "General Page"
            kw_recs_str = f"Primary: {primary_kw}"

        is_changed = (recommended_url.rstrip("/") != raw_url.split("?")[0].rstrip("/"))
        score = max(15, min(100, score))

        rows.append({
            "Current_URL": raw_url,
            "URL_Health_Score": score,
            "Folder_Depth": depth,
            "Structural_Issues": " | ".join(issues) if issues else "🟢 Clean & SEO-Friendly URL",
            "Detected_Page_Topic": primary_kw,
            "Recommended_Clean_URL": recommended_url if is_changed else "✓ Keep Current URL (Optimal)",
            "Recommended_Target_Keywords": kw_recs_str,
            "Migration_Action": "301 Redirect Old URL -> New URL + Update Internal Links & Canonical" if is_changed else "No Change Required"
        })

    df_u = pd.DataFrame(rows)
    if not df_u.empty:
        df_u = df_u.sort_values(by=["URL_Health_Score", "Folder_Depth"], ascending=[True, False]).reset_index(drop=True)
    return df_u

def build_master_audit_excel(
    df_c, df_r, df_a, df_eq, df_can, df_ads, df_broken, brand_name, scope_label,
    df_serp_ads=None, df_brand_serp=None, df_cwv_batch=None, crawly_data=None,
    df_mentions=None, robots_data=None, first_party_summary=None, df_url_audit=None
):
    """Generates a comprehensive multi-tab styled Excel workbook (.xlsx) integrating every audit module."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()
    ws_sum = wb.active
    ws_sum.title = "Executive_Summary"

    header_fill = PatternFill(start_color="004B93", end_color="004B93", fill_type="solid")
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    title_font = Font(name="Calibri", size=14, bold=True, color="004B93")
    reg_font = Font(name="Calibri", size=10, color="1E293B")
    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1")
    )

    def style_worksheet(ws, df_data, title_banner=None):
        start_row = 1
        if title_banner:
            ws.cell(row=1, column=1, value=title_banner).font = title_font
            start_row = 3
        if df_data is None or df_data.empty:
            ws.cell(row=start_row, column=1, value="No data recorded for this module during the current session.").font = reg_font
            return
        clean_df = df_data.copy()
        for col_c in clean_df.columns:
            clean_df[col_c] = clean_df[col_c].apply(lambda v: str(v) if isinstance(v, (list, dict)) else v)

        for c_idx, col_name in enumerate(clean_df.columns, 1):
            cell = ws.cell(row=start_row, column=c_idx, value=str(col_name))
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            cell.border = thin_border

        for r_idx, row_vals in enumerate(clean_df.itertuples(index=False), start_row + 1):
            for c_idx, val in enumerate(row_vals, 1):
                cell = ws.cell(row=r_idx, column=c_idx, value=val)
                cell.font = reg_font
                cell.border = thin_border
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if r_idx % 2 == 0:
                    cell.fill = zebra_fill

        ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
        for col in ws.columns:
            max_len = 12
            col_letter = col[0].column_letter
            for cell in col:
                if cell.row >= start_row and cell.value is not None:
                    val_len = min(55, len(str(cell.value)))
                    if val_len > max_len:
                        max_len = val_len
            ws.column_dimensions[col_letter].width = max_len + 4

    tot_p = len(df_c) if not df_c.empty else 0
    schema_pct = round(df_c["Commercial_Schema"].sum() / max(1, tot_p) * 100, 1) if not df_c.empty else 0.0
    thin_cnt = int((df_c["Word_Count"] < 300).sum()) if not df_c.empty else 0
    miss_alt = int(df_c["Missing_Alt_Count"].sum()) if (not df_c.empty and "Missing_Alt_Count" in df_c.columns) else 0
    avg_a11y = round(df_c["A11y_Score"].mean(), 1) if (not df_c.empty and "A11y_Score" in df_c.columns) else 0.0

    summary_rows = [
        {"Audit Dimension": "Client / Brand", "Measured Value": brand_name, "Notes": scope_label},
        {"Audit Dimension": "Audit Date", "Measured Value": datetime.now().strftime("%Y-%m-%d"), "Notes": "Generated by Connext Enterprise Digital & AEO Suite"},
        {"Audit Dimension": "Total HTML Pages Audited", "Measured Value": tot_p, "Notes": "Full page crawl inventory"},
        {"Audit Dimension": "Rich Commercial / FAQ Schema Coverage", "Measured Value": f"{schema_pct}%", "Notes": "Target: 100% across commercial & informational pages"},
        {"Audit Dimension": "Thin Content Pages (<300 Words)", "Measured Value": thin_cnt, "Notes": "Pages requiring content expansion"},
        {"Audit Dimension": "Total Images Missing Alt Text", "Measured Value": miss_alt, "Notes": "WCAG 2.1 AA & Image SEO compliance"},
        {"Audit Dimension": "Average Accessibility (WCAG) Score", "Measured Value": f"{avg_a11y}/100", "Notes": "Target: > 90/100"}
    ]
    if crawly_data and crawly_data.get("domain_authority"):
        summary_rows.append({"Audit Dimension": "Domain Authority (Crawly)", "Measured Value": f"{crawly_data.get('domain_authority')}/100", "Notes": f"{crawly_data.get('referring_domains', 0):,} Referring Domains | {crawly_data.get('total_backlinks', 0):,} Backlinks"})
    if first_party_summary:
        for fp in first_party_summary:
            summary_rows.append({"Audit Dimension": f"First-Party ({fp.get('Source')})", "Measured Value": fp.get("Metric_1", ""), "Notes": f"{fp.get('Metric_2', '')} | {fp.get('Metric_3', '')}"})

    style_worksheet(ws_sum, pd.DataFrame(summary_rows), title_banner=f"Connext Global Solutions — Master Digital Audit ({brand_name})")

    if not df_c.empty:
        drop_internal = ["Body_Text", "Tech_Tools_JSON", "Tech_Weaknesses_JSON", "Internal_Link_Targets_JSON"]
        export_crawl_df = df_c.drop(columns=[c for c in drop_internal if c in df_c.columns], errors="ignore")
        ws_tech = wb.create_sheet(title="Technical_and_SEO")
        style_worksheet(ws_tech, export_crawl_df, title_banner="Complete On-Page SEO, Metadata, Headings, Schemas & Tech Stack")

        # Aggregate Installed Tech Stack & Weaknesses tab
        tools_agg = {}
        weak_agg = {}
        if "Tech_Tools_JSON" in df_c.columns:
            for _, rt in df_c.iterrows():
                try:
                    for it in json.loads(rt.get("Tech_Tools_JSON", "[]")):
                        tn = it["Tool"]
                        if tn not in tools_agg:
                            tools_agg[tn] = {**it, "Pages_Detected": 1}
                        else:
                            tools_agg[tn]["Pages_Detected"] += 1
                except Exception:
                    pass
                try:
                    for iw in json.loads(rt.get("Tech_Weaknesses_JSON", "[]")):
                        weak_agg[iw["Finding"]] = iw
                except Exception:
                    pass
        if tools_agg:
            ws_stack = wb.create_sheet(title="Installed_Tech_Stack")
            style_worksheet(ws_stack, pd.DataFrame(list(tools_agg.values())), title_banner="Detected Technologies, Pixels & Marketing Apps (Wappalyzer)")
        if weak_agg:
            ws_st_w = wb.create_sheet(title="Tech_Stack_Weaknesses")
            style_worksheet(ws_st_w, pd.DataFrame(list(weak_agg.values())), title_banner="Tech Stack Weaknesses & Optimization Opportunities")

    eff_url_df = df_url_audit if (df_url_audit is not None and not df_url_audit.empty) else analyze_url_structure_and_keywords(df_c)
    if eff_url_df is not None and not eff_url_df.empty:
        ws_url = wb.create_sheet(title="URL_Structure_Audit")
        style_worksheet(ws_url, eff_url_df, title_banner="URL Structure, Folder Hygiene & Keyword Alignment Audit")

    if robots_data and robots_data.get("ai_bots"):
        ws_rob = wb.create_sheet(title="Robots_AI_Bots")
        style_worksheet(ws_rob, pd.DataFrame(robots_data["ai_bots"]), title_banner="Robots.txt & AI Search Bot Governance")

    if df_broken is not None and not df_broken.empty:
        ws_brk = wb.create_sheet(title="Links_and_Redirects")
        style_worksheet(ws_brk, df_broken, title_banner="Internal Link Health & Redirect Diagnostics")

    if df_eq is not None and not df_eq.empty:
        ws_eq = wb.create_sheet(title="Internal_Link_Equity")
        style_worksheet(ws_eq, df_eq, title_banner="Internal Link Equity & Content Silo Flow")

    if df_can is not None and not df_can.empty:
        ws_can = wb.create_sheet(title="Duplicate_Cannibalization")
        style_worksheet(ws_can, df_can, title_banner="Content Overlap & Keyword Cannibalization Pairs")

    if df_brand_serp is not None and not df_brand_serp.empty:
        ws_br = wb.create_sheet(title="Brand_SERP_Top_Pages")
        style_worksheet(ws_br, df_brand_serp, title_banner="Branded Search Results & Top Valuable Sitelink Pages")

    if df_r is not None and not df_r.empty:
        ws_serp = wb.create_sheet(title="Organic_SERP_Rankings")
        style_worksheet(ws_serp, df_r.drop(columns=["Top_10_JSON"], errors="ignore"), title_banner="Live Google Organic Search Rankings & Page-1 Leaders")

    if df_a is not None and not df_a.empty:
        ws_aeo = wb.create_sheet(title="Generative_AEO_Citations")
        style_worksheet(ws_aeo, df_a, title_banner="Google AI Overviews & Generative Engine Citations")

    if df_ads is not None and not df_ads.empty:
        ws_ads = wb.create_sheet(title="Paid_Ads_Portfolio")
        style_worksheet(ws_ads, df_ads.drop(columns=["Format", "Type"], errors="ignore"), title_banner="Google Ads Transparency Center Portfolio & Longevity")

    if df_serp_ads is not None and not df_serp_ads.empty:
        ws_sads = wb.create_sheet(title="Live_Competitor_Search_Ads")
        style_worksheet(ws_sads, df_serp_ads, title_banner="Live Google Search Paid SERP & Competitor Conquesting")

    if df_cwv_batch is not None and not df_cwv_batch.empty:
        ws_cwv = wb.create_sheet(title="Speed_Core_Web_Vitals")
        style_worksheet(ws_cwv, df_cwv_batch, title_banner="Mobile Core Web Vitals Multi-Template Benchmark")

    if crawly_data and crawly_data.get("backlinks"):
        ws_bl = wb.create_sheet(title="Inbound_Backlinks")
        style_worksheet(ws_bl, pd.DataFrame(crawly_data["backlinks"]), title_banner="Inbound Backlink Profile (Crawly Index)")

    if df_mentions is not None and not df_mentions.empty:
        ws_mn = wb.create_sheet(title="Unlinked_Brand_Mentions")
        style_worksheet(ws_mn, df_mentions, title_banner="Open-Web Unlinked Brand Mentions (Digital PR Opportunities)")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

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

def esc(val):
    """Safely escapes dynamic strings for HTML/WeasyPrint rendering."""
    return html.escape(str(val)) if val is not None else ""

def clean_display_url(raw_url):
    """Strips noisy tracking parameters (?srsltid=, ?utm_...) for clean tables and PDFs."""
    if not raw_url or str(raw_url).strip() in ("N/A", "None", "Error", ""):
        return "—"
    s = str(raw_url).strip()
    if not s.startswith("http"):
        return s
    try:
        p = urlparse(s)
        qs = parse_qs(p.query, keep_blank_values=True)
        clean_qs = {k: v for k, v in qs.items() if not k.lower().startswith(("srsltid", "utm_", "gclid", "fbclid", "msclkid"))}
        new_q = urlencode(clean_qs, doseq=True)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, new_q, ""))
    except Exception:
        return s

def detect_audit_scope_label(df_c, target_domain, custom_label=""):
    """Infers a clean, human-readable scope title for the audited batch of pages."""
    if custom_label and custom_label.strip():
        return custom_label.strip()
    if df_c.empty:
        return "Website Digital & Search Audit"
    urls = df_c["URL"].dropna().tolist()
    n = len(urls)
    if n == 1:
        path = urlparse(urls[0]).path.strip("/")
        return f"Focused Page Audit ({'/' + path if path else 'Homepage'})"
    paths = [urlparse(u).path.strip("/") for u in urls if urlparse(u).path.strip("/")]
    if paths:
        top_folders = [p.split("/")[0] for p in paths]
        most_common_folder, count = Counter(top_folders).most_common(1)[0]
        if count >= max(2, int(n * 0.65)):
            folder_map = {
                "products": "Product & Pricing Pages (/products/)",
                "pages": "Core Informational & Service Pages (/pages/)",
                "blogs": "Blog & Educational Articles (/blogs/)",
                "blog": "Blog & Educational Articles (/blog/)",
                "collections": "Category & Collection Pages (/collections/)",
                "solutions": "Solution & Service Pages (/solutions/)",
                "services": "Service Offering Pages (/services/)",
                "case-studies": "Case Studies & Client Stories (/case-studies/)"
            }
            friendly = folder_map.get(most_common_folder.lower(), f"Section Pages (/{most_common_folder}/)")
            return f"{friendly} — {n} Pages"
    return f"Website Audit ({n} Pages Evaluated)"

def get_safe_logo_src(logo_url):
    """Caches logo as base64 data URI with inline SVG fallback so WeasyPrint never fails on network timeouts."""
    if "cached_logo_b64" in st.session_state and st.session_state["cached_logo_b64"]:
        return st.session_state["cached_logo_b64"]
    fallback_svg = (
        "data:image/svg+xml;utf8,"
        "<svg xmlns='http://www.w3.org/2000/svg' width='140' height='36' viewBox='0 0 140 36'>"
        "<text x='0' y='26' font-family='DejaVu Sans, Arial, sans-serif' font-size='24' font-weight='bold' fill='%23004b93'>connext</text>"
        "<path d='M2 8 Q 35 0, 72 8' stroke='%23e85122' stroke-width='2.5' fill='none'/>"
        "</svg>"
    )
    try:
        r = requests.get(logo_url, timeout=5)
        if r.status_code == 200 and r.content and "image" in r.headers.get("Content-Type", "").lower():
            mime = r.headers.get("Content-Type", "image/png").split(";")[0].strip()
            b64 = base64.b64encode(r.content).decode("ascii")
            data_uri = f"data:{mime};base64,{b64}"
            st.session_state["cached_logo_b64"] = data_uri
            return data_uri
    except Exception:
        pass
    return fallback_svg

def fetch_serpapi_with_retry(params, max_retries=2, timeout=28):
    """Queries SerpApi with automatic retry to eliminate read timeout errors."""
    for attempt in range(max_retries + 1):
        try:
            r = requests.get("https://serpapi.com/search", params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json(), None
        except Exception as e:
            if attempt == max_retries:
                return None, str(e)
    return None, "Timeout"

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
            "kg_info", "nlp_categories", "prev_snapshot", "is_thin_site", "target_domain", "crawly_data", "df_mentions", "discovered_sitemaps", "df_url_audit", "url_ai_recommendations", "compiled_summary_pdf_bytes", "compiled_dossier_pdf_bytes", "df_serp_ads", "ads_ai_strategy", "cached_logo_b64", "df_brand_serp", "brand_sitelinks", "brand_kg_panel", "cwv_tested_url", "cwv_desktop_metrics", "df_cwv_batch", "robots_audit_data", "df_broken_links", "master_xlsx_bytes", "first_party_summary", "df_gsc_quickwins", "df_ga4_top_pages"
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
    whole_website_mode = st.checkbox(
        "🌐 Whole Website (All Sitemap Pages)",
        value=False,
        help="Automatically unpacks all XML sitemaps and audits every discovered page across the entire domain (up to 2,500 pages)."
    )
    if whole_website_mode:
        crawl_limit = 2500
        st.info("🌐 **Whole Website Mode Active:** All discovered XML sitemaps and internal pages will be audited.")
    else:
        crawl_limit = st.slider("Max Landing Pages to Crawl", min_value=5, max_value=500, value=50, step=5)
    st.session_state["whole_website_mode"] = whole_website_mode
    st.caption("Multi-threaded engine analyzes DOM, schemas, metadata & security.")

# Initialize Session States
if "df_crawl" not in st.session_state: st.session_state["df_crawl"] = pd.DataFrame()
if "df_rankings" not in st.session_state: st.session_state["df_rankings"] = pd.DataFrame()
if "df_aeo" not in st.session_state: st.session_state["df_aeo"] = pd.DataFrame()
if "df_ads" not in st.session_state: st.session_state["df_ads"] = pd.DataFrame()
if "df_serp_ads" not in st.session_state: st.session_state["df_serp_ads"] = pd.DataFrame()
if "ads_ai_strategy" not in st.session_state: st.session_state["ads_ai_strategy"] = ""
if "df_brand_serp" not in st.session_state: st.session_state["df_brand_serp"] = pd.DataFrame()
if "brand_sitelinks" not in st.session_state: st.session_state["brand_sitelinks"] = []
if "brand_kg_panel" not in st.session_state: st.session_state["brand_kg_panel"] = {}
if "cwv_tested_url" not in st.session_state: st.session_state["cwv_tested_url"] = ""
if "cwv_desktop_metrics" not in st.session_state: st.session_state["cwv_desktop_metrics"] = {}
if "df_cwv_batch" not in st.session_state: st.session_state["df_cwv_batch"] = pd.DataFrame()
if "robots_audit_data" not in st.session_state: st.session_state["robots_audit_data"] = {}
if "df_broken_links" not in st.session_state: st.session_state["df_broken_links"] = pd.DataFrame()
if "df_url_audit" not in st.session_state: st.session_state["df_url_audit"] = pd.DataFrame()
if "url_ai_recommendations" not in st.session_state: st.session_state["url_ai_recommendations"] = ""
if "first_party_summary" not in st.session_state: st.session_state["first_party_summary"] = []
if "df_gsc_quickwins" not in st.session_state: st.session_state["df_gsc_quickwins"] = pd.DataFrame()
if "df_ga4_top_pages" not in st.session_state: st.session_state["df_ga4_top_pages"] = pd.DataFrame()
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

            # E. Recursive XML Sitemap Index, Category Unpacker & Internal Spider
            if crawl_mode == "🎯 Specific / Unique URLs (Direct Audit)":
                custom_urls = [u.strip() for u in specific_urls_input.split("\n") if u.strip().startswith("http")]
                url_pool = custom_urls if custom_urls else [target_site]
                st.session_state["discovered_sitemaps"] = [{"Sitemap_URL": "Direct URL Input Mode", "Type": "Manual List", "Child_Sitemaps_Found": 0, "HTML_Pages_Extracted": len(url_pool)}]
            else:
                url_pool, sm_log = discover_target_urls(root, target_site, slug_filter, crawl_limit, HEADERS, whole_website=whole_website_mode)
                st.session_state["discovered_sitemaps"] = sm_log

            robots_audit = audit_robots_and_ai_bots(root, url_pool, HEADERS)
            st.session_state["has_robots"] = robots_audit.get("exists", False)
            st.session_state["robots_audit_data"] = robots_audit

            # F. Complete On-Page SEO, Metadata, Content Depth, Readability, Schema & WCAG Auditor
            def audit_page(url):
                res = {
                    "URL": url,
                    "Status": 0,
                    "Response_Time_ms": 0,
                    "Redirect_Hops": 0,
                    "Redirect_Chain": "Direct (200)",
                    "Indexability_Status": "Indexable",
                    "URL_Depth": len([p for p in urlparse(url).path.split("/") if p]),
                    "Internal_Link_Targets_JSON": "[]",
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
                    "H4_H6_Count": 0,
                    "Heading_Audit_Status": "Missing H1",
                    "Missing_Alt_Files": "None",
                    "Detected_Tech_Stack": "None",
                    "Tech_Tools_JSON": "[]",
                    "Tech_Weaknesses_JSON": "[]",
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
                    res["Response_Time_ms"] = round(r.elapsed.total_seconds() * 1000)
                    hops = len(r.history)
                    res["Redirect_Hops"] = hops
                    if hops > 0:
                        chain_codes = " -> ".join([str(h.status_code) for h in r.history] + [str(r.status_code)])
                        res["Redirect_Chain"] = f"{chain_codes} ({clean_display_url(r.url)})"
                    x_rob = r.headers.get("X-Robots-Tag", "").lower()
                    if "noindex" in x_rob:
                        res["Indexability_Status"] = "🔴 Blocked by X-Robots-Tag (noindex)"
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
                        h4_6 = [h.get_text(separator=" ", strip=True) for h in soup.find_all(["h4", "h5", "h6"]) if h.get_text(strip=True)]
                        res["H2_Count"] = len(h2s)
                        res["H2_Texts"] = " | ".join(h2s[:6]) if h2s else "None"
                        res["H3_Count"] = len(h3s)
                        res["H3_Texts"] = " | ".join(h3s[:6]) if h3s else "None"
                        res["H4_H6_Count"] = len(h4_6)
                        if len(h1s) > 0 and len(h3s) > 0 and len(h2s) == 0:
                            res["Skipped_Headings"] = True

                        if len(h1s) == 0:
                            res["Heading_Audit_Status"] = "🔴 Missing H1"
                        elif len(h1s) > 1:
                            res["Heading_Audit_Status"] = f"⚠️ Multiple H1s ({len(h1s)})"
                        elif res["Concatenated_H1"]:
                            res["Heading_Audit_Status"] = "⚠️ Merged Words in H1"
                        elif res["Skipped_Headings"]:
                            res["Heading_Audit_Status"] = "⚠️ Skipped H2 Level"
                        elif len(h2s) == 0:
                            res["Heading_Audit_Status"] = "🟡 No H2 Subheadings"
                        else:
                            res["Heading_Audit_Status"] = "🟢 Valid (H1→H2→H3)"

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
                            missing_alt_imgs = [img for img in imgs if not img.get("alt") or len(img.get("alt").strip()) == 0]
                            missing_alt = len(missing_alt_imgs)
                            res["Missing_Alt_Count"] = missing_alt
                            res["Missing_Alt_Pct"] = round((missing_alt / len(imgs)) * 100, 1)
                            bad_srcs = []
                            for m_img in missing_alt_imgs[:5]:
                                s_attr = m_img.get("src") or m_img.get("data-src") or ""
                                if s_attr:
                                    bad_srcs.append(s_attr.split("?")[0].split("/")[-1][:35])
                            res["Missing_Alt_Files"] = ", ".join(bad_srcs) if bad_srcs else "Decorative / Inline SVG"
                        
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
                        int_link_targets = []
                        seen_int_targets = set()
                        page_netloc = urlparse(url).netloc.replace("www.", "")
                        for a in soup.find_all("a", href=True):
                            raw_txt = a.get_text(strip=True)
                            txt = raw_txt.lower()
                            if txt in vague_terms:
                                vague_count += 1
                            href_val = a["href"].strip()
                            if href_val and not href_val.startswith(("#", "javascript:", "mailto:", "tel:")):
                                full_a = urljoin(url, href_val).split("#")[0]
                                if page_netloc in urlparse(full_a).netloc.replace("www.", ""):
                                    int_links += 1
                                    if full_a not in seen_int_targets and len(int_link_targets) < 35:
                                        seen_int_targets.add(full_a)
                                        int_link_targets.append({"url": full_a, "anchor": raw_txt[:60]})
                                else:
                                    ext_links += 1
                        res["Vague_Links_Count"] = vague_count
                        res["Internal_Links_Count"] = int_links
                        res["External_Links_Count"] = ext_links
                        res["Internal_Link_Targets_JSON"] = json.dumps(int_link_targets)
                        
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

                        # 7. Structured Data Schemas (JSON-LD, Microdata & RDFa Recursive Audit)
                        s_cnt, u_types, has_rich, has_aeo, s_tier, s_plain = extract_all_schemas_from_soup(soup)
                        res["Total_Schemas"] = s_cnt
                        res["Commercial_Schema"] = bool(has_rich or has_aeo)
                        res["Schema_Types"] = ", ".join(u_types) if u_types else "None"
                        res["Schema_Tier"] = s_tier
                        res["Schema_Plain_English"] = s_plain

                        # 7.5 Wappalyzer-Style Tech Stack & App Weakness Detection
                        t_tools, t_weaknesses = detect_tech_stack_and_weaknesses(r.text, r.headers, soup)
                        res["Detected_Tech_Stack"] = ", ".join([t["Tool"] for t in t_tools]) if t_tools else "None Detected"
                        res["Tech_Tools_JSON"] = json.dumps(t_tools)
                        res["Tech_Weaknesses_JSON"] = json.dumps(t_weaknesses)
                        
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

            total_to_crawl = len(url_pool)
            crawl_prog = st.progress(0, text=f"Crawling and auditing 0 of {total_to_crawl} pages...")
            records_map = {}
            with ThreadPoolExecutor(max_workers=12) as ex:
                future_to_idx = {ex.submit(audit_page, u): i for i, u in enumerate(url_pool)}
                completed_cnt = 0
                for fut in as_completed(future_to_idx):
                    idx_u = future_to_idx[fut]
                    records_map[idx_u] = fut.result()
                    completed_cnt += 1
                    if completed_cnt % max(1, total_to_crawl // 25) == 0 or completed_cnt == total_to_crawl:
                        crawl_prog.progress(
                            completed_cnt / max(1, total_to_crawl),
                            text=f"Crawling and auditing {completed_cnt} of {total_to_crawl} pages..."
                        )
            crawl_prog.empty()
            records = [records_map[i] for i in range(total_to_crawl)]

            df_crawl = pd.DataFrame(records)
            st.session_state["df_crawl"] = df_crawl
            st.session_state["is_thin_site"] = (len(df_crawl) <= 2)
            st.session_state["df_url_audit"] = analyze_url_structure_and_keywords(df_crawl)

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

        xray_tab1, xray_tab2, xray_tab3, xray_tab4, xray_tab5, xray_tab6, xray_tab7 = st.tabs([
            "🏷️ 1. Meta Title & Meta Description Matrix",
            "📑 2. H1–H6 Headings & Image Alt Text Audit",
            "🔎 3. Single-Page Deep X-Ray & AI Copywriter",
            "🛠️ 4. Installed Apps & Tech Stack (Wappalyzer)",
            "🔗 5. URL Structure & Keyword Audit",
            "🤖 6. Robots.txt, AI Bots & Broken Links",
            "🗺️ 7. Unpacked XML Sitemaps Log"
        ])

        with xray_tab1:
            st.markdown("##### Complete On-Page Meta Title, Meta Description, Canonical & Indexation Matrix")
            m_title_ok_cnt = int((display_df["Meta_Title_Status"] == "Optimal (30-60)").sum())
            m_desc_ok_cnt = int((display_df["Meta_Desc_Status"] == "Good (120-165)").sum())
            mt1, mt2, mt3 = st.columns(3)
            mt1.metric("Optimal Meta Titles (30–60 chars)", f"{m_title_ok_cnt}/{len(display_df)}")
            mt2.metric("Optimal Meta Descriptions (120–165 chars)", f"{m_desc_ok_cnt}/{len(display_df)}")
            mt3.metric("OpenGraph Social Tags Active", f"{int(display_df['Has_OG_Tags'].sum())}/{len(display_df)}")
            meta_cols = [
                "URL", "Meta_Title", "Meta_Title_Length", "Meta_Title_Status",
                "Meta_Desc", "Meta_Desc_Length", "Meta_Desc_Status",
                "Canonical_URL", "Canonical_Status", "Robots_Meta", "Has_OG_Tags"
            ]
            st.dataframe(display_df[meta_cols], hide_index=True, use_container_width=True)

        with xray_tab2:
            st.markdown("##### Complete Heading Hierarchy (H1–H6) & Image Alt Text Audit")
            h1_clean_cnt = int((display_df["H1_Count"] == 1).sum())
            total_imgs_all = int(display_df["Images_Count"].sum()) if "Images_Count" in display_df.columns else 0
            missing_alt_all = int(display_df["Missing_Alt_Count"].sum()) if "Missing_Alt_Count" in display_df.columns else 0
            ht1, ht2, ht3, ht4 = st.columns(4)
            ht1.metric("Pages with Single Clean H1", f"{h1_clean_cnt}/{len(display_df)}")
            ht2.metric("Total Images Audited", f"{total_imgs_all:,}")
            ht3.metric("Images Missing Alt Text", f"{missing_alt_all:,}", delta="Needs Alt Tags" if missing_alt_all > 0 else "100% Tagged", delta_color="inverse" if missing_alt_all > 0 else "normal")
            ht4.metric("Avg. Readability (Flesch)", f"{display_df['Readability_Score'].mean():.1f}/100")

            h_alt_cols = [
                "URL", "Heading_Audit_Status", "H1_Text", "H1_Count", "H2_Count", "H2_Texts",
                "H3_Count", "H3_Texts", "Images_Count", "Missing_Alt_Count", "Missing_Alt_Pct",
                "Missing_Alt_Files", "Word_Count", "AEO_Readiness_Score"
            ]
            avail_h_cols = [c for c in h_alt_cols if c in display_df.columns]
            st.dataframe(display_df[avail_h_cols], hide_index=True, use_container_width=True)

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
            st.markdown("##### 🛠️ Installed Apps, Marketing Pixels & Tech Stack (Wappalyzer-Style Detector)")
            st.caption("Automatically fingerprints CMS platforms, JS frameworks, tag managers, analytics, heatmaps, chat widgets, ad pixels, and CDNs across crawled pages, and flags stack weak points.")
            all_tools_map = {}
            all_weaknesses_map = {}
            if "Tech_Tools_JSON" in display_df.columns:
                for _, r_t in display_df.iterrows():
                    try:
                        for item_t in json.loads(r_t.get("Tech_Tools_JSON", "[]")):
                            t_name = item_t["Tool"]
                            if t_name not in all_tools_map:
                                all_tools_map[t_name] = {**item_t, "Pages_Detected": 1}
                            else:
                                all_tools_map[t_name]["Pages_Detected"] += 1
                    except Exception:
                        pass
                    try:
                        for item_w in json.loads(r_t.get("Tech_Weaknesses_JSON", "[]")):
                            w_key = item_w["Finding"]
                            if w_key not in all_weaknesses_map:
                                all_weaknesses_map[w_key] = item_w
                    except Exception:
                        pass

            if all_tools_map:
                df_tools_agg = pd.DataFrame(list(all_tools_map.values())).sort_values(by=["Pages_Detected", "Category"], ascending=[False, True])
                st.dataframe(df_tools_agg, hide_index=True, use_container_width=True)
            else:
                st.info("No third-party marketing apps or CMS fingerprints detected on this sample.")

            if all_weaknesses_map:
                st.markdown("##### 🚨 Tech Stack Weaknesses & Optimization Opportunities")
                st.dataframe(pd.DataFrame(list(all_weaknesses_map.values())), hide_index=True, use_container_width=True)

        with xray_tab5:
            st.markdown("##### 🔗 URL Structure, Folder Hierarchy & Keyword Alignment Audit")
            st.markdown("""
            <div class="guide-box">
                <strong>🎯 What This Audits:</strong> Detects redundant/duplicate path folders (such as <code>/blogs/blog/...</code> or <code>/products/product/...</code>), CMS wrapper bloat (<code>/pages/...</code>), deep nesting (&gt;3 levels), uppercase/underscore slugs, and mismatches between a page's H1/Title topic and its URL slug—generating clean target URLs, keyword recommendations, and 301 redirect rules.
            </div>
            """, unsafe_allow_html=True)

            df_url_aud = st.session_state.get("df_url_audit", pd.DataFrame())
            if df_url_aud.empty and not df.empty:
                df_url_aud = analyze_url_structure_and_keywords(df)
                st.session_state["df_url_audit"] = df_url_aud

            if not df_url_aud.empty:
                # Filter to match active search bar if used
                disp_url_aud = df_url_aud[df_url_aud["Current_URL"].isin(display_df["URL"])] if not display_df.empty else df_url_aud
                avg_u_score = int(disp_url_aud["URL_Health_Score"].mean()) if not disp_url_aud.empty else 100
                flagged_u_cnt = int((disp_url_aud["Migration_Action"] != "No Change Required").sum())
                dup_folder_cnt = int(disp_url_aud["Structural_Issues"].str.contains("Redundant Folder", na=False).sum())

                uk1, uk2, uk3, uk4 = st.columns(4)
                uk1.metric("Avg. URL Structure Score", f"{avg_u_score}/100")
                uk2.metric("URLs Needing Optimization", f"{flagged_u_cnt}/{len(disp_url_aud)}", delta="301 Redirects Needed" if flagged_u_cnt > 0 else "All Clean", delta_color="inverse" if flagged_u_cnt > 0 else "normal")
                uk3.metric("Redundant Folder Paths", f"{dup_folder_cnt} URLs", help="e.g. /blogs/blog/... or /products/product/...")
                uk4.metric("Avg. Folder Depth", f"{disp_url_aud['Folder_Depth'].mean():.1f} levels")

                st.dataframe(
                    disp_url_aud,
                    column_config={
                        "Current_URL": st.column_config.LinkColumn("Current Audited URL"),
                        "URL_Health_Score": st.column_config.ProgressColumn("URL Score", format="%d/100", min_value=0, max_value=100),
                        "Folder_Depth": st.column_config.NumberColumn("Depth", format="%d"),
                        "Structural_Issues": st.column_config.TextColumn("Detected URL & Slug Findings"),
                        "Detected_Page_Topic": st.column_config.TextColumn("Core Page Topic"),
                        "Recommended_Clean_URL": st.column_config.TextColumn("Recommended Clean URL"),
                        "Recommended_Target_Keywords": st.column_config.TextColumn("Recommended Target Keywords"),
                        "Migration_Action": st.column_config.TextColumn("SEO Migration Rule")
                    },
                    hide_index=True,
                    use_container_width=True
                )

                st.warning("⚠️ **Important SEO Migration Rule:** Never change live indexed URLs without deploying **301 Permanent Redirects** from the old URL to the new URL, updating internal links, and updating `<link rel='canonical'>` tags so existing rankings and backlinks transfer cleanly.")

                col_u1, col_u2 = st.columns(2)
                with col_u1:
                    st.download_button(
                        label="⬇️ Download URL Structure & 301 Redirect Map (CSV)",
                        data=disp_url_aud.to_csv(index=False),
                        file_name=f"url_structure_audit_{urlparse(st.session_state.get('target_domain', 'site')).netloc}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                with col_u2:
                    if st.button("🤖 Generate AI URL & Keyword Architecture Plan (Gemini)", key="btn_url_ai_plan", use_container_width=True):
                        if not gemini_key:
                            st.error("Please add your Gemini API Key in the sidebar.")
                        else:
                            with st.spinner("Analyzing URL hierarchy, slugs, and keyword clusters with Gemini..."):
                                sample_u_rows = disp_url_aud[["Current_URL", "Structural_Issues", "Detected_Page_Topic", "Recommended_Clean_URL"]].head(20).to_dict(orient="records")
                                u_prompt = f"""You are a Senior Technical SEO & Information Architecture Strategist at Connext Global Solutions.
Review these audited URLs, structural issues, and page topics:
{json.dumps(sample_u_rows, indent=2)}

Provide a concise, executive-ready URL Architecture & Keyword Optimization Plan:
1. **URL Structure & Taxonomy Diagnosis:** Explain structural flaws (such as redundant folders like /blogs/blog/, CMS prefixes, or overly long slugs) and how fixing them improves crawl efficiency and topical authority.
2. **Priority 301 Redirect & Slug Rewrite Table:** List the top URLs to restructure with their exact Recommended Target URL, Primary Keyword, and 2 Secondary Long-Tail Keywords.
3. **Safe Migration Checklist:** 4 bullet points covering 301 redirects, canonical updates, XML sitemap refresh, and internal link updates."""
                                ai_u_txt, mod_u = query_gemini_dynamic(u_prompt, gemini_key)
                                if ai_u_txt:
                                    st.session_state["url_ai_recommendations"] = ai_u_txt
                                    st.success(f"✓ URL & Keyword Architecture Plan Generated ({mod_u})!")

                if st.session_state.get("url_ai_recommendations"):
                    st.markdown(st.session_state["url_ai_recommendations"])

        with xray_tab6:
            st.markdown("##### 🤖 Robots.txt & AI Crawler Governance (GPTBot, ClaudeBot, PerplexityBot, Google-Extended)")
            rob_data = st.session_state.get("robots_audit_data", {})
            if rob_data.get("ai_bots"):
                rb1, rb2, rb3 = st.columns(3)
                rb1.metric("robots.txt File Status", "✓ Active (200)" if rob_data.get("exists") else "⚠️ Not Found")
                rb2.metric("Sitemaps Declared in robots.txt", len(rob_data.get("sitemaps_declared", [])))
                rb3.metric("Crawled URLs Blocked by Disallow", len(rob_data.get("blocked_target_urls", [])), delta="Blocked URLs!" if rob_data.get("blocked_target_urls") else "0 Blocked", delta_color="inverse" if rob_data.get("blocked_target_urls") else "normal")
                st.dataframe(pd.DataFrame(rob_data["ai_bots"]), hide_index=True, use_container_width=True)
                if rob_data.get("blocked_target_urls"):
                    st.error("🚨 The following target URLs are blocked by Disallow directives in robots.txt:")
                    st.dataframe(pd.DataFrame(rob_data["blocked_target_urls"]), hide_index=True, use_container_width=True)
            else:
                st.info("Run the site audit above to inspect robots.txt and AI crawler permissions.")

            st.markdown("##### 🔀 HTTP Redirect Chains, Indexability & Server Response Latency (TTFB)")
            tech_gov_cols = [c for c in ["URL", "Status", "Indexability_Status", "Redirect_Hops", "Redirect_Chain", "Response_Time_ms", "URL_Depth"] if c in display_df.columns]
            if tech_gov_cols:
                st.dataframe(display_df[tech_gov_cols], hide_index=True, use_container_width=True)

            st.markdown("##### 🔗 Broken Internal Links & Redirect Probe (404 / 301 / 5xx Scanner)")
            if st.button("🔎 Scan Internal Links for 404 Broken Pages & Redirects", key="btn_probe_links"):
                with st.spinner("Probing internal links across crawled pages for 404/5xx errors and redirects..."):
                    df_brk = verify_internal_links_health(df, HEADERS, max_links=60)
                    st.session_state["df_broken_links"] = df_brk
                    if df_brk.empty:
                        st.success("✓ All internal links on the crawled pages are already verified HTTP 200 OK!")
                    else:
                        broken_only = df_brk[df_brk["Status_Flag"].str.contains("Broken|Error", na=False)]
                        st.success(f"✓ Probed {len(df_brk)} internal links — found {len(broken_only)} broken link(s).")
            if not st.session_state.get("df_broken_links", pd.DataFrame()).empty:
                st.dataframe(st.session_state["df_broken_links"], hide_index=True, use_container_width=True)

        with xray_tab7:
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
            for _, r in df.head(150).iterrows():
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
    st.subheader("👑 Branded Search & Top Most Valuable Pages Analyzer")
    st.markdown("""
    <div class="guide-box">
        <strong>🎯 Why Check Branded Search Results?</strong> When people search your exact brand name on Google, the #1 result and its <strong>Google Sitelinks</strong> reveal the exact pages Google considers your website's most valuable, authoritative entry points—and exposes whether third-party review sites or competitors are hijacking your branded traffic.
    </div>
    """, unsafe_allow_html=True)

    default_brand_q = (urlparse(st.session_state.get("target_domain", "https://alphacord.com")).netloc.replace("www.", "").split(".")[0] or "alphacord").title()
    col_br1, col_br2 = st.columns([3, 1])
    with col_br1:
        brand_search_input = st.text_input("Brand Name Query to Audit on Google (US Index):", value=default_brand_q)
    with col_br2:
        st.write("")
        st.write("")
        run_brand_serp_btn = st.button("👑 Audit Brand SERP & Top Pages", use_container_width=True)

    if run_brand_serp_btn:
        if not serpapi_key:
            st.error("Please provide a SerpApi key in the sidebar.")
        else:
            with st.spinner(f"Analyzing live Google branded SERP & top valuable pages for '{brand_search_input}'..."):
                t_dom_br = st.session_state.get("target_domain", "https://alphacord.com")
                df_br, sl_list, kg_br = analyze_brand_serp_top_pages(brand_search_input, t_dom_br, serpapi_key)
                st.session_state["df_brand_serp"] = df_br
                st.session_state["brand_sitelinks"] = sl_list
                st.session_state["brand_kg_panel"] = kg_br
                if not df_br.empty:
                    st.success(f"✓ Brand SERP Audit Complete! Captured {len(df_br)} Page-1 results and {len(sl_list)} Google Sitelinks.")

    if not st.session_state.get("df_brand_serp", pd.DataFrame()).empty:
        df_br_res = st.session_state["df_brand_serp"]
        sl_res = st.session_state.get("brand_sitelinks", [])
        kg_br_res = st.session_state.get("brand_kg_panel", {})

        own_br_cnt = int(df_br_res["Ownership"].str.contains("Your Website", na=False).sum())
        ext_br_cnt = len(df_br_res) - own_br_cnt
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Branded Page-1 Ownership", f"{own_br_cnt}/{len(df_br_res)} Slots", delta=f"{ext_br_cnt} External/Rival Links" if ext_br_cnt > 0 else "100% Owned")
        b2.metric("Most Valuable Sitelink Pages", f"{len(sl_res)} Pages", help="Pages Google awards sitelink shortcuts for under your #1 result.")
        b3.metric("Knowledge Graph Brand Panel", "✓ Active" if kg_br_res.get("Has_Panel") else "🔴 Missing")
        b4.metric("#1 Branded Anchor", df_br_res["Displayed_URL"].iloc[0] if not df_br_res.empty else "N/A")

        if sl_res:
            st.markdown("##### 🌟 Google's Top Most Valuable Pages (Awarded Branded Sitelinks)")
            st.dataframe(pd.DataFrame(sl_res), hide_index=True, use_container_width=True)

        st.markdown("##### 📋 Full Branded SERP Breakdown (Who Owns Your Brand Name on Page 1)")
        st.dataframe(df_br_res, hide_index=True, use_container_width=True)

    st.markdown("---")
    st.subheader("📈 Non-Branded Commercial Organic Search Rank Benchmark")
    st.write("Track search engine positioning across high-intent non-branded commercial terms using SerpApi.")
    
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
                params = {"engine": "google", "q": kw, "location": "United States", "hl": "en", "gl": "us", "api_key": serpapi_key}
                res, err_msg = fetch_serpapi_with_retry(params, max_retries=2, timeout=28)
                if res:
                    found_rank = "> 20 (Absent)"
                    ranking_url = "—"
                    top_competitor = "—"
                    org_results = res.get("organic_results", [])
                    top_10_doms = []
                    if org_results:
                        top_competitor = urlparse(org_results[0].get("link", "")).netloc.replace("www.", "")
                    for pos, item in enumerate(org_results[:20], 1):
                        link = item.get("link", "")
                        dom_i = urlparse(link).netloc.replace("www.", "").lower()
                        if pos <= 10 and dom_i:
                            top_10_doms.append({"domain": dom_i, "rank": pos})
                        if target_host in link and found_rank == "> 20 (Absent)":
                            found_rank = f"#{pos}"
                            ranking_url = clean_display_url(link)
                    records.append({
                        "Keyword": kw,
                        "Target_Rank": found_rank,
                        "Ranking_URL": ranking_url,
                        "Top_Ranking_Competitor": top_competitor,
                        "Status": "Visible" if "#" in found_rank else "Absent",
                        "Top_10_JSON": json.dumps(top_10_doms)
                    })
                else:
                    records.append({
                        "Keyword": kw,
                        "Target_Rank": "Not Checked (Timeout)",
                        "Ranking_URL": "—",
                        "Top_Ranking_Competitor": "—",
                        "Status": "Timeout"
                    })
                prog.progress((idx + 1) / len(kws))
                
            df_rank = pd.DataFrame(records)
            st.session_state["df_rankings"] = df_rank
            st.success("✓ SERP Rank Tracking Complete!")

    if not st.session_state["df_rankings"].empty:
        df_r = st.session_state["df_rankings"]
        valid_r_ui = df_r[~df_r["Target_Rank"].astype(str).str.contains("Error|Timeout", case=False, na=False)]
        absent_count = (valid_r_ui["Target_Rank"] == "> 20 (Absent)").sum()
        invisibility_pct = (absent_count / max(1, len(valid_r_ui))) * 100
        
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

        disp_r_cols = [c for c in ["Keyword", "Target_Rank", "Ranking_URL", "Top_Ranking_Competitor", "Status"] if c in df_r.columns]
        st.dataframe(df_r[disp_r_cols], hide_index=True, use_container_width=True)

        # Competitor Share-of-Voice (SOV) Leaderboard across Page 1
        if "Top_10_JSON" in df_r.columns:
            sov_stats = {}
            total_valid_qs = max(1, len(valid_r_ui))
            for _, r_s in valid_r_ui.iterrows():
                try:
                    t10 = json.loads(r_s.get("Top_10_JSON", "[]"))
                    seen_in_q = set()
                    for entry in t10:
                        d_name = entry.get("domain", "")
                        rk_val = entry.get("rank", 10)
                        if d_name and d_name not in seen_in_q:
                            seen_in_q.add(d_name)
                            if d_name not in sov_stats:
                                sov_stats[d_name] = {"Domain": d_name, "Page_1_Keywords": 0, "Ranks": []}
                            sov_stats[d_name]["Page_1_Keywords"] += 1
                            sov_stats[d_name]["Ranks"].append(rk_val)
                except Exception:
                    pass
            if sov_stats:
                st.markdown("##### 🏆 Page-1 Competitor Share-of-Voice (SOV) Leaderboard")
                st.caption("Measures which domains dominate Page-1 positions across all tracked non-branded queries.")
                sov_rows = []
                t_host_clean = urlparse(st.session_state.get("target_domain", "")).netloc.replace("www.", "").lower()
                for d_k, info_s in sov_stats.items():
                    kw_hits = info_s["Page_1_Keywords"]
                    avg_rk = round(sum(info_s["Ranks"]) / max(1, len(info_s["Ranks"])), 1)
                    sov_pct = round((kw_hits / total_valid_qs) * 100, 1)
                    sov_rows.append({
                        "Domain": d_k,
                        "Role": "🟢 Your Website" if (t_host_clean and t_host_clean in d_k) else "⚔️ Competitor / Publisher",
                        "Page_1_Appearances": kw_hits,
                        "Page_1_SOV_Pct": sov_pct,
                        "Avg_Page_1_Position": avg_rk
                    })
                df_sov = pd.DataFrame(sov_rows).sort_values(by=["Page_1_Appearances", "Avg_Page_1_Position"], ascending=[False, True]).head(12)
                st.dataframe(
                    df_sov,
                    column_config={
                        "Page_1_SOV_Pct": st.column_config.ProgressColumn("Page-1 Share of Voice", format="%.1f%%", min_value=0, max_value=100)
                    },
                    hide_index=True,
                    use_container_width=True
                )
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
                params = {"engine": "google", "q": q, "location": "United States", "hl": "en", "gl": "us", "api_key": serpapi_key}
                res, err_msg = fetch_serpapi_with_retry(params, max_retries=2, timeout=28)
                if res:
                    ai_overview = res.get("ai_overview", {})
                    has_aio = bool(ai_overview)
                    cited = False
                    winning_sources = []
                    if has_aio:
                        if not ai_overview.get("references") and ai_overview.get("serpapi_link"):
                            try:
                                aio_full = requests.get(ai_overview["serpapi_link"], params={"api_key": serpapi_key}, timeout=12).json()
                                ai_overview = aio_full.get("ai_overview", ai_overview)
                            except Exception:
                                pass
                        for ref in ai_overview.get("references", []):
                            ref_link = ref.get("link", "")
                            if ref_link:
                                dom_ref = urlparse(ref_link).netloc.replace("www.", "")
                                if dom_ref and dom_ref not in winning_sources:
                                    winning_sources.append(dom_ref)
                                if target_host in ref_link:
                                    cited = True
                    aio_snippet = ""
                    if has_aio:
                        if isinstance(ai_overview.get("snippet"), str):
                            aio_snippet = ai_overview["snippet"][:260]
                        elif isinstance(ai_overview.get("text_blocks"), list):
                            parts_tb = [tb.get("snippet", "") for tb in ai_overview["text_blocks"] if isinstance(tb, dict) and tb.get("snippet")]
                            aio_snippet = " ".join(parts_tb)[:260]
                    aeo_records.append({
                        "Buyer_Query": q,
                        "AI_Overview_Generated": "Yes" if has_aio else "No",
                        "Target_Cited": "✓ Yes" if cited else "🔴 Omitted",
                        "Cited_Competitors": ", ".join(winning_sources[:3]) if winning_sources else ("AI Summary (No External Links)" if has_aio else "None"),
                        "AI_Answer_Preview": aio_snippet or "—",
                        "Outcome": "Won" if cited else "Competitor Won"
                    })
                else:
                    aeo_records.append({
                        "Buyer_Query": q,
                        "AI_Overview_Generated": "Timeout",
                        "Target_Cited": "Not Checked (Timeout)",
                        "Cited_Competitors": "—",
                        "Outcome": "Timeout"
                    })
                prog_aeo.progress((idx + 1) / len(aeo_queries))

            df_aeo = pd.DataFrame(aeo_records)
            st.session_state["df_aeo"] = df_aeo
            st.success("✓ AEO Benchmark Complete!")

    if not st.session_state["df_aeo"].empty:
        df_a = st.session_state["df_aeo"]
        valid_a_ui = df_a[~df_a["Target_Cited"].astype(str).str.contains("Error|Timeout", case=False, na=False)]
        competitor_wins = (valid_a_ui["Target_Cited"] == "🔴 Omitted").sum()
        loss_rate = (competitor_wins / max(1, len(valid_a_ui))) * 100
        
        st.metric("Generative AI Competitor Win Rate", f"{loss_rate:.1f}%", help="Percentage of AI Overviews won by competitors or third parties.")
        
        # Visual Chart: AEO Win vs Loss
        st.markdown("##### 📊 AI Overview Recommendation Win vs. Loss Ratio")
        st.bar_chart(df_a["Outcome"].value_counts(), color="#f2a900")

        st.dataframe(df_a, hide_index=True, use_container_width=True)

        # Aggregate Top Third-Party Domains Cited by Google AI Overviews (Digital PR Targets)
        cited_dom_counter = Counter()
        for c_str in df_a["Cited_Competitors"].dropna():
            for d_item in [x.strip() for x in str(c_str).split(",") if x.strip() and "." in x and "Summary" not in x]:
                cited_dom_counter[d_item] += 1
        if cited_dom_counter:
            st.markdown("##### 📣 Top Third-Party Domains Cited by AI (Digital PR & Outreach Targets)")
            st.caption("Websites that Google AI Overviews rely on most frequently when answering buyer questions in your category.")
            pr_targets_df = pd.DataFrame([
                {"Cited_Domain": dom_k, "AI_Citations": cnt_k, "Strategic_Action": "Secure brand mention, review profile, or guest placement on this domain"}
                for dom_k, cnt_k in cited_dom_counter.most_common(10)
            ])
            st.dataframe(pr_targets_df, hide_index=True, use_container_width=True)
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
    st.markdown("""
    <div class="guide-box">
        <strong>ℹ️ Important Scope Notice (Single-Page vs. Domain-Wide Speed):</strong><br>
        • <strong>Lighthouse Lab Speed Score, LCP, TBT &amp; CLS</strong> are measured <strong>strictly for the single specific URL entered below</strong> (not the entire website). Different page templates (e.g., Homepage vs. Product Pages vs. Blog Articles) can have completely different mobile load speeds.<br>
        • <strong>CrUX 28-Day Field Data</strong> reflects real Chrome visitor cohorts across the origin domain when traffic thresholds are met.
    </div>
    """, unsafe_allow_html=True)

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
                # 1. Dual Lab Data (Mobile + Desktop PageSpeed Insights API)
                def fetch_psi_strategy(target_u, strat):
                    ps_u = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={target_u}&strategy={strat}&key={google_cloud_key}"
                    r_p = requests.get(ps_u, timeout=32).json()
                    lh_obj = r_p.get("lighthouseResult", {})
                    aud_obj = lh_obj.get("audits", {})
                    return {
                        "Score": int(lh_obj.get("categories", {}).get("performance", {}).get("score", 0) * 100),
                        "TBT": aud_obj.get("total-blocking-time", {}).get("displayValue", "N/A"),
                        "LCP": aud_obj.get("largest-contentful-paint", {}).get("displayValue", "N/A"),
                        "CLS": aud_obj.get("cumulative-layout-shift", {}).get("displayValue", "N/A"),
                        "FCP": aud_obj.get("first-contentful-paint", {}).get("displayValue", "N/A"),
                        "Speed_Index": aud_obj.get("speed-index", {}).get("displayValue", "N/A"),
                        "Tested_URL": target_u
                    }

                try:
                    cwv_mob = fetch_psi_strategy(cwv_url, "mobile")
                    st.session_state["cwv_metrics"] = cwv_mob
                    st.session_state["cwv_tested_url"] = cwv_url
                except Exception as e:
                    st.error(f"Mobile PageSpeed Lab API Error: {e}")

                try:
                    cwv_desk = fetch_psi_strategy(cwv_url, "desktop")
                    st.session_state["cwv_desktop_metrics"] = cwv_desk
                except Exception:
                    st.session_state["cwv_desktop_metrics"] = {}

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
        
        tested_u_disp = cwv.get("Tested_URL") or st.session_state.get("cwv_tested_url") or cwv_url
        st.markdown(f"""
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-left:4px solid #004b93; border-radius:6px; padding:16px; margin-bottom:15px;">
            <div style="font-size:18px; font-weight:bold; color:#004b93; margin-bottom:4px;">
                Lab Mobile Performance Score: <span style="color:{score_color}; font-size:24px;">{score_val}/100</span>
            </div>
            <div style="font-size:12.5px; color:#475569;">
                📍 <strong>Audited Page Scope:</strong> <code>{tested_u_disp}</code> <em>(Single-page Lighthouse lab benchmark — test individual product or landing page URLs above to compare template speeds)</em>
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

        # Desktop Lab Comparison
        cwv_d = st.session_state.get("cwv_desktop_metrics", {})
        if cwv_d and "Score" in cwv_d:
            st.markdown("##### 🖥️ Desktop vs. Mobile Lab Comparison (Same URL)")
            comp_cwv_df = pd.DataFrame([
                {"Device": "📱 Mobile (4G Throttled)", "Performance Score": f"{cwv.get('Score', 0)}/100", "LCP": cwv.get("LCP", "N/A"), "TBT": cwv.get("TBT", "N/A"), "CLS": cwv.get("CLS", "N/A")},
                {"Device": "🖥️ Desktop (Broadband)", "Performance Score": f"{cwv_d.get('Score', 0)}/100", "LCP": cwv_d.get("LCP", "N/A"), "TBT": cwv_d.get("TBT", "N/A"), "CLS": cwv_d.get("CLS", "N/A")}
            ])
            st.dataframe(comp_cwv_df, hide_index=True, use_container_width=True)

    # Multi-Template Batch CWV Comparison
    st.markdown("---")
    st.subheader("📑 Multi-Template Speed Comparison (Compare Up to 5 Key URLs)")
    st.caption("Test different page templates (e.g., Homepage, Product Page, Collection Page, Blog Article) side-by-side.")
    crawled_sample_urls = st.session_state["df_crawl"]["URL"].head(4).tolist() if not st.session_state["df_crawl"].empty else [st.session_state.get("target_domain", "https://alphacord.com")]
    batch_cwv_input = st.text_area("Page Template URLs to Benchmark (Max 5 URLs, one per line):", value="\n".join(crawled_sample_urls), height=105)
    if st.button("⚡ Benchmark Multiple Page Templates (Mobile)", key="btn_batch_cwv"):
        if not google_cloud_key:
            st.error("Google Cloud API Key required in sidebar.")
        else:
            b_urls = [u.strip() for u in batch_cwv_input.split("\n") if u.strip().startswith("http")][:5]
            b_rows = []
            b_prog = st.progress(0)
            for idx_b, bu in enumerate(b_urls):
                try:
                    ps_b = f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url={bu}&strategy=mobile&key={google_cloud_key}"
                    rb = requests.get(ps_b, timeout=32).json()
                    lh_b = rb.get("lighthouseResult", {})
                    aud_b = lh_b.get("audits", {})
                    b_rows.append({
                        "Tested_Template_URL": bu,
                        "Mobile_Score": int(lh_b.get("categories", {}).get("performance", {}).get("score", 0) * 100),
                        "LCP": aud_b.get("largest-contentful-paint", {}).get("displayValue", "N/A"),
                        "TBT": aud_b.get("total-blocking-time", {}).get("displayValue", "N/A"),
                        "CLS": aud_b.get("cumulative-layout-shift", {}).get("displayValue", "N/A")
                    })
                except Exception:
                    b_rows.append({"Tested_Template_URL": bu, "Mobile_Score": 0, "LCP": "Timeout", "TBT": "Timeout", "CLS": "Timeout"})
                b_prog.progress((idx_b + 1) / max(1, len(b_urls)))
            b_prog.empty()
            st.session_state["df_cwv_batch"] = pd.DataFrame(b_rows)
            st.success(f"✓ Benchmarked {len(b_rows)} page templates!")

    if not st.session_state.get("df_cwv_batch", pd.DataFrame()).empty:
        st.dataframe(st.session_state["df_cwv_batch"], hide_index=True, use_container_width=True)

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

                    st.session_state["first_party_summary"] = [
                        s for s in st.session_state.get("first_party_summary", []) if s.get("Source") != f"GSC ({file_name})"
                    ] + [{
                        "Source": f"GSC ({file_name})",
                        "Metric_1": f"{tot_clicks:,} Total Clicks",
                        "Metric_2": f"{tot_impressions:,} Impressions",
                        "Metric_3": f"Avg CTR: {avg_ctr} | Avg Pos: {avg_pos}"
                    }]
                    if pos_cols:
                        quick_wins = df_up[(df_up[i_col] >= 150) & (df_up[pos_cols[0]] >= 7.0) & (df_up[pos_cols[0]] <= 22.0)].sort_values(by=i_col, ascending=False)
                        if not quick_wins.empty:
                            st.session_state["df_gsc_quickwins"] = quick_wins[[q_col, c_col, i_col, pos_cols[0]]].head(15).copy()
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

                    st.session_state["first_party_summary"] = [
                        s for s in st.session_state.get("first_party_summary", []) if s.get("Source") != f"GA4 ({file_name})"
                    ] + [{
                        "Source": f"GA4 ({file_name})",
                        "Metric_1": f"{tot_sessions:,} Sessions",
                        "Metric_2": f"{tot_convs:,} Conversions",
                        "Metric_3": f"{len(df_up)} Landing Pages Tracked"
                    }]
                    st.markdown("##### Top Landing Pages by Session Volume")
                    top_pages = df_up.sort_values(by=s_col, ascending=False).head(15)
                    st.session_state["df_ga4_top_pages"] = top_pages.copy()
                    st.bar_chart(top_pages.head(8).set_index(p_col)[s_col], color="#e85122")
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
            "invis_pct": round(((df_r[~df_r["Target_Rank"].astype(str).str.contains("Error|Timeout", case=False, na=False)]["Target_Rank"] == "> 20 (Absent)").sum() / max(1, len(df_r[~df_r["Target_Rank"].astype(str).str.contains("Error|Timeout", case=False, na=False)])) * 100), 1) if not df_r.empty else 0.0,
            "aeo_loss_pct": round(((df_a[~df_a["Target_Cited"].astype(str).str.contains("Error|Timeout", case=False, na=False)]["Target_Cited"] == "🔴 Omitted").sum() / max(1, len(df_a[~df_a["Target_Cited"].astype(str).str.contains("Error|Timeout", case=False, na=False)])) * 100), 1) if not df_a.empty else 0.0,
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
        valid_r_dash = df_r[~df_r["Target_Rank"].astype(str).str.contains("Error|Timeout", case=False, na=False)] if not df_r.empty else pd.DataFrame()
        valid_a_dash = df_a[~df_a["Target_Cited"].astype(str).str.contains("Error|Timeout", case=False, na=False)] if not df_a.empty else pd.DataFrame()
        invis_pct = ((valid_r_dash["Target_Rank"] == "> 20 (Absent)").sum() / max(1, len(valid_r_dash)) * 100) if not valid_r_dash.empty else 100.0
        aeo_loss_pct = ((valid_a_dash["Target_Cited"] == "🔴 Omitted").sum() / max(1, len(valid_a_dash)) * 100) if not valid_a_dash.empty else 100.0
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

        df_u_dash = st.session_state.get("df_url_audit", pd.DataFrame())
        if df_u_dash.empty and not df_c.empty:
            df_u_dash = analyze_url_structure_and_keywords(df_c)
        u_issues_cnt = int((df_u_dash["Migration_Action"] != "No Change Required").sum()) if not df_u_dash.empty else 0
        if u_issues_cnt > 0:
            with st.expander(f"⚠️ URL Structure & Slug Optimization Opportunities ({u_issues_cnt} URLs Flagged)", expanded=False):
                st.markdown(f"""
                * **Business Impact:** 🟡 **Information Architecture & CTR** — {u_issues_cnt} URL(s) contain redundant nested folders (e.g. `/blogs/blog/...`), CMS prefixes, or unoptimized keyword slugs.
                * **Action Required:** Review **Tab 1 → Sub-Tab 5 (URL Structure & Keyword Audit)** to deploy clean slugs with 301 redirects.
                """)
            dynamic_plan.append({"Priority": "Priority 2 (High Impact)", "Initiative": "Optimize Redundant URL Folders & Keyword Slugs", "Action": f"Clean up {u_issues_cnt} suboptimal URL structures with 301 redirects and updated internal links.", "Effort": "Low (1-2 days)", "Projected Impact": "Improves crawl clarity, keyword relevance, and SERP CTR"})

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
    target_name, has_llms, logo_url, crawly_data=None,
    section_label="", client_name="",
    df_serp_ads=None, df_brand_serp=None, brand_sitelinks=None,
    df_broken_links=None, robots_data=None, df_mentions=None,
    first_party_summary=None
):
    crawly_data = crawly_data or {}
    df_serp_ads = df_serp_ads if df_serp_ads is not None else pd.DataFrame()
    df_brand_serp = df_brand_serp if df_brand_serp is not None else pd.DataFrame()
    brand_sitelinks = brand_sitelinks or []
    df_broken_links = df_broken_links if df_broken_links is not None else pd.DataFrame()
    robots_data = robots_data or {}
    df_mentions = df_mentions if df_mentions is not None else pd.DataFrame()
    first_party_summary = first_party_summary or []

    total_pages = len(df_c) if not df_c.empty else 0
    is_single_or_thin = (total_pages <= 2)
    clean_host = urlparse(target_name).netloc.replace("www.", "") or target_name
    brand_display = client_name.strip() if client_name and client_name.strip() else clean_host.split(".")[0].title()
    scope_title = detect_audit_scope_label(df_c, target_name, section_label)

    comm_schema_cnt = int(df_c["Commercial_Schema"].sum()) if (not df_c.empty and "Commercial_Schema" in df_c.columns) else 0
    comm_schema_pct = (comm_schema_cnt / total_pages * 100) if total_pages > 0 else 0.0
    missing_schema_pct = max(0.0, 100.0 - comm_schema_pct)

    thin_pages_df = df_c[df_c["Word_Count"] < 300] if (not df_c.empty and "Word_Count" in df_c.columns) else pd.DataFrame()
    thin_cnt = len(thin_pages_df)
    missing_meta_cnt = int(((df_c["Meta_Title_Status"] == "Missing") | (df_c["Meta_Desc_Status"] == "Missing")).sum()) if (not df_c.empty and "Meta_Title_Status" in df_c.columns) else 0
    mashed_count = int(df_c["Concatenated_H1"].sum()) if (not df_c.empty and "Concatenated_H1" in df_c.columns) else 0
    avg_a11y = round(df_c["A11y_Score"].mean()) if (not df_c.empty and "A11y_Score" in df_c.columns) else 75
    miss_alt_total = int(df_c["Missing_Alt_Count"].sum()) if (not df_c.empty and "Missing_Alt_Count" in df_c.columns) else 0

    valid_r = df_r[~df_r["Target_Rank"].astype(str).str.contains("Error|Timeout|Skipped", case=False, na=False)] if not df_r.empty else pd.DataFrame()
    has_serp = not valid_r.empty
    invis_pct = ((valid_r["Target_Rank"] == "> 20 (Absent)").sum() / len(valid_r) * 100) if has_serp else None

    valid_a = df_a[~df_a["Target_Cited"].astype(str).str.contains("Error|Timeout|Skipped", case=False, na=False)] if not df_a.empty else pd.DataFrame()
    has_aeo = not valid_a.empty
    aeo_loss_pct = ((valid_a["Target_Cited"] == "🔴 Omitted").sum() / len(valid_a) * 100) if has_aeo else None

    has_eq = (not df_eq.empty and "Inbound_Editorial_Links" in df_eq.columns)
    orphan_cnt = int((df_eq["Inbound_Editorial_Links"] == 0).sum()) if has_eq else 0

    has_can = not df_can.empty
    dup_pairs_cnt = len(df_can) if has_can else 0

    has_ads = not df_ads.empty
    search_ads_cnt = int(df_ads["Channel"].str.contains("Search", case=False, na=False).sum()) if (has_ads and "Channel" in df_ads.columns) else 0
    conquest_cnt = int(df_serp_ads["Competitor_Status"].str.contains("Conquesting", case=False, na=False).sum()) if (not df_serp_ads.empty and "Competitor_Status" in df_serp_ads.columns) else 0
    broken_cnt = int(df_broken_links["Status_Flag"].str.contains("Broken|Error", case=False, na=False).sum()) if (not df_broken_links.empty and "Status_Flag" in df_broken_links.columns) else 0

    sec_grade = sec_res.get("Grade", "Verified")
    cwv_score = cwv_res.get("Score", "N/A")
    raw_lcp = str(cwv_res.get("LCP", "N/A"))
    da_val = crawly_data.get("domain_authority", 0)
    ref_dom = crawly_data.get("referring_domains", 0)

    kpi_cards = [
        (f"{total_pages}", "Pages Reviewed", "#004b93"),
        (f"{comm_schema_pct:.0f}%", "Product/FAQ Schema", "#16a34a" if comm_schema_pct >= 80 else "#dc2626"),
        (f"{thin_cnt}/{total_pages}", "Thin Pages (<300w)", "#16a34a" if thin_cnt == 0 else "#dc2626"),
    ]
    if has_serp:
        kpi_cards.append((f"{100.0 - invis_pct:.0f}%", "Google Page 1–2 Share", "#16a34a" if invis_pct <= 30 else "#dc2626"))
    elif has_can:
        kpi_cards.append((f"{dup_pairs_cnt}", "Duplicate Copy Pairs", "#16a34a" if dup_pairs_cnt == 0 else "#dc2626"))
    else:
        kpi_cards.append((f"{missing_meta_cnt}", "Missing Meta Tags", "#16a34a" if missing_meta_cnt == 0 else "#dc2626"))

    if has_aeo:
        kpi_cards.append((f"{100.0 - aeo_loss_pct:.0f}%", "AI Answer Citations", "#16a34a" if aeo_loss_pct <= 30 else "#dc2626"))
    elif has_eq:
        kpi_cards.append((f"{orphan_cnt}", "Unlinked Pages", "#16a34a" if orphan_cnt == 0 else "#e85122"))
    else:
        kpi_cards.append((f"{avg_a11y}/100", "Accessibility Score", "#16a34a" if avg_a11y >= 85 else "#f2a900"))

    kpi_html = "".join([
        f'<div class="metric-box"><div class="metric-num" style="color:{col};">{esc(val)}</div><div class="metric-title">{esc(lbl)}</div></div>'
        for val, lbl, col in kpi_cards
    ])

    whats_working = []
    priority_fixes = []

    if total_pages >= 5:
        whats_working.append(f"<strong>Dedicated Page Structure ({total_pages} Pages):</strong> Having individual URLs for separate offerings gives search engines clear landing pages to index.")
    if has_llms:
        whats_working.append("<strong>AI Crawler Guide Active (<code>/llms.txt</code>):</strong> Your site publishes an <code>llms.txt</code> directory so AI tools like ChatGPT and Perplexity can read your offerings.")
    if comm_schema_cnt > 0:
        whats_working.append(f"<strong>Structured Data Found on {comm_schema_cnt} Page(s):</strong> Search engines can read structured business tags on {comm_schema_pct:.0f}% of the pages in this batch.")
    if brand_sitelinks:
        sl_names = ", ".join([esc(s.get("Sitelink_Title", "")) for s in brand_sitelinks[:3] if s.get("Sitelink_Title")])
        whats_working.append(f"<strong>Google Branded Sitelinks Active:</strong> Google highlights key pages ({sl_names}) directly under your #1 branded search result.")
    if mashed_count == 0:
        whats_working.append("<strong>Clean Page Headlines:</strong> All main headings (H1 tags) are properly spaced and easy for search engines to read.")
    if has_ads and search_ads_cnt > 0:
        whats_working.append(f"<strong>Active Google Search Ads ({search_ads_cnt} Text Ads):</strong> Paid search campaigns are actively defending bottom-of-funnel buyer queries.")
    if "A" in str(sec_grade):
        whats_working.append(f"<strong>Strong Website Security ({esc(sec_grade)}):</strong> HTTPS encryption and browser security headers are properly configured.")
    if da_val and int(da_val) >= 35:
        whats_working.append(f"<strong>Established Domain Authority ({da_val}/100):</strong> Backed by {ref_dom:,} referring websites.")

    if is_single_or_thin:
        priority_fixes.append(f"<strong>Expand Page Depth ({total_pages} Page(s) Reviewed):</strong> Search engines rarely rank single-page sections for multiple buyer searches. Create dedicated pages for each core service.")
    if comm_schema_pct < 100.0:
        unmarked = total_pages - comm_schema_cnt
        priority_fixes.append(f"<strong>Add Product &amp; FAQ Labels to {unmarked} Page(s) ({missing_schema_pct:.0f}% Missing):</strong> Only {comm_schema_cnt} of {total_pages} pages include product/service schema. Adding structured labels helps Google display pricing, reviews, and FAQs.")
    if thin_cnt > 0:
        sample_thin = ", ".join([esc(urlparse(u).path or "/") for u in thin_pages_df["URL"].head(2)])
        priority_fixes.append(f"<strong>Expand Thin Copy on {thin_cnt} Page(s) (&lt;300 Words):</strong> Pages like <code>{sample_thin}</code> have very little text, making it hard for Google or AI tools to rank them.")
    if conquest_cnt > 0:
        priority_fixes.append(f"<strong>Defend Branded Search ({conquest_cnt} Competitor Conquesting Ad(s)):</strong> Rivals are bidding on your brand name on Google Search. Maintain exact-match brand search protection.")
    if broken_cnt > 0:
        priority_fixes.append(f"<strong>Fix {broken_cnt} Broken Internal Link(s):</strong> Dead internal links hurt visitor navigation and waste search engine crawl budget.")
    if has_can and dup_pairs_cnt > 0:
        priority_fixes.append(f"<strong>Consolidate {dup_pairs_cnt} Duplicate Page Pair(s):</strong> Several URLs in this batch share identical text, splitting ranking power. Merge duplicates or set canonical tags.")
    if mashed_count > 0:
        mashed_urls = ", ".join([esc(urlparse(u).path or "/") for u in df_c[df_c["Concatenated_H1"]]["URL"].head(2)])
        priority_fixes.append(f"<strong>Fix Merged Headline Words ({mashed_count} Page(s)):</strong> Styling on <code>{mashed_urls}</code> removes spaces between words in the main headline.")
    df_u_1p = analyze_url_structure_and_keywords(df_c)
    u_fix_1p = df_u_1p[df_u_1p["Migration_Action"] != "No Change Required"] if not df_u_1p.empty else pd.DataFrame()
    if not u_fix_1p.empty:
        sample_u_bad = esc(urlparse(u_fix_1p["Current_URL"].iloc[0]).path or "/")
        sample_u_good = esc(urlparse(u_fix_1p["Recommended_Clean_URL"].iloc[0]).path or "/")
        priority_fixes.append(f"<strong>Clean Up {len(u_fix_1p)} Suboptimal URL Path(s):</strong> For example, simplify <code>{sample_u_bad}</code> to <code>{sample_u_good}</code> (with a 301 redirect) for cleaner keyword alignment.")
    if miss_alt_total > 0:
        priority_fixes.append(f"<strong>Add Alt Descriptions to {miss_alt_total} Image(s):</strong> Descriptive alt tags improve image search visibility and screen-reader accessibility.")
    if has_serp and invis_pct > 40:
        priority_fixes.append(f"<strong>Improve Non-Branded Google Visibility ({invis_pct:.0f}% Off Page 1–2):</strong> Prospective customers searching general category terms are finding competitors first.")
    if has_aeo and aeo_loss_pct > 50:
        priority_fixes.append(f"<strong>Win Citations in AI Answers ({aeo_loss_pct:.0f}% Omitted):</strong> Add direct Q&amp;A sections on key pages so Google AI Overviews and ChatGPT quote {esc(brand_display)}.")
    if "s" in raw_lcp:
        try:
            if float(raw_lcp.replace("s", "").replace(",", "").strip()) > 3.0:
                priority_fixes.append(f"<strong>Speed Up Mobile Page Load ({esc(raw_lcp)}):</strong> Compressing large hero images to WebP format will cut mobile wait times below 2.5 seconds.")
        except Exception:
            pass
    if has_eq and orphan_cnt > 0:
        priority_fixes.append(f"<strong>Connect {orphan_cnt} Unlinked Page(s):</strong> Add internal links from your main guides and blog posts to these pages.")

    if not whats_working:
        whats_working.append("<strong>Clean Technical Foundation:</strong> Pages load with HTTP 200 status and pass basic crawlability checks.")
    if not priority_fixes:
        priority_fixes.append("<strong>Ongoing Content &amp; FAQ Refresh:</strong> Keep service descriptions and customer FAQs updated to maintain search and AI visibility.")

    table_rows = ""
    if not df_c.empty:
        for idx, r in df_c.head(7).iterrows():
            u_clean = clean_display_url(r["URL"])
            w_cnt = int(r.get("Word_Count", 0))
            has_comm = bool(r.get("Commercial_Schema", False))
            is_mashed = bool(r.get("Concatenated_H1", False))

            if is_mashed:
                action_note = "Fix merged words in main headline"
                action_color = "#dc2626"
            elif w_cnt < 250 and not has_comm:
                action_note = f"Expand thin copy ({w_cnt}w) &amp; add schema"
                action_color = "#dc2626"
            elif w_cnt < 250:
                action_note = f"Expand thin page copy ({w_cnt} words)"
                action_color = "#b45309"
            elif not has_comm:
                action_note = "Add Product/Service &amp; FAQ schema"
                action_color = "#b45309"
            else:
                action_note = "Strong baseline — add Q&amp;A block"
                action_color = "#16a34a"

            table_rows += f"""<tr>
                <td style="font-family:monospace; font-size:6.4pt; word-break:break-all;">{esc(u_clean)}</td>
                <td style="text-align:center;">{w_cnt:,}</td>
                <td style="text-align:center; color:{'#16a34a' if has_comm else '#dc2626'}; font-weight:bold;">{'✓ Active' if has_comm else 'Missing'}</td>
                <td style="color:{action_color}; font-weight:bold;">{action_note}</td>
            </tr>"""

    w_list_html = "".join([f"<li>{x}</li>" for x in whats_working[:4]])
    f_list_html = "".join([f"<li>{x}</li>" for x in priority_fixes[:5]])

    meta_extras = []
    if da_val:
        meta_extras.append(f"<span><strong>Domain Authority:</strong> {da_val}/100</span>")
    if cwv_score != "N/A":
        meta_extras.append(f"<span><strong>Mobile Speed:</strong> {esc(cwv_score)}/100</span>")
    meta_extras.append(f"<span><strong>Accessibility:</strong> {avg_a11y}/100</span>")
    meta_extras.append(f"<span><strong>AI Guide (llms.txt):</strong> {'Active' if has_llms else 'Missing'}</span>")

    html_1p = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    @page {{
        size: letter portrait;
        margin: 0.38in 0.42in;
        @bottom-left {{
            content: "Prepared by Connext Global Solutions — Digital Strategy & Search Advisory";
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
    * {{ box-sizing: border-box; }}
    body {{
        font-family: 'DejaVu Sans', sans-serif;
        color: #1e293b;
        font-size: 7.3pt;
        line-height: 1.38;
        margin: 0;
        padding: 0;
    }}
    .header {{
        border-bottom: 2.5px solid #004b93;
        padding-bottom: 6px;
        margin-bottom: 7px;
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
        font-size: 8.2pt;
        font-weight: bold;
        margin-top: 2px;
    }}
    .meta-bar {{
        background: #f1f5f9;
        border: 1px solid #cbd5e1;
        padding: 5px 9px;
        border-radius: 3px;
        font-size: 6.8pt;
        margin-bottom: 8px;
        display: flex;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 6px;
    }}
    .metric-row {{
        display: flex;
        gap: 6px;
        margin-bottom: 9px;
    }}
    .metric-box {{
        flex: 1;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-top: 2.5px solid #004b93;
        padding: 7px 3px;
        text-align: center;
        border-radius: 3px;
    }}
    .metric-num {{
        font-size: 13pt;
        font-weight: bold;
        line-height: 1.1;
    }}
    .metric-title {{
        font-size: 5.7pt;
        color: #64748b;
        font-weight: bold;
        text-transform: uppercase;
        margin-top: 3px;
    }}
    .section-title {{
        color: #004b93;
        font-size: 8.8pt;
        font-weight: bold;
        text-transform: uppercase;
        letter-spacing: 0.3px;
        margin: 7px 0 5px 0;
        padding-bottom: 2px;
        border-bottom: 1.5px solid #004b93;
    }}
    .two-col {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        margin-bottom: 8px;
    }}
    .panel {{
        border: 1px solid #e2e8f0;
        padding: 7px 9px;
        border-radius: 4px;
        font-size: 6.8pt;
        line-height: 1.38;
    }}
    .panel-good {{ border-top: 3px solid #16a34a; background: #fcfefd; }}
    .panel-fix {{ border-top: 3px solid #dc2626; background: #fffdfd; }}
    .panel-head {{
        font-size: 7.5pt;
        font-weight: bold;
        margin-bottom: 4px;
    }}
    ul {{ margin: 0; padding-left: 13px; }}
    li {{ margin-bottom: 3px; }}
    table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 6.7pt;
        margin-top: 4px;
    }}
    thead {{ display: table-header-group; }}
    tr {{ page-break-inside: avoid; break-inside: avoid; }}
    th {{
        background: #004b93;
        color: white;
        padding: 4px 5px;
        text-align: left;
        font-weight: bold;
        border: 1px solid #003a73;
    }}
    td {{
        padding: 3.5px 5px;
        border: 1px solid #e2e8f0;
    }}
    tr:nth-child(even) td {{ background: #f8fafc; }}
</style>
</head>
<body>
    <div class="header">
        <div>
            <div class="title">{esc(brand_display)} — Page Performance &amp; Search Brief</div>
            <div class="subtitle">{esc(scope_title)}</div>
        </div>
        <div>
            <img src="{logo_url}" style="width: 112px;">
        </div>
    </div>

    <div class="meta-bar">
        <span><strong>Website:</strong> {esc(clean_host)}</span>
        <span><strong>Date:</strong> {datetime.now().strftime("%B %d, %Y")}</span>
        {''.join(meta_extras)}
    </div>

    <div class="metric-row">
        {kpi_html}
    </div>

    <div class="section-title">Executive Findings for This Page Group</div>
    <div class="two-col">
        <div class="panel panel-good">
            <div class="panel-head" style="color:#16a34a;">WHAT IS WORKING WELL</div>
            <ul>{w_list_html}</ul>
        </div>
        <div class="panel panel-fix">
            <div class="panel-head" style="color:#dc2626;">PRIORITY IMPROVEMENTS NEEDED</div>
            <ul>{f_list_html}</ul>
        </div>
    </div>

    <div class="section-title">Page-by-Page Audit &amp; Recommended Next Step</div>
    <table>
        <thead>
            <tr>
                <th style="width:45%;">Audited Page URL</th>
                <th style="width:11%; text-align:center;">Words</th>
                <th style="width:13%; text-align:center;">Schema</th>
                <th style="width:31%;">Recommended Action for This Page</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
    </table>
</body>
</html>"""
    return weasyprint.HTML(string=html_1p).write_pdf()


def build_executive_dossier_html(
    df_c, df_r, df_a, df_eq, df_can, df_ads,
    cwv_res, crux_res, sec_res, sb_res, kg_info, prev_snapshot,
    target_name, has_llms, logo_url, crawly_data=None,
    section_label="", client_name="",
    df_serp_ads=None, df_brand_serp=None, brand_sitelinks=None,
    cwv_desktop_res=None, df_cwv_batch=None, df_broken_links=None,
    robots_data=None, df_mentions=None, first_party_summary=None,
    df_gsc_quickwins=None, df_ga4_top_pages=None
):
    crawly_data = crawly_data or {}
    df_serp_ads = df_serp_ads if df_serp_ads is not None else pd.DataFrame()
    df_brand_serp = df_brand_serp if df_brand_serp is not None else pd.DataFrame()
    brand_sitelinks = brand_sitelinks or []
    cwv_desktop_res = cwv_desktop_res or {}
    df_cwv_batch = df_cwv_batch if df_cwv_batch is not None else pd.DataFrame()
    df_broken_links = df_broken_links if df_broken_links is not None else pd.DataFrame()
    robots_data = robots_data or {}
    df_mentions = df_mentions if df_mentions is not None else pd.DataFrame()
    first_party_summary = first_party_summary or []
    df_gsc_quickwins = df_gsc_quickwins if df_gsc_quickwins is not None else pd.DataFrame()
    df_ga4_top_pages = df_ga4_top_pages if df_ga4_top_pages is not None else pd.DataFrame()

    total_pages = len(df_c) if not df_c.empty else 0
    is_single_or_thin = (total_pages <= 2)
    clean_host = urlparse(target_name).netloc.replace("www.", "") or target_name
    brand_display = client_name.strip() if client_name and client_name.strip() else clean_host.split(".")[0].title()
    scope_title = detect_audit_scope_label(df_c, target_name, section_label)

    comm_schema_cnt = int(df_c["Commercial_Schema"].sum()) if (not df_c.empty and "Commercial_Schema" in df_c.columns) else 0
    comm_schema_pct = (comm_schema_cnt / total_pages * 100) if total_pages > 0 else 0.0
    missing_schema_pct = max(0.0, 100.0 - comm_schema_pct)

    thin_pages_df = df_c[df_c["Word_Count"] < 300] if (not df_c.empty and "Word_Count" in df_c.columns) else pd.DataFrame()
    thin_cnt = len(thin_pages_df)
    missing_meta_cnt = int(((df_c["Meta_Title_Status"] == "Missing") | (df_c["Meta_Desc_Status"] == "Missing")).sum()) if (not df_c.empty and "Meta_Title_Status" in df_c.columns) else 0
    mashed_count = int(df_c["Concatenated_H1"].sum()) if (not df_c.empty and "Concatenated_H1" in df_c.columns) else 0
    avg_a11y = round(df_c["A11y_Score"].mean()) if (not df_c.empty and "A11y_Score" in df_c.columns) else 80
    avg_aeo_ready = round(df_c["AEO_Readiness_Score"].mean()) if (not df_c.empty and "AEO_Readiness_Score" in df_c.columns) else 50
    total_imgs_cnt = int(df_c["Images_Count"].sum()) if (not df_c.empty and "Images_Count" in df_c.columns) else 0
    miss_alt_cnt = int(df_c["Missing_Alt_Count"].sum()) if (not df_c.empty and "Missing_Alt_Count" in df_c.columns) else 0

    tools_agg = {}
    stack_weaknesses_list = []
    seen_w_findings = set()
    if not df_c.empty and "Tech_Tools_JSON" in df_c.columns:
        for _, rt in df_c.iterrows():
            try:
                for it in json.loads(rt.get("Tech_Tools_JSON", "[]")):
                    tn = it["Tool"]
                    if tn not in tools_agg:
                        tools_agg[tn] = {**it, "Pages": 1}
                    else:
                        tools_agg[tn]["Pages"] += 1
            except Exception:
                pass
            try:
                for iw in json.loads(rt.get("Tech_Weaknesses_JSON", "[]")):
                    fk = iw.get("Finding", "")
                    if fk and fk not in seen_w_findings:
                        seen_w_findings.add(fk)
                        stack_weaknesses_list.append(iw)
            except Exception:
                pass

    valid_r = df_r[~df_r["Target_Rank"].astype(str).str.contains("Error|Timeout|Skipped", case=False, na=False)] if not df_r.empty else pd.DataFrame()
    has_serp = not valid_r.empty
    invis_pct = ((valid_r["Target_Rank"] == "> 20 (Absent)").sum() / len(valid_r) * 100) if has_serp else 0.0

    valid_a = df_a[~df_a["Target_Cited"].astype(str).str.contains("Error|Timeout|Skipped", case=False, na=False)] if not df_a.empty else pd.DataFrame()
    has_aeo = not valid_a.empty
    aeo_loss_pct = ((valid_a["Target_Cited"] == "🔴 Omitted").sum() / len(valid_a) * 100) if has_aeo else 0.0

    has_eq = (not df_eq.empty and "Inbound_Editorial_Links" in df_eq.columns)
    has_can = not df_can.empty
    dup_pairs_cnt = len(df_can) if has_can else 0

    has_broken = not df_broken_links.empty
    broken_only_cnt = int(df_broken_links["Status_Flag"].str.contains("Broken|Error", case=False, na=False).sum()) if (has_broken and "Status_Flag" in df_broken_links.columns) else 0

    has_brand_serp = not df_brand_serp.empty
    has_ads = not df_ads.empty
    has_serp_ads = not df_serp_ads.empty
    conquest_cnt = int(df_serp_ads["Competitor_Status"].str.contains("Conquesting", case=False, na=False).sum()) if (has_serp_ads and "Competitor_Status" in df_serp_ads.columns) else 0
    search_ads_cnt = int(df_ads["Channel"].str.contains("Search", case=False, na=False).sum()) if (has_ads and "Channel" in df_ads.columns) else 0

    raw_tbt = str(cwv_res.get("TBT", "N/A"))
    tbt_numeric = 0
    tbt_digits = "".join([c for c in raw_tbt.split(".")[0] if c.isdigit()])
    if tbt_digits:
        tbt_numeric = int(tbt_digits)
    tbt_is_good = (tbt_numeric > 0 and tbt_numeric <= 300)

    raw_lcp = str(cwv_res.get("LCP", "N/A"))
    lcp_numeric = 0.0
    if "s" in raw_lcp and "ms" not in raw_lcp:
        try:
            lcp_numeric = float(raw_lcp.replace("s", "").replace(",", "").strip())
        except Exception:
            lcp_numeric = 0.0

    has_cwv = bool(cwv_res and cwv_res.get("Score") not in (None, "N/A", ""))
    cwv_score = int(cwv_res.get("Score", 68)) if has_cwv else 68
    sec_grade = sec_res.get("Grade", "Verified")
    da_val = crawly_data.get("domain_authority", 0)
    ref_dom = crawly_data.get("referring_domains", 0)
    tot_bl = crawly_data.get("total_backlinks", 0)
    has_offpage = bool(da_val or not df_mentions.empty)
    has_first_party = bool(first_party_summary or not df_gsc_quickwins.empty or not df_ga4_top_pages.empty)

    base_seo = max(10, round(100 - ((invis_pct * 0.55 if has_serp else 20) + (thin_cnt / max(1, total_pages) * 25) + (missing_meta_cnt / max(1, total_pages) * 20))))
    score_seo = min(base_seo, 25) if is_single_or_thin else base_seo
    score_schema = round(comm_schema_pct)
    score_aeo = round((100 - aeo_loss_pct) * 0.6 + avg_aeo_ready * 0.4) if has_aeo else avg_aeo_ready
    if is_single_or_thin:
        score_aeo = min(score_aeo, 30)
    score_sec = 95 if "A" in str(sec_grade) else (75 if "B" in str(sec_grade) else 50)

    def badge_color(score):
        return "#16a34a" if score >= 85 else ("#f2a900" if score >= 50 else "#dc2626")

    progress_banner_html = ""
    if prev_snapshot:
        p_dt = esc(prev_snapshot.get("snapshot_date", "Baseline"))
        p_sch = float(prev_snapshot.get("commercial_schema_pct", 0))
        p_inv = float(prev_snapshot.get("invis_pct", 0))
        p_aeo = float(prev_snapshot.get("aeo_loss_pct", 0))
        progress_banner_html = f"""
        <div class="intro-note" style="border-left-color:#16a34a; background:#f0fdf4;">
            <strong>Progress Comparison vs. Baseline ({p_dt}):</strong>
            Schema Coverage: <strong>{p_sch:.0f}% &rarr; {comm_schema_pct:.0f}%</strong> ({'+' if comm_schema_pct >= p_sch else ''}{comm_schema_pct - p_sch:.0f}%)
            {' | Search Invisibility: <strong>' + f'{p_inv:.0f}% &rarr; {invis_pct:.0f}%</strong>' if has_serp else ''}
            {' | AI Answer Omission: <strong>' + f'{p_aeo:.0f}% &rarr; {aeo_loss_pct:.0f}%</strong>' if has_aeo else ''}
        </div>"""

    crawl_rows = ""
    if not df_c.empty:
        for _, r in df_c.head(16).iterrows():
            u_clean = clean_display_url(r["URL"])
            w_cnt = int(r.get("Word_Count", 0))
            has_comm = bool(r.get("Commercial_Schema", False))
            is_mashed = bool(r.get("Concatenated_H1", False))
            m_title_ok = (r.get("Meta_Title_Status", "") not in ("Missing", "Too Short (<30)"))
            m_desc_ok = (r.get("Meta_Desc_Status", "") not in ("Missing", "Too Short (<120)"))
            meta_lbl = "✓ Complete" if (m_title_ok and m_desc_ok) else ("Partial" if (m_title_ok or m_desc_ok) else "Missing")
            img_c = int(r.get("Images_Count", 0))
            miss_a = int(r.get("Missing_Alt_Count", 0))
            alt_lbl = f"{img_c - miss_a}/{img_c}" if img_c > 0 else "0 imgs"
            alt_col = "#16a34a" if miss_a == 0 else "#b45309"
            crawl_rows += f"""<tr>
                <td style="word-break:break-all; font-size:6.6pt; font-family:monospace;">{esc(u_clean)}</td>
                <td style="text-align:center;">{w_cnt:,}</td>
                <td style="text-align:center; color:{'#16a34a' if meta_lbl == '✓ Complete' else '#b45309'}; font-weight:bold;">{meta_lbl}</td>
                <td style="text-align:center; color:{'#dc2626' if is_mashed else '#16a34a'}; font-weight:bold;">{'Merged' if is_mashed else '✓ Clean'}</td>
                <td style="text-align:center; color:{alt_col}; font-weight:bold;">{alt_lbl}</td>
                <td style="text-align:center; color:{'#16a34a' if has_comm else '#dc2626'}; font-weight:bold;">{'✓ Active' if has_comm else 'Missing'}</td>
                <td style="text-align:center;">{int(r.get('A11y_Score', 80))}/100</td>
            </tr>"""

    tech_stack_html = ""
    if tools_agg:
        t_rows = ""
        for t_item in sorted(tools_agg.values(), key=lambda x: (-x["Pages"], x["Category"]))[:12]:
            t_rows += f"""<tr>
                <td style="font-weight:bold;">{esc(t_item['Tool'])}</td>
                <td>{esc(t_item['Category'])}</td>
                <td style="text-align:center;">{esc(t_item['Performance_Weight'])}</td>
                <td style="text-align:center;">{t_item['Pages']}/{total_pages}</td>
            </tr>"""
        tech_stack_html = f"""
        <div class="sub-header">Installed Technologies, Tag Managers &amp; Marketing Apps</div>
        <table>
            <thead>
                <tr>
                    <th style="width:34%;">Detected Tool / App</th>
                    <th style="width:32%;">Category</th>
                    <th style="width:18%; text-align:center;">Script Weight</th>
                    <th style="width:16%; text-align:center;">Pages Found</th>
                </tr>
            </thead>
            <tbody>{t_rows}</tbody>
        </table>"""

    ai_bots_row_html = ""
    if robots_data.get("ai_bots"):
        blocked_bots = [b["Crawler_Bot"] for b in robots_data["ai_bots"] if "Blocked" in b.get("Governance_Status", "")]
        bot_status_txt = f"🔴 Blocked: {', '.join(blocked_bots)}" if blocked_bots else "✓ Open to GPTBot, ClaudeBot, PerplexityBot & Google-Extended"
        ai_bots_row_html = f"""<tr>
            <td><strong>AI Search Crawler Permissions (<code>/robots.txt</code>)</strong></td>
            <td>{esc(bot_status_txt)}</td>
            <td>Controls whether AI search engines can index and quote your pages.</td>
        </tr>"""

    equity_rows = ""
    if has_eq:
        for _, r in df_eq.head(10).iterrows():
            u_clean = clean_display_url(r.get("Target_Commercial_URL", r.get("URL", "")))
            cnt = int(r.get("Inbound_Editorial_Links", r.get("Peer_Cross_Links", 0)))
            anch = str(r.get("Sample_Anchor_Texts", r.get("Cross_Link_Anchors", "None")))[:65]
            st_text = "Well Linked" if cnt >= 2 else ("Needs More Links (1)" if cnt == 1 else "Unlinked (0 Links)")
            st_col = "#16a34a" if cnt >= 2 else ("#b45309" if cnt == 1 else "#dc2626")
            equity_rows += f"""<tr>
                <td style="word-break:break-all; font-size:6.8pt; font-family:monospace;">{esc(u_clean)}</td>
                <td style="text-align:center; font-weight:bold;">{cnt}</td>
                <td style="font-size:6.8pt;">{esc(anch)}</td>
                <td style="text-align:center; font-weight:bold; color:{st_col};">{st_text}</td>
            </tr>"""

    can_rows = ""
    if has_can:
        for _, r in df_can.head(7).iterrows():
            u1 = clean_display_url(r["URL_1"])
            u2 = clean_display_url(r["URL_2"])
            sim_val = r["Cosine_Similarity"]
            can_rows += f"""<tr>
                <td style="word-break:break-all; font-size:6.7pt; font-family:monospace;">{esc(u1)}</td>
                <td style="word-break:break-all; font-size:6.7pt; font-family:monospace;">{esc(u2)}</td>
                <td style="text-align:center; font-weight:bold; color:#dc2626;">{int(float(sim_val)*100)}% Match</td>
                <td style="text-align:center;">Consolidate / Canonicalize</td>
            </tr>"""

    broken_rows = ""
    if has_broken:
        non_200 = df_broken_links[~df_broken_links["Status_Flag"].str.contains("Healthy", na=False)]
        target_brk_df = non_200 if not non_200.empty else df_broken_links
        for _, r in target_brk_df.head(8).iterrows():
            st_fl = str(r.get("Status_Flag", "")).replace("🔴 ", "").replace("⚠️ ", "").replace("🟡 ", "").replace("🟢 ", "")
            broken_rows += f"""<tr>
                <td style="font-weight:bold; color:{'#dc2626' if 'Broken' in st_fl or 'Error' in st_fl else '#b45309'};">{esc(st_fl)}</td>
                <td style="word-break:break-all; font-size:6.6pt; font-family:monospace;">{esc(clean_display_url(r.get('Target_URL', '')))}</td>
                <td style="font-size:6.7pt;">{esc(r.get('Anchor_Text', ''))}</td>
                <td style="word-break:break-all; font-size:6.6pt; font-family:monospace;">{esc(clean_display_url(r.get('Found_On_Page', '')))}</td>
            </tr>"""

    brand_serp_rows = ""
    if has_brand_serp:
        for _, r in df_brand_serp.head(8).iterrows():
            own_lbl = str(r.get("Ownership", "")).replace("🟢 ", "").replace("🌐 ", "")
            is_own_b = "Your Website" in own_lbl
            brand_serp_rows += f"""<tr>
                <td style="text-align:center; font-weight:bold;">{esc(r.get('SERP_Pos', ''))}</td>
                <td style="font-weight:bold; color:{'#16a34a' if is_own_b else '#b45309'};">{esc(own_lbl)}</td>
                <td style="font-size:6.8pt;">{esc(r.get('Page_Title_Shown_in_Google', ''))}</td>
                <td style="word-break:break-all; font-size:6.6pt; font-family:monospace;">{esc(r.get('Displayed_URL', ''))}</td>
            </tr>"""

    serp_rows = ""
    sov_rows_html = ""
    if has_serp:
        for _, r in valid_r.head(12).iterrows():
            rk = str(r["Target_Rank"])
            r_url = clean_display_url(r["Ranking_URL"])
            comp = esc(str(r["Top_Ranking_Competitor"]))
            is_vis = rk.startswith("#")
            serp_rows += f"""<tr>
                <td style="font-weight:bold;">{esc(r['Keyword'])}</td>
                <td style="text-align:center; font-weight:bold; color:{'#16a34a' if is_vis else '#dc2626'};">{esc(rk)}</td>
                <td style="word-break:break-all; font-size:6.7pt; font-family:monospace;">{esc(r_url)}</td>
                <td style="text-align:center;">{comp}</td>
            </tr>"""
        if "Top_10_JSON" in valid_r.columns:
            sov_map = {}
            tot_v = max(1, len(valid_r))
            for _, rs in valid_r.iterrows():
                try:
                    seen_q = set()
                    for item in json.loads(rs.get("Top_10_JSON", "[]")):
                        dn = item.get("domain", "")
                        if dn and dn not in seen_q:
                            seen_q.add(dn)
                            sov_map.setdefault(dn, []).append(item.get("rank", 10))
                except Exception:
                    pass
            for dn, rks in sorted(sov_map.items(), key=lambda kv: (-len(kv[1]), sum(kv[1])/len(kv[1])))[:6]:
                is_me = (clean_host.lower() in dn.lower())
                sov_rows_html += f"""<tr>
                    <td style="font-weight:bold; color:{'#16a34a' if is_me else '#1e293b'};">{esc(dn)} {'(Your Domain)' if is_me else ''}</td>
                    <td style="text-align:center;">{len(rks)} / {tot_v}</td>
                    <td style="text-align:center; font-weight:bold;">{round(len(rks)/tot_v*100, 1)}%</td>
                    <td style="text-align:center;">#{round(sum(rks)/len(rks), 1)}</td>
                </tr>"""

    aeo_rows = ""
    aeo_pr_rows = ""
    if has_aeo:
        cited_counter = Counter()
        for _, r in valid_a.head(10).iterrows():
            cited_flag = "Yes" in str(r["Target_Cited"])
            sources_txt = str(r.get("Cited_Competitors", "None"))
            for d_it in [x.strip() for x in sources_txt.split(",") if x.strip() and "." in x and "Summary" not in x]:
                cited_counter[d_it] += 1
            if sources_txt in ("None", "N/A", ""):
                sources_txt = "AI Overview Summary (No External Links)"
            aeo_rows += f"""<tr>
                <td>{esc(r['Buyer_Query'])}</td>
                <td style="text-align:center;">{esc(r['AI_Overview_Generated'])}</td>
                <td style="text-align:center; font-weight:bold; color:{'#16a34a' if cited_flag else '#dc2626'};">{'✓ Cited' if cited_flag else 'Not Cited'}</td>
                <td style="font-size:6.8pt;">{esc(sources_txt)}</td>
            </tr>"""
        for dom_k, cnt_k in cited_counter.most_common(5):
            aeo_pr_rows += f"""<tr>
                <td style="font-weight:bold;">{esc(dom_k)}</td>
                <td style="text-align:center;">{cnt_k} Prompt(s)</td>
                <td>Secure editorial mention, review profile, or citation on this source</td>
            </tr>"""

    ads_rows = ""
    if has_ads:
        grouped_ads = {}
        for _, r in df_ads.iterrows():
            ch_lbl = str(r.get("Channel", r.get("Type", "Google Search Text Ad"))).replace("🔍 ", "").replace("🖼️ ", "").replace("🎬 ", "")
            f_dt = str(r.get("First_Shown", "N/A"))
            l_dt = str(r.get("Last_Shown", "N/A"))
            days_v = int(r.get("Days_Active", 1))
            tier_lbl = str(r.get("Longevity_Tier", "Active Ad")).replace("🔥 ", "").replace("🟢 ", "").replace("🧪 ", "")
            key = (ch_lbl, f_dt, l_dt, days_v, tier_lbl)
            grouped_ads[key] = grouped_ads.get(key, 0) + 1

        for (ch_lbl, f_dt, l_dt, days_v, tier_lbl), count_ads in list(grouped_ads.items())[:8]:
            count_prefix = f"{count_ads}x " if count_ads > 1 else "1x "
            takeaway = (
                f"Long-running control ad ({days_v} days active) — indicates consistent conversion performance."
                if days_v >= 60 else f"Recent creative test ({days_v} days active)."
            )
            ads_rows += f"""<tr>
                <td style="font-weight:bold;">{esc(count_prefix + ch_lbl)}</td>
                <td>{days_v} Days ({esc(f_dt)} to {esc(l_dt)})</td>
                <td style="font-weight:bold; color:{'#16a34a' if days_v >= 60 else '#004b93'};">{esc(tier_lbl)}</td>
                <td style="font-size:6.8pt;">{esc(takeaway)}</td>
            </tr>"""

    serp_ads_rows = ""
    if has_serp_ads:
        for _, r in df_serp_ads.head(8).iterrows():
            st_c = str(r.get("Competitor_Status", "")).replace("🟢 ", "").replace("🚨 ", "").replace("⚔️ ", "")
            is_alert = "Conquesting" in st_c
            serp_ads_rows += f"""<tr>
                <td style="font-weight:bold;">{esc(r.get('Search_Query', ''))}</td>
                <td style="font-weight:bold; color:{'#dc2626' if is_alert else '#004b93'};">{esc(st_c)}</td>
                <td>{esc(r.get('Bidding_Domain', ''))}</td>
                <td style="font-size:6.7pt;">{esc(r.get('Live_Ad_Headline', ''))}</td>
            </tr>"""

    strengths = []
    weaknesses = []
    opportunities = []
    threats = []
    roadmap = []

    if total_pages >= 5:
        strengths.append(f"<strong>Dedicated Page Structure ({total_pages} Pages):</strong> Separate URLs allow each service or topic to be indexed independently.")
    else:
        weaknesses.append(f"<strong>Limited Page Count ({total_pages} Page(s)):</strong> A small page footprint limits how many distinct search queries the site can rank for.")
        opportunities.append("<strong>Build Dedicated Service Pages:</strong> Create separate landing pages for each core offering and buyer question.")
        roadmap.append(("Priority 1 (High Impact)", "Expand Page Architecture", f"Add dedicated URLs for each core service beyond the {total_pages} page(s) reviewed.", "Allows Google to rank individual services separately"))

    if comm_schema_pct >= 80:
        strengths.append(f"<strong>Strong Structured Data Coverage ({comm_schema_pct:.0f}%):</strong> Most pages include product, service, or FAQ schema labels.")
    elif comm_schema_pct > 0:
        weaknesses.append(f"<strong>Partial Schema Coverage ({comm_schema_pct:.0f}% Declared / {missing_schema_pct:.0f}% Missing):</strong> Only {comm_schema_cnt} of {total_pages} pages include product, service, or FAQ structured data.")
        opportunities.append("<strong>Complete Schema Rollout:</strong> Add Product, Service, and FAQPage JSON-LD tags to the remaining unmarked pages.")
        roadmap.append(("Priority 1 (High Impact)", "Add Product & FAQ Structured Data", f"Add Product/Service and FAQPage schema to the {total_pages - comm_schema_cnt} unmarked pages in this batch.", "Improves search appearance and AI answer eligibility"))
    else:
        weaknesses.append(f"<strong>Missing Product &amp; FAQ Schema (0 of {total_pages} Pages):</strong> Pages lack structured labels telling Google and AI tools what is being offered.")
        opportunities.append("<strong>Deploy Product, Service &amp; FAQ Schema:</strong> Add structured JSON-LD tags so search engines clearly understand your offerings.")
        roadmap.append(("Priority 1 (High Impact)", "Deploy Product & FAQ Structured Data", "Add Product, Service, and FAQPage JSON-LD tags across all audited pages.", "Helps Google and AI tools extract accurate service details"))

    if thin_cnt > 0:
        sample_thin_list = ", ".join([esc(urlparse(u).path or "/") for u in thin_pages_df["URL"].head(2)])
        weaknesses.append(f"<strong>Thin Content on {thin_cnt} Page(s) (&lt;300 Words):</strong> Pages such as <code>{sample_thin_list}</code> lack sufficient explanatory text to rank competitively.")
        opportunities.append("<strong>Enrich Thin Page Copy:</strong> Expand short pages to 500+ words with clear benefits, process steps, and buyer FAQs.")
        roadmap.append(("Priority 1 (High Impact)", "Expand Thin Page Copy", f"Add helpful descriptions, specifications, and FAQs to the {thin_cnt} pages under 300 words.", "Improves keyword relevance and organic rankings"))

    if broken_only_cnt > 0:
        weaknesses.append(f"<strong>Broken Internal Links ({broken_only_cnt} Dead Target(s)):</strong> Internal links pointing to 404/unreachable pages hurt user experience and crawl efficiency.")
        roadmap.append(("Priority 1 (Quick Fix)", "Repair Broken Internal Links", f"Update or redirect the {broken_only_cnt} broken internal link targets identified in the link audit.", "Restores seamless navigation and link equity"))

    if has_can and dup_pairs_cnt > 0:
        weaknesses.append(f"<strong>Duplicate Page Copy ({dup_pairs_cnt} High-Overlap Pair(s)):</strong> Multiple URLs share near-identical text, causing them to compete against each other in Google.")
        opportunities.append("<strong>Consolidate Duplicate Variants:</strong> Use canonical tags or combine near-identical product variations into unified pages.")
        roadmap.append(("Priority 1 (High Impact)", "Resolve Duplicate Page Copy", f"Consolidate or set canonical tags across the {dup_pairs_cnt} duplicate URL pairs identified.", "Concentrates ranking authority onto primary pages"))

    if mashed_count > 0:
        mashed_sample = ", ".join([esc(urlparse(u).path or "/") for u in df_c[df_c["Concatenated_H1"]]["URL"].head(2)])
        weaknesses.append(f"<strong>Merged Headline Words ({mashed_count} Page(s)):</strong> Styling on <code>{mashed_sample}</code> joins words without spaces inside the main H1 headline.")
        roadmap.append(("Priority 1 (Quick Fix)", "Fix H1 Headline Spacing", f"Add spaces between styled text spans in the H1 template on {mashed_count} page(s).", "Ensures Google reads headline keywords cleanly"))
    else:
        strengths.append("<strong>Clean Page Headlines:</strong> All main H1 headings are properly spaced and readable.")

    df_u_dos = analyze_url_structure_and_keywords(df_c)
    u_fix_dos = df_u_dos[df_u_dos["Migration_Action"] != "No Change Required"] if not df_u_dos.empty else pd.DataFrame()
    url_structure_pdf_html = ""
    if not u_fix_dos.empty:
        weaknesses.append(f"<strong>Suboptimal URL Structure on {len(u_fix_dos)} Page(s):</strong> Redundant folders (e.g. <code>/blogs/blog/...</code>) or unoptimized slugs dilute keyword focus.")
        roadmap.append(("Priority 2 (URL Hygiene)", "Streamline URL Slugs & 301 Redirects", f"Restructure {len(u_fix_dos)} suboptimal URL path(s) to clean keyword slugs with 301 redirects.", "Improves crawl clarity, keyword alignment, and SERP CTR"))
        u_rows_pdf = ""
        for _, ru in u_fix_dos.head(8).iterrows():
            iss_clean = str(ru.get("Structural_Issues", "")).replace("🔴 ", "").replace("🟡 ", "").replace("🟠 ", "").replace("⚠️ ", "")
            u_rows_pdf += f"""<tr>
                <td style="word-break:break-all; font-size:6.4pt; font-family:monospace;">{esc(clean_display_url(ru.get('Current_URL', '')))}</td>
                <td style="font-size:6.5pt;">{esc(iss_clean)}</td>
                <td style="word-break:break-all; font-size:6.4pt; font-family:monospace; color:#16a34a; font-weight:bold;">{esc(clean_display_url(ru.get('Recommended_Clean_URL', '')))}</td>
                <td style="font-size:6.4pt;">{esc(ru.get('Recommended_Target_Keywords', ''))}</td>
            </tr>"""
        url_structure_pdf_html = f"""
        <div class="sub-header">URL Structure, Folder Hygiene &amp; Keyword Optimization Review</div>
        <p class="intro-note">Auditing URL paths for redundant folders (such as <code>/blogs/blog/...</code>), CMS prefixes, and slug-to-topic keyword alignment. Always deploy 301 redirects when updating live URLs.</p>
        <table>
            <thead>
                <tr>
                    <th style="width:27%;">Current URL</th>
                    <th style="width:25%;">Detected URL Finding</th>
                    <th style="width:26%;">Recommended Clean URL</th>
                    <th style="width:22%;">Target Keywords</th>
                </tr>
            </thead>
            <tbody>{u_rows_pdf}</tbody>
        </table>"""

    if miss_alt_cnt > 0:
        weaknesses.append(f"<strong>Missing Image Alt Text ({miss_alt_cnt} of {total_imgs_cnt} Images):</strong> Images without alt descriptions miss image search traffic and reduce screen-reader accessibility.")
        roadmap.append(("Priority 2 (SEO & WCAG)", "Add Descriptive Image Alt Text", f"Populate descriptive alt attributes across the {miss_alt_cnt} untagged images.", "Improves Google Image indexing and WCAG compliance"))

    for sw in stack_weaknesses_list[:2]:
        weaknesses.append(f"<strong>Tech Stack Finding ({esc(sw.get('Area', '').replace('⚡ ', '').replace('📊 ', '').replace('📈 ', '').replace('🎯 ', '').replace('🔍 ', '').replace('🤖 ', '').replace('🛡️ ', ''))}):</strong> {esc(sw.get('Finding', ''))}")
        roadmap.append(("Priority 2 (Tech Stack)", esc(sw.get("Area", "Stack Optimization").split(" ", 1)[-1]), esc(sw.get("Recommendation", "")), "Improves tracking accuracy and page speed"))

    if has_llms:
        strengths.append("<strong>AI Directory Published (<code>/llms.txt</code>):</strong> Active <code>llms.txt</code> file helps ChatGPT and Perplexity index your offerings.")
    else:
        weaknesses.append("<strong>Missing <code>/llms.txt</code> File:</strong> Publishing a plain-text <code>llms.txt</code> guide helps AI search engines discover your key pages.")
        opportunities.append("<strong>Publish <code>/llms.txt</code> at Domain Root:</strong> Upload the generated <code>llms.txt</code> file from Tab 1.")

    if conquest_cnt > 0:
        threats.append(f"<strong>Competitors Bidding on Your Brand ({conquest_cnt} Paid Ad(s)):</strong> Rivals are running sponsored ads on your branded Google searches.")
        roadmap.append(("Priority 1 (Paid Defense)", "Protect Branded Search Terms", "Run exact-match brand defense ads with sitelinks to prevent competitors from intercepting high-intent visitors.", "Protects branded conversion traffic"))

    if has_serp and invis_pct > 40:
        weaknesses.append(f"<strong>Low Non-Branded Google Visibility ({invis_pct:.0f}% Off Page 1–2):</strong> Buyers searching general industry terms frequently see competitors before {esc(brand_display)}.")
        threats.append(f"<strong>Competitors Capturing Non-Branded Searches:</strong> Rivals rank ahead on {invis_pct:.0f}% of the commercial queries tested.")

    if has_aeo and aeo_loss_pct > 50:
        threats.append(f"<strong>Low AI Overview Citation Rate ({aeo_loss_pct:.0f}% Omitted):</strong> Conversational AI answers rarely cite {esc(brand_display)} directly.")
        opportunities.append("<strong>Add Direct Q&amp;A Sections:</strong> Place concise 40-word answers to common buyer questions on key pages to earn AI citations.")
        roadmap.append(("Priority 2 (Growth)", "Add Direct Buyer Q&A Blocks", "Open key service pages with a clear summary definition and 3–5 answered FAQs.", "Increases brand citations in Google AI Overviews & ChatGPT"))

    if has_ads and search_ads_cnt > 0:
        strengths.append(f"<strong>Active Google Search Ads ({search_ads_cnt} Text Ads):</strong> Paid search is actively capturing bottom-of-funnel buyer queries.")
        opportunities.append("<strong>Refresh Long-Running Ad Copy:</strong> Test new ad variations alongside evergreen search ads to improve click-through rates.")
    else:
        opportunities.append("<strong>Capture High-Intent Search Demand:</strong> Run targeted Google Search Ads on commercial buyer terms while organic rankings grow.")

    if has_cwv and lcp_numeric > 3.0:
        weaknesses.append(f"<strong>Slow Mobile Visual Load ({esc(raw_lcp)}):</strong> Large hero images delay mobile page rendering beyond the 2.5-second benchmark.")
        opportunities.append("<strong>Compress Mobile Hero Images:</strong> Convert large images to WebP format and defer non-essential scripts.")
        roadmap.append(("Priority 2 (Performance)", "Speed Up Mobile Page Load", f"Compress hero images and defer heavy scripts to cut mobile load time from {esc(raw_lcp)} to under 2.5s.", "Reduces mobile bounce rate and improves user experience"))

    if "A" in str(sec_grade):
        strengths.append(f"<strong>Strong Website Security ({esc(sec_grade)}):</strong> HTTPS transport security and safe browsing checks passed.")

    if avg_a11y < 85:
        roadmap.append(("Priority 3 (Compliance)", "Improve Page Accessibility (WCAG)", 'Ensure &lt;html lang="en"&gt; is set, add descriptive image alt text, and label buttons.', "Improves usability for screen readers and search quality"))

    w_html = "".join([f"<li>{x}</li>" for x in weaknesses[:6]])
    o_html = "".join([f"<li>{x}</li>" for x in opportunities[:5]])
    s_html = "".join([f"<li>{x}</li>" for x in strengths[:5]])
    t_html = "".join([f"<li>{x}</li>" for x in threats[:4]]) or "<li><strong>Competitive Search Pressure:</strong> Established competitors invest heavily in educational content and structured data.</li>"

    roadmap_rows = ""
    for prio, init_name, act_txt, imp_txt in roadmap[:8]:
        p_col = "#dc2626" if "Priority 1" in prio else ("#004b93" if "Priority 2" in prio else "#16a34a")
        roadmap_rows += f"""<tr>
            <td style="color:{p_col}; font-weight:bold;">{esc(prio)}</td>
            <td style="font-weight:bold;">{esc(init_name)}</td>
            <td>{act_txt}</td>
            <td>{esc(imp_txt)}</td>
        </tr>"""

    conditional_sections_html = ""

    if has_eq or has_can or has_broken:
        eq_block = ""
        if has_eq:
            eq_block = f"""
            <div class="sub-header">Internal Link Flow Across Reviewed Pages</div>
            <table>
                <thead>
                    <tr>
                        <th style="width:44%;">Page URL</th>
                        <th style="width:14%; text-align:center;">Inbound Links</th>
                        <th style="width:26%;">Sample Link Text</th>
                        <th style="width:16%; text-align:center;">Link Status</th>
                    </tr>
                </thead>
                <tbody>{equity_rows}</tbody>
            </table>"""

        can_block = ""
        if has_can:
            can_block = f"""
            <div class="sub-header">Duplicate &amp; Overlapping Page Copy Detected</div>
            <p class="intro-note">Pages with 60%+ identical copy compete against each other in Google search results. Consolidating variants or adding canonical tags resolves this overlap.</p>
            <table>
                <thead>
                    <tr>
                        <th style="width:36%;">Primary Page URL</th>
                        <th style="width:36%;">Overlapping Page URL</th>
                        <th style="width:12%; text-align:center;">Copy Overlap</th>
                        <th style="width:16%; text-align:center;">Recommended Fix</th>
                    </tr>
                </thead>
                <tbody>{can_rows}</tbody>
            </table>"""

        brk_block = ""
        if has_broken:
            brk_block = f"""
            <div class="sub-header">Internal Link Health &amp; Redirect Diagnostics</div>
            <table>
                <thead>
                    <tr>
                        <th style="width:24%;">Link Status</th>
                        <th style="width:32%;">Target URL</th>
                        <th style="width:18%;">Anchor Text</th>
                        <th style="width:26%;">Found On Page</th>
                    </tr>
                </thead>
                <tbody>{broken_rows}</tbody>
            </table>"""

        conditional_sections_html += f"""
        <div class="page-break"></div>
        <div class="section-header">Internal Linking, Redirects &amp; Content Duplication Review</div>
        <p class="intro-note">Reviewing how internal links connect your pages, checking for broken destinations, and identifying duplicate copy that can dilute search rankings.</p>
        {eq_block}
        {can_block}
        {brk_block}
        """

    if has_brand_serp or has_serp or has_aeo or has_ads or has_serp_ads:
        brand_serp_block = ""
        if has_brand_serp:
            sl_note = ""
            if brand_sitelinks:
                sl_titles_str = ", ".join([esc(s.get("Sitelink_Title", "")) for s in brand_sitelinks[:6] if s.get("Sitelink_Title")])
                sl_note = f'<p class="intro-note"><strong>Most Valuable Pages Highlighted by Google (Branded Sitelinks):</strong> {sl_titles_str}</p>'
            brand_serp_block = f"""
            <div class="sub-header">Branded Google Search Results &amp; Top Entry Pages</div>
            {sl_note}
            <table>
                <thead>
                    <tr>
                        <th style="width:10%; text-align:center;">Rank</th>
                        <th style="width:20%;">Ownership</th>
                        <th style="width:36%;">Title Shown in Google</th>
                        <th style="width:34%;">Displayed URL</th>
                    </tr>
                </thead>
                <tbody>{brand_serp_rows}</tbody>
            </table>"""

        serp_block = ""
        if has_serp:
            sov_table = ""
            if sov_rows_html:
                sov_table = f"""
                <div class="sub-header">Page-1 Competitor Share-of-Voice (SOV) Leaderboard</div>
                <table>
                    <thead>
                        <tr>
                            <th style="width:44%;">Domain</th>
                            <th style="width:18%; text-align:center;">Page-1 Hits</th>
                            <th style="width:20%; text-align:center;">Share of Voice</th>
                            <th style="width:18%; text-align:center;">Avg. Position</th>
                        </tr>
                    </thead>
                    <tbody>{sov_rows_html}</tbody>
                </table>"""
            serp_block = f"""
            <div class="sub-header">Non-Branded Google Search Rankings (US Index — {len(valid_r)} Queries)</div>
            <table>
                <thead>
                    <tr>
                        <th style="width:34%;">Search Query</th>
                        <th style="width:14%; text-align:center;">Current Rank</th>
                        <th style="width:32%;">Ranking Page URL</th>
                        <th style="width:20%; text-align:center;">#1 Page Leader</th>
                    </tr>
                </thead>
                <tbody>{serp_rows}</tbody>
            </table>
            {sov_table}"""

        aeo_block = ""
        if has_aeo:
            pr_sub = ""
            if aeo_pr_rows:
                pr_sub = f"""
                <div class="sub-header">Top Third-Party Sources Cited by AI (Digital PR Targets)</div>
                <table>
                    <thead>
                        <tr>
                            <th style="width:34%;">Cited Source Domain</th>
                            <th style="width:18%; text-align:center;">AI Citations</th>
                            <th style="width:48%;">Recommended Outreach Action</th>
                        </tr>
                    </thead>
                    <tbody>{aeo_pr_rows}</tbody>
                </table>"""
            aeo_block = f"""
            <div class="sub-header">Generative AI Answer Visibility (Google AI Overviews — {len(valid_a)} Prompts)</div>
            <table>
                <thead>
                    <tr>
                        <th style="width:42%;">Buyer Question Prompt</th>
                        <th style="width:14%; text-align:center;">AI Overview?</th>
                        <th style="width:14%; text-align:center;">Brand Cited?</th>
                        <th style="width:30%;">Sources Referenced</th>
                    </tr>
                </thead>
                <tbody>{aeo_rows}</tbody>
            </table>
            {pr_sub}"""

        ads_block = ""
        if has_ads or has_serp_ads:
            trans_table = ""
            if has_ads:
                trans_table = f"""
                <div class="sub-header">Active Google Ads Portfolio Summary ({len(df_ads)} Tracked Creatives)</div>
                <table>
                    <thead>
                        <tr>
                            <th style="width:26%;">Ad Channel &amp; Format</th>
                            <th style="width:26%;">Active Flight &amp; Dates</th>
                            <th style="width:20%;">Longevity Tier</th>
                            <th style="width:28%;">Performance Insight</th>
                        </tr>
                    </thead>
                    <tbody>{ads_rows}</tbody>
                </table>"""
            conq_table = ""
            if has_serp_ads:
                conq_table = f"""
                <div class="sub-header">Live Google Search Sponsored Ads &amp; Competitor Conquesting</div>
                <table>
                    <thead>
                        <tr>
                            <th style="width:24%;">Search Query</th>
                            <th style="width:24%;">Status</th>
                            <th style="width:20%;">Advertiser</th>
                            <th style="width:32%;">Live Ad Headline</th>
                        </tr>
                    </thead>
                    <tbody>{serp_ads_rows}</tbody>
                </table>"""
            ads_block = trans_table + conq_table

        conditional_sections_html += f"""
        <div class="page-break"></div>
        <div class="section-header">Search Visibility, AI Answers &amp; Paid Media Presence</div>
        <p class="intro-note">Evaluating how prospective customers discover {esc(brand_display)} across branded and non-branded Google searches, conversational AI summaries, and paid search placements.</p>
        {brand_serp_block}
        {serp_block}
        {aeo_block}
        {ads_block}
        """

    if has_offpage or has_first_party:
        offpage_block = ""
        if has_offpage:
            mentions_table = ""
            if not df_mentions.empty:
                m_rows = ""
                for _, rm in df_mentions.head(6).iterrows():
                    m_rows += f"""<tr>
                        <td style="font-weight:bold;">{esc(rm.get('Source_Website', ''))}</td>
                        <td style="font-size:6.8pt;">{esc(rm.get('Article_Title', ''))}</td>
                        <td style="word-break:break-all; font-size:6.5pt; font-family:monospace;">{esc(clean_display_url(rm.get('Target_Outreach_URL', '')))}</td>
                    </tr>"""
                mentions_table = f"""
                <div class="sub-header">Unlinked Brand Mentions Found on the Web (Link Reclamation Targets)</div>
                <table>
                    <thead>
                        <tr>
                            <th style="width:26%;">Website</th>
                            <th style="width:38%;">Article / Page Title</th>
                            <th style="width:36%;">Outreach URL</th>
                        </tr>
                    </thead>
                    <tbody>{m_rows}</tbody>
                </table>"""
            offpage_block = f"""
            <div class="sub-header">Domain Authority &amp; Backlink Profile (Crawly Index)</div>
            <table>
                <thead>
                    <tr>
                        <th style="width:25%;">Domain Authority</th>
                        <th style="width:25%;">Referring Websites</th>
                        <th style="width:25%;">Total Backlinks</th>
                        <th style="width:25%;">Authority Tier</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td style="font-weight:bold; color:#004b93;">{da_val} / 100</td>
                        <td>{ref_dom:,}</td>
                        <td>{tot_bl:,}</td>
                        <td>{esc(crawly_data.get('authority_tier', 'Evaluated'))}</td>
                    </tr>
                </tbody>
            </table>
            {mentions_table}"""

        fp_block = ""
        if has_first_party:
            fp_rows = ""
            for item_fp in first_party_summary:
                fp_rows += f"""<tr>
                    <td style="font-weight:bold;">{esc(item_fp.get('Source', ''))}</td>
                    <td>{esc(item_fp.get('Metric_1', ''))}</td>
                    <td>{esc(item_fp.get('Metric_2', ''))}</td>
                    <td>{esc(item_fp.get('Metric_3', ''))}</td>
                </tr>"""
            qw_table = ""
            if not df_gsc_quickwins.empty:
                qw_r = ""
                cols_qw = list(df_gsc_quickwins.columns)
                for _, rqw in df_gsc_quickwins.head(8).iterrows():
                    qw_r += f"""<tr>
                        <td style="font-weight:bold;">{esc(rqw[cols_qw[0]])}</td>
                        <td style="text-align:center;">{esc(rqw[cols_qw[1]])}</td>
                        <td style="text-align:center;">{esc(rqw[cols_qw[2]])}</td>
                        <td style="text-align:center;">{esc(rqw[cols_qw[3]])}</td>
                    </tr>"""
                qw_table = f"""
                <div class="sub-header">First-Party Search Console Page-2 Quick-Win Keywords</div>
                <table>
                    <thead>
                        <tr>
                            <th style="width:46%;">Search Query</th>
                            <th style="width:18%; text-align:center;">Clicks</th>
                            <th style="width:18%; text-align:center;">Impressions</th>
                            <th style="width:18%; text-align:center;">Avg. Position</th>
                        </tr>
                    </thead>
                    <tbody>{qw_r}</tbody>
                </table>"""
            fp_block = f"""
            <div class="sub-header">First-Party Analytics Summary (Search Console / GA4)</div>
            <table>
                <thead>
                    <tr>
                        <th style="width:28%;">Data Source</th>
                        <th style="width:24%;">Primary Volume</th>
                        <th style="width:24%;">Secondary Metric</th>
                        <th style="width:24%;">Performance Note</th>
                    </tr>
                </thead>
                <tbody>{fp_rows}</tbody>
            </table>
            {qw_table}"""

        conditional_sections_html += f"""
        <div class="page-break"></div>
        <div class="section-header">Off-Page Authority &amp; First-Party Performance Data</div>
        <p class="intro-note">External backlink authority, unlinked brand mentions, and first-party traffic data uploaded for {esc(brand_display)}.</p>
        {offpage_block}
        {fp_block}
        """

    cwv_block = ""
    if has_cwv or not df_cwv_batch.empty:
        single_cwv_html = ""
        if has_cwv:
            desk_row = ""
            if cwv_desktop_res and "Score" in cwv_desktop_res:
                d_sc = int(cwv_desktop_res.get("Score", 0))
                desk_row = f"""
                <tr>
                    <td><strong>Desktop Performance Score (Same URL)</strong></td>
                    <td style="text-align:center; font-weight:bold; color:{badge_color(d_sc)};">{d_sc} / 100</td>
                    <td style="text-align:center;">&gt; 90 / 100</td>
                    <td>Desktop LCP: {esc(cwv_desktop_res.get('LCP', 'N/A'))} | TBT: {esc(cwv_desktop_res.get('TBT', 'N/A'))}</td>
                </tr>"""
            single_cwv_html = f"""
            <div class="sub-header">Mobile &amp; Desktop Speed (Single-Page Audit: {esc(clean_display_url(cwv_res.get("Tested_URL", target_name)))})</div>
            <p class="intro-note">Note: Lab Core Web Vitals (Performance Score, LCP, TBT, and CLS) measure the specific page URL listed above rather than every page across the site.</p>
            <table>
                <thead>
                    <tr>
                        <th style="width:28%;">Speed Metric</th>
                        <th style="width:20%; text-align:center;">Measured Value</th>
                        <th style="width:20%; text-align:center;">Target Benchmark</th>
                        <th style="width:32%;">What This Means for Visitors</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><strong>Mobile Performance Score</strong></td>
                        <td style="text-align:center; font-weight:bold; color:{badge_color(cwv_score)};">{cwv_score} / 100</td>
                        <td style="text-align:center;">&gt; 90 / 100</td>
                        <td>{'Fast mobile experience.' if cwv_score >= 85 else 'Visual load delays are slowing mobile visitors.'}</td>
                    </tr>
                    {desk_row}
                    <tr>
                        <td><strong>Main Visual Load Time (Mobile LCP)</strong></td>
                        <td style="text-align:center; font-weight:bold; color:{'#16a34a' if lcp_numeric <= 2.5 else '#dc2626'};">{esc(raw_lcp)}</td>
                        <td style="text-align:center;">&lt; 2.5 seconds</td>
                        <td>{'Main banner loads quickly.' if lcp_numeric <= 2.5 else 'Visitors wait too long for the main image/headline to appear.'}</td>
                    </tr>
                    <tr>
                        <td><strong>Script Responsiveness (Mobile TBT)</strong></td>
                        <td style="text-align:center; font-weight:bold; color:{'#16a34a' if tbt_is_good else '#b45309'};">{esc(raw_tbt)}</td>
                        <td style="text-align:center;">&lt; 300 ms</td>
                        <td>{'Page responds smoothly to taps.' if tbt_is_good else 'Background tracking scripts slightly delay tap responsiveness.'}</td>
                    </tr>
                    <tr>
                        <td><strong>Visual Layout Stability (CLS)</strong></td>
                        <td style="text-align:center; font-weight:bold; color:#16a34a;">{esc(cwv_res.get('CLS', '0.000'))}</td>
                        <td style="text-align:center;">&lt; 0.100</td>
                        <td>Page elements stay steady without jumping during load.</td>
                    </tr>
                </tbody>
            </table>"""

        batch_cwv_html = ""
        if not df_cwv_batch.empty:
            b_rows = ""
            for _, rb in df_cwv_batch.iterrows():
                ms = int(rb.get("Mobile_Score", 0))
                b_rows += f"""<tr>
                    <td style="word-break:break-all; font-size:6.6pt; font-family:monospace;">{esc(clean_display_url(rb.get('Tested_Template_URL', '')))}</td>
                    <td style="text-align:center; font-weight:bold; color:{badge_color(ms)};">{ms}/100</td>
                    <td style="text-align:center;">{esc(rb.get('LCP', 'N/A'))}</td>
                    <td style="text-align:center;">{esc(rb.get('TBT', 'N/A'))}</td>
                    <td style="text-align:center;">{esc(rb.get('CLS', 'N/A'))}</td>
                </tr>"""
            batch_cwv_html = f"""
            <div class="sub-header">Multi-Template Mobile Speed Comparison</div>
            <table>
                <thead>
                    <tr>
                        <th style="width:44%;">Tested Page Template URL</th>
                        <th style="width:14%; text-align:center;">Score</th>
                        <th style="width:14%; text-align:center;">LCP</th>
                        <th style="width:14%; text-align:center;">TBT</th>
                        <th style="width:14%; text-align:center;">CLS</th>
                    </tr>
                </thead>
                <tbody>{b_rows}</tbody>
            </table>"""
        cwv_block = single_cwv_html + batch_cwv_html

    summary_rows = f"""
        <tr>
            <td><strong>Pages Reviewed in This Audit</strong></td>
            <td>{total_pages} Page(s)</td>
            <td>Dedicated Service URLs</td>
            <td style="color:{'#16a34a' if total_pages >= 5 else '#dc2626'}; font-weight:bold;">{'✓ Multi-Page Structure' if total_pages >= 5 else 'Needs More Service Pages'}</td>
        </tr>
        <tr>
            <td><strong>Product / Service &amp; FAQ Schema</strong></td>
            <td>{comm_schema_pct:.0f}% Declared ({comm_schema_cnt}/{total_pages})</td>
            <td>100% Coverage</td>
            <td style="color:{'#16a34a' if comm_schema_pct >= 80 else '#dc2626'}; font-weight:bold;">{'✓ Complete' if comm_schema_pct >= 80 else f'{missing_schema_pct:.0f}% Missing Schema'}</td>
        </tr>
        <tr>
            <td><strong>Content Depth (&lt;300 Words)</strong></td>
            <td>{thin_cnt} Thin Page(s)</td>
            <td>0 Thin Pages</td>
            <td style="color:{'#16a34a' if thin_cnt == 0 else '#dc2626'}; font-weight:bold;">{'✓ Substantial Copy' if thin_cnt == 0 else 'Expand Short Pages'}</td>
        </tr>
        <tr>
            <td><strong>Image Alt Text Coverage</strong></td>
            <td>{total_imgs_cnt - miss_alt_cnt}/{total_imgs_cnt} Tagged ({miss_alt_cnt} Missing)</td>
            <td>100% Tagged</td>
            <td style="color:{'#16a34a' if miss_alt_cnt == 0 else '#b45309'}; font-weight:bold;">{'✓ Complete' if miss_alt_cnt == 0 else 'Add Image Alt Text'}</td>
        </tr>
        <tr>
            <td><strong>AI Crawler Directory (<code>/llms.txt</code>)</strong></td>
            <td>{'Active (HTTP 200)' if has_llms else 'Not Published (404)'}</td>
            <td>Published at Root</td>
            <td style="color:{'#16a34a' if has_llms else '#b45309'}; font-weight:bold;">{'✓ Active' if has_llms else 'Recommended'}</td>
        </tr>
        <tr>
            <td><strong>Web Accessibility (WCAG 2.1 AA)</strong></td>
            <td>{avg_a11y} / 100</td>
            <td>&gt; 90 / 100</td>
            <td style="color:{'#16a34a' if avg_a11y >= 85 else '#b45309'}; font-weight:bold;">{'✓ Compliant' if avg_a11y >= 85 else 'Needs Minor Fixes'}</td>
        </tr>"""

    if has_serp:
        summary_rows += f"""
        <tr>
            <td><strong>Google Organic Search Visibility</strong></td>
            <td>{100.0 - invis_pct:.0f}% on Page 1–2 ({invis_pct:.0f}% Off)</td>
            <td>&gt; 80% on Page 1–2</td>
            <td style="color:{'#16a34a' if invis_pct <= 20 else '#dc2626'}; font-weight:bold;">{'✓ Strong Visibility' if invis_pct <= 20 else 'Growth Opportunity'}</td>
        </tr>"""
    if has_aeo:
        summary_rows += f"""
        <tr>
            <td><strong>AI Answer Citations (Google AIO)</strong></td>
            <td>{100.0 - aeo_loss_pct:.0f}% Cited ({aeo_loss_pct:.0f}% Omitted)</td>
            <td>&gt; 50% Cited</td>
            <td style="color:{'#16a34a' if aeo_loss_pct <= 40 else '#dc2626'}; font-weight:bold;">{'✓ Cited by AI' if aeo_loss_pct <= 40 else 'Needs FAQ &amp; Q&amp;A Copy'}</td>
        </tr>"""
    if has_cwv:
        summary_rows += f"""
        <tr>
            <td><strong>Mobile Visual Load Time (Single URL)</strong></td>
            <td>{esc(raw_lcp)}</td>
            <td>&lt; 2.5 seconds</td>
            <td style="color:{'#16a34a' if lcp_numeric <= 2.5 else '#dc2626'}; font-weight:bold;">{'✓ Fast' if lcp_numeric <= 2.5 else 'Compress Hero Images'}</td>
        </tr>"""
    if da_val:
        summary_rows += f"""
        <tr>
            <td><strong>Domain Authority (Crawly Index)</strong></td>
            <td>{da_val} / 100 ({ref_dom:,} Referring Sites)</td>
            <td>&gt; 35 / 100</td>
            <td style="color:{'#16a34a' if int(da_val) >= 35 else '#b45309'}; font-weight:bold;">{'✓ Established' if int(da_val) >= 35 else 'Build Industry Links'}</td>
        </tr>"""

    pdf_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
    @page {{
        size: letter;
        margin: 0.5in;
        @top-left {{
            content: "Connext Global Solutions — Digital & Search Performance Report";
            font-size: 7pt;
            color: #64748b;
            font-family: 'DejaVu Sans', sans-serif;
            font-weight: bold;
        }}
        @top-right {{
            content: "{esc(brand_display)} ({esc(clean_host)})";
            font-size: 7pt;
            color: #004b93;
            font-family: 'DejaVu Sans', sans-serif;
        }}
        @bottom-left {{
            content: "Prepared for {esc(brand_display)} by Connext Global Solutions";
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
    .page-break {{ page-break-after: always; }}
    .header-cover {{
        border-bottom: 3.5px solid #004b93;
        padding-bottom: 8px;
        margin-bottom: 10px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    .title-main {{
        color: #004b93;
        font-size: 16.5pt;
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
    .meta-strip {{
        background: #f1f5f9;
        border: 1px solid #cbd5e1;
        padding: 6px 10px;
        border-radius: 4px;
        font-size: 7.5pt;
        margin: 8px 0;
        line-height: 1.5;
    }}
    .score-row {{
        display: flex;
        justify-content: space-between;
        gap: 8px;
        margin: 11px 0;
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
    .score-val {{ font-size: 15pt; font-weight: bold; }}
    .score-lbl {{
        font-size: 6.3pt;
        color: #64748b;
        font-weight: bold;
        text-transform: uppercase;
        margin-top: 3px;
    }}
    .section-header {{
        color: #004b93;
        font-size: 11.5pt;
        font-weight: bold;
        border-bottom: 2px solid #004b93;
        padding-bottom: 3px;
        margin-top: 8px;
        margin-bottom: 6px;
    }}
    .sub-header {{
        color: #e85122;
        font-size: 9pt;
        font-weight: bold;
        margin-top: 8px;
        margin-bottom: 4px;
    }}
    .intro-note {{
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-left: 3.5px solid #004b93;
        padding: 6px 10px;
        margin: 4px 0 8px 0;
        font-size: 7.3pt;
        line-height: 1.45;
        color: #334155;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        margin-top: 4px;
        margin-bottom: 10px;
        font-size: 7.1pt;
    }}
    thead {{ display: table-header-group; }}
    tr {{ page-break-inside: avoid; break-inside: avoid; }}
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
    tr:nth-child(even) td {{ background: #f8fafc; }}
    .swot-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 9px;
        margin: 8px 0;
    }}
    .swot-box {{
        border: 1px solid #e2e8f0;
        padding: 7px 9px;
        border-radius: 4px;
        font-size: 7.2pt;
        line-height: 1.4;
        page-break-inside: avoid;
        break-inside: avoid;
    }}
    .swot-w {{ border-top: 3.5px solid #dc2626; background: #fffdfd; }}
    .swot-o {{ border-top: 3.5px solid #16a34a; background: #fdfefe; }}
    .swot-s {{ border-top: 3.5px solid #004b93; background: #fbfdff; }}
    .swot-t {{ border-top: 3.5px solid #f2a900; background: #fffdfa; }}
    ul {{ margin: 4px 0 2px 0; padding-left: 14px; }}
    li {{ margin-bottom: 3px; }}
</style>
</head>
<body>

    <!-- PAGE 1: EXECUTIVE SUMMARY & SCORECARD -->
    <div class="header-cover">
        <div>
            <div class="title-main">{esc(brand_display)} — Digital &amp; Search Performance Audit</div>
            <div class="sub-main">{esc(scope_title)}</div>
        </div>
        <div>
            <img src="{logo_url}" style="width: 125px;">
        </div>
    </div>

    <div class="meta-strip">
        <strong>Website:</strong> {esc(clean_host)} &nbsp;|&nbsp;
        <strong>Scope:</strong> {esc(scope_title)} &nbsp;|&nbsp;
        <strong>Audit Date:</strong> {datetime.now().strftime("%B %d, %Y")} &nbsp;|&nbsp;
        <strong>Prepared by:</strong> Connext Global Solutions
    </div>

    <p style="margin: 6px 0 10px 0; font-size: 8pt; color:#334155; line-height:1.5;">
        This report evaluates how easily customers and AI search engines (Google, ChatGPT, and Perplexity) can find, understand, and trust the audited pages on <strong>{esc(clean_host)}</strong>. All findings below are based on live crawl and search diagnostics for this page group.
    </p>

    {progress_banner_html}

    <div class="score-row">
        <div class="score-card">
            <div class="score-val" style="color:{badge_color(score_seo)};">{score_seo}</div>
            <div class="score-lbl">Search Readiness</div>
        </div>
        <div class="score-card">
            <div class="score-val" style="color:{badge_color(score_schema)};">{score_schema}</div>
            <div class="score-lbl">Product/FAQ Schema</div>
        </div>
        <div class="score-card">
            <div class="score-val" style="color:{badge_color(score_aeo)};">{score_aeo}</div>
            <div class="score-lbl">AI Answer Readiness</div>
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
            <div class="score-lbl">Security</div>
        </div>
    </div>

    <div class="section-header">Performance Summary Across Audited Pillars</div>
    <table>
        <thead>
            <tr>
                <th style="width:30%;">Evaluation Area</th>
                <th style="width:26%;">Current Status</th>
                <th style="width:22%;">Recommended Target</th>
                <th style="width:22%;">Assessment</th>
            </tr>
        </thead>
        <tbody>
            {summary_rows}
        </tbody>
    </table>

    <div class="page-break"></div>

    <!-- PAGE 2: PAGE-BY-PAGE CONTENT, METADATA, SCHEMA & TECH STACK REVIEW -->
    <div class="section-header">Page-by-Page Content, Metadata, Schema &amp; Tech Stack Review</div>
    <p class="intro-note">
        Each page below was checked for word count depth, Meta Title/Description completeness, headline spacing, image alt text coverage, structured product/FAQ schema tags, and accessibility compliance.
    </p>

    <table>
        <thead>
            <tr>
                <th style="width:38%;">Audited Page URL</th>
                <th style="width:9%; text-align:center;">Words</th>
                <th style="width:11%; text-align:center;">Meta Tags</th>
                <th style="width:10%; text-align:center;">Headlines</th>
                <th style="width:10%; text-align:center;">Img Alt</th>
                <th style="width:11%; text-align:center;">Schema</th>
                <th style="width:11%; text-align:center;">WCAG</th>
            </tr>
        </thead>
        <tbody>
            {crawl_rows if crawl_rows else '<tr><td colspan="7">No pages crawled.</td></tr>'}
        </tbody>
    </table>

    {url_structure_pdf_html}

    {tech_stack_html}

    <div class="sub-header">Technical Trust, Security &amp; AI Crawler Governance</div>
    <table>
        <thead>
            <tr>
                <th style="width:36%;">Technical Check</th>
                <th style="width:28%;">Status</th>
                <th style="width:36%;">Why It Matters</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td><strong>HTTPS &amp; Transport Security</strong></td>
                <td>{esc(sec_grade)}</td>
                <td>Protects visitor data and builds browser trust.</td>
            </tr>
            <tr>
                <td><strong>Google Safe Browsing Status</strong></td>
                <td>{esc(sb_res.get('Status', 'Clean'))}</td>
                <td>Confirms zero malware or phishing flags in Google's index.</td>
            </tr>
            <tr>
                <td><strong>AI Crawler Directory (<code>/llms.txt</code>)</strong></td>
                <td>{'✓ Active (HTTP 200)' if has_llms else 'Not Published (404)'}</td>
                <td>Gives AI assistants a clean summary of your pages and services.</td>
            </tr>
            {ai_bots_row_html}
            <tr>
                <td><strong>Google Knowledge Graph Entity</strong></td>
                <td>{esc(kg_info.get('Recognized', 'Not Checked'))}</td>
                <td>Helps Google connect your domain to a verified brand profile.</td>
            </tr>
        </tbody>
    </table>

    {conditional_sections_html}

    <div class="page-break"></div>

    <!-- FINAL PAGE: SPEED, SWOT & PRIORITIZED ACTION PLAN -->
    <div class="section-header">Strategic Assessment &amp; Prioritized Action Plan</div>
    {cwv_block}

    <div class="sub-header">Strategic Strengths, Gaps &amp; Opportunities</div>
    <div class="swot-grid">
        <div class="swot-box swot-s">
            <strong style="color:#004b93; font-size:7.8pt;">STRENGTHS (What Is Working)</strong>
            <ul>{s_html}</ul>
        </div>
        <div class="swot-box swot-w">
            <strong style="color:#dc2626; font-size:7.8pt;">GAPS TO FIX (Internal Priorities)</strong>
            <ul>{w_html}</ul>
        </div>
        <div class="swot-box swot-o">
            <strong style="color:#16a34a; font-size:7.8pt;">GROWTH OPPORTUNITIES</strong>
            <ul>{o_html}</ul>
        </div>
        <div class="swot-box swot-t">
            <strong style="color:#b45309; font-size:7.8pt;">MARKET &amp; COMPETITIVE RISKS</strong>
            <ul>{t_html}</ul>
        </div>
    </div>

    <div class="sub-header">Recommended Action Plan for These Pages</div>
    <table>
        <thead>
            <tr>
                <th style="width:19%;">Priority</th>
                <th style="width:24%;">Action Item</th>
                <th style="width:35%;">What to Do</th>
                <th style="width:22%;">Expected Result</th>
            </tr>
        </thead>
        <tbody>
            {roadmap_rows}
        </tbody>
    </table>

    <div class="meta-strip" style="margin-top:10px; font-size:7pt;">
        <strong>Prepared by Connext Global Solutions:</strong> Need help implementing schema markup, page copy updates, or paid search campaigns? Contact your Connext digital strategy team.
    </div>

</body>
</html>"""
    return weasyprint.HTML(string=pdf_html).write_pdf()


# =============================================================================
# TAB 13: CLIENT-READY PDF & EXCEL DELIVERABLES
# =============================================================================
with tab_report:
    st.subheader("Client-Ready PDF & Excel Deliverables (Connext Global Identity)")
    st.markdown("""
    <div class="guide-box">
        <strong>🎯 All 12 Audit Tabs Integrated Automatically:</strong><br>
        • <strong>Option A (1-Page Executive Brief):</strong> A clean 1-page summary tailored specifically to the audited pages—ideal when sending separate reports for different sections of a client's website.<br>
        • <strong>Option B (Full Technical &amp; Search Report PDF):</strong> Consolidates On-Page SEO, Metadata, Image Alt Text, Installed Tech Stack, Robots/AI Bots, Internal Linking, Duplicate Copy, Branded &amp; Non-Branded SERP + SOV, Generative AEO, Paid Ads + Conquesting, Mobile/Desktop Speed, Backlinks, and First-Party Analytics.<br>
        • <strong>Option C (Master Multi-Tab Excel Workbook .xlsx):</strong> Exports every raw dataset into a styled, multi-worksheet Excel workbook for engineering and analytics teams.
    </div>
    """, unsafe_allow_html=True)

    df_c = st.session_state.get("df_crawl", pd.DataFrame())
    if df_c.empty:
        st.warning("No audit data available yet. Please run Tab 1 (Tech, Content & SEO) first before generating reports.")
    else:
        target_name = st.session_state.get("target_domain", "https://alphacord.com")
        clean_host_default = urlparse(target_name).netloc.replace("www.", "") or target_name
        auto_brand = clean_host_default.split(".")[0].title()
        auto_scope = detect_audit_scope_label(df_c, target_name)

        c_meta1, c_meta2 = st.columns(2)
        with c_meta1:
            custom_client_name = st.text_input("Client / Brand Name (Shown on Report Header)", value=auto_brand)
        with c_meta2:
            custom_section_label = st.text_input("Report Focus / Audited Page Group", value=auto_scope)

        safe_slug = re.sub(r'[^a-zA-Z0-9_-]+', '_', custom_section_label.split("—")[0].strip()).strip("_").lower() or "site_audit"
        safe_logo = get_safe_logo_src(LOGO_URL)

        col_pdf1, col_pdf2, col_xlsx = st.columns(3)

        with col_pdf1:
            st.markdown("### Option A: 1-Page Executive Brief")
            st.caption("Single-page summary tailored to this specific page group.")
            if st.button("Compile 1-Page Executive Brief (PDF)", use_container_width=True):
                try:
                    with st.spinner("Building 1-page executive brief..."):
                        summary_pdf_bytes = build_executive_summary_1page_html(
                            df_c=df_c,
                            df_r=st.session_state.get("df_rankings", pd.DataFrame()),
                            df_a=st.session_state.get("df_aeo", pd.DataFrame()),
                            df_eq=st.session_state.get("df_equity", pd.DataFrame()),
                            df_can=st.session_state.get("df_cannibal", pd.DataFrame()),
                            df_ads=st.session_state.get("df_ads", pd.DataFrame()),
                            cwv_res=st.session_state.get("cwv_metrics", {}),
                            crux_res=st.session_state.get("crux_metrics", {}),
                            sec_res=st.session_state.get("sec_headers", {}),
                            sb_res=st.session_state.get("safe_browsing", {}),
                            kg_info=st.session_state.get("kg_info", {}),
                            prev_snapshot=st.session_state.get("prev_snapshot"),
                            target_name=target_name,
                            has_llms=bool(st.session_state.get("has_llms", False)),
                            logo_url=safe_logo,
                            crawly_data=st.session_state.get("crawly_data", {}),
                            section_label=custom_section_label,
                            client_name=custom_client_name,
                            df_serp_ads=st.session_state.get("df_serp_ads", pd.DataFrame()),
                            df_brand_serp=st.session_state.get("df_brand_serp", pd.DataFrame()),
                            brand_sitelinks=st.session_state.get("brand_sitelinks", []),
                            df_broken_links=st.session_state.get("df_broken_links", pd.DataFrame()),
                            robots_data=st.session_state.get("robots_audit_data", {}),
                            df_mentions=st.session_state.get("df_mentions", pd.DataFrame()),
                            first_party_summary=st.session_state.get("first_party_summary", [])
                        )
                        st.session_state["compiled_summary_pdf_bytes"] = summary_pdf_bytes
                        st.session_state["compiled_summary_filename"] = f"{clean_host_default}_{safe_slug}_executive_brief.pdf"
                        st.success("✓ 1-Page Executive Brief ready!")
                except Exception as e:
                    st.error(f"Could not compile 1-page PDF: {e}")

            if st.session_state.get("compiled_summary_pdf_bytes"):
                st.download_button(
                    label="⬇️ Download 1-Page Executive Brief (PDF)",
                    data=st.session_state["compiled_summary_pdf_bytes"],
                    file_name=st.session_state.get("compiled_summary_filename", "executive_brief.pdf"),
                    mime="application/pdf",
                    use_container_width=True
                )

        with col_pdf2:
            st.markdown("### Option B: Full Technical & Search Report")
            st.caption("Complete multi-page report covering every module run for this batch.")
            if st.button("Compile Full Technical Report (PDF)", use_container_width=True):
                try:
                    with st.spinner("Building full technical report..."):
                        dossier_pdf_bytes = build_executive_dossier_html(
                            df_c=df_c,
                            df_r=st.session_state.get("df_rankings", pd.DataFrame()),
                            df_a=st.session_state.get("df_aeo", pd.DataFrame()),
                            df_eq=st.session_state.get("df_equity", pd.DataFrame()),
                            df_can=st.session_state.get("df_cannibal", pd.DataFrame()),
                            df_ads=st.session_state.get("df_ads", pd.DataFrame()),
                            cwv_res=st.session_state.get("cwv_metrics", {}),
                            crux_res=st.session_state.get("crux_metrics", {}),
                            sec_res=st.session_state.get("sec_headers", {}),
                            sb_res=st.session_state.get("safe_browsing", {}),
                            kg_info=st.session_state.get("kg_info", {}),
                            prev_snapshot=st.session_state.get("prev_snapshot"),
                            target_name=target_name,
                            has_llms=bool(st.session_state.get("has_llms", False)),
                            logo_url=safe_logo,
                            crawly_data=st.session_state.get("crawly_data", {}),
                            section_label=custom_section_label,
                            client_name=custom_client_name,
                            df_serp_ads=st.session_state.get("df_serp_ads", pd.DataFrame()),
                            df_brand_serp=st.session_state.get("df_brand_serp", pd.DataFrame()),
                            brand_sitelinks=st.session_state.get("brand_sitelinks", []),
                            cwv_desktop_res=st.session_state.get("cwv_desktop_metrics", {}),
                            df_cwv_batch=st.session_state.get("df_cwv_batch", pd.DataFrame()),
                            df_broken_links=st.session_state.get("df_broken_links", pd.DataFrame()),
                            robots_data=st.session_state.get("robots_audit_data", {}),
                            df_mentions=st.session_state.get("df_mentions", pd.DataFrame()),
                            first_party_summary=st.session_state.get("first_party_summary", []),
                            df_gsc_quickwins=st.session_state.get("df_gsc_quickwins", pd.DataFrame()),
                            df_ga4_top_pages=st.session_state.get("df_ga4_top_pages", pd.DataFrame())
                        )
                        st.session_state["compiled_dossier_pdf_bytes"] = dossier_pdf_bytes
                        st.session_state["compiled_dossier_filename"] = f"{clean_host_default}_{safe_slug}_technical_report.pdf"
                        st.success("✓ Full Technical Report ready!")
                except Exception as e:
                    st.error(f"Could not compile full technical report: {e}")

            if st.session_state.get("compiled_dossier_pdf_bytes"):
                st.download_button(
                    label="⬇️ Download Full Technical Report (PDF)",
                    data=st.session_state["compiled_dossier_pdf_bytes"],
                    file_name=st.session_state.get("compiled_dossier_filename", "technical_report.pdf"),
                    mime="application/pdf",
                    use_container_width=True
                )

        with col_xlsx:
            st.markdown("### Option C: Master Audit Workbook (.xlsx)")
            st.caption("Multi-tab Excel spreadsheet with all raw crawl, SEO, schema, link, SERP & AEO tables.")
            if st.button("Compile Master Excel Workbook (.xlsx)", use_container_width=True):
                try:
                    with st.spinner("Building styled multi-tab Excel workbook..."):
                        xlsx_bytes = build_master_audit_excel(
                            df_c=df_c,
                            df_r=st.session_state.get("df_rankings", pd.DataFrame()),
                            df_a=st.session_state.get("df_aeo", pd.DataFrame()),
                            df_eq=st.session_state.get("df_equity", pd.DataFrame()),
                            df_can=st.session_state.get("df_cannibal", pd.DataFrame()),
                            df_ads=st.session_state.get("df_ads", pd.DataFrame()),
                            df_broken=st.session_state.get("df_broken_links", pd.DataFrame()),
                            brand_name=custom_client_name,
                            scope_label=custom_section_label,
                            df_serp_ads=st.session_state.get("df_serp_ads", pd.DataFrame()),
                            df_brand_serp=st.session_state.get("df_brand_serp", pd.DataFrame()),
                            df_cwv_batch=st.session_state.get("df_cwv_batch", pd.DataFrame()),
                            crawly_data=st.session_state.get("crawly_data", {}),
                            df_mentions=st.session_state.get("df_mentions", pd.DataFrame()),
                            robots_data=st.session_state.get("robots_audit_data", {}),
                            first_party_summary=st.session_state.get("first_party_summary", []),
                            df_url_audit=st.session_state.get("df_url_audit", pd.DataFrame())
                        )
                        st.session_state["master_xlsx_bytes"] = xlsx_bytes
                        st.session_state["master_xlsx_filename"] = f"{clean_host_default}_{safe_slug}_master_audit.xlsx"
                        st.success("✓ Master Excel Workbook ready!")
                except Exception as e:
                    st.error(f"Could not compile Excel workbook: {e}")

            if st.session_state.get("master_xlsx_bytes"):
                st.download_button(
                    label="⬇️ Download Master Excel Workbook (.xlsx)",
                    data=st.session_state["master_xlsx_bytes"],
                    file_name=st.session_state.get("master_xlsx_filename", "master_audit.xlsx"),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
