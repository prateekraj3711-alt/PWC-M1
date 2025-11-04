import os
import json
import asyncio
from pathlib import Path
from typing import Optional, Dict, List

import httpx
import pandas as pd
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from utils import logger, storage_state_to_cookie_header, ensure_dir
from gsheets import sync_to_sheets_with_audit, get_sheets_service
from gdrive import DriveClient
from pdf_to_json import pdf_to_json

TMP_DIR = Path('/tmp')
EXPORT_DIR = TMP_DIR / 'dashboard_exports'
CAND_DIR = TMP_DIR / 'candidates'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
CAND_DIR.mkdir(parents=True, exist_ok=True)

# Configurable rate limits (via environment variables)
CANDIDATE_DELAY = float(os.getenv('CANDIDATE_PROCESS_DELAY', '0.5'))  # Delay between candidates (seconds)
DOCUMENT_DELAY = float(os.getenv('DOCUMENT_DOWNLOAD_DELAY', '0.3'))  # Delay between documents (seconds)
MAX_CONCURRENT_CANDIDATES = int(os.getenv('MAX_CONCURRENT_CANDIDATES', '5'))  # Concurrent candidate processing
MAX_CONCURRENT_DOCUMENTS = int(os.getenv('MAX_CONCURRENT_DOCUMENTS', '3'))  # Concurrent document downloads per candidate


async def upload_existing_to_sheets(spreadsheet_id: str):
    tabs = [
        "Today's allocated",
        "Not started",
        "Draft",
        "Rejected / Insufficient",
        "Submitted",
        "Work in progress",
        "BGV closed",
    ]
    existing = []
    for t in tabs:
        fp = EXPORT_DIR / f"{t}.xlsx"
        if fp.exists():
            existing.append((t, fp))
    if not existing:
        raise ValueError(f"No Excel files in {EXPORT_DIR}")

    results = []
    for tab, fp in existing:
        try:
            r = await sync_to_sheets_with_audit(tab, fp, spreadsheet_id)
            results.append(r)
        except Exception as e:
            logger.exception(f"Sync failed for {tab}: {e}")
            results.append({"tab": tab, "error": str(e)})
    return {"ok": True, "tab_results": results}


def resolve_endpoint_for_tab(tab: str, api_map: Optional[Dict]) -> Optional[str]:
    if not api_map:
        return None
    patterns = {
        "Today's allocated": ["today", "todays", "allocated"],
        "Not started": ["notstarted", "not-started", "not_started"],
        "Draft": ["draft"],
        "Rejected / Insufficient": ["rejected", "insufficient"],
        "Submitted": ["submitted"],
        "Work in progress": ["workinprogress", "work-in-progress", "inprogress"],
        "BGV closed": ["bgvclosed", "closed"],
    }
    export_dict = api_map.get('exportEndpoints', {}) if isinstance(api_map, dict) else {}
    for path_key, info in export_dict.items():
        p = (info.get('path') or path_key or '').lower()
        if any(k in p for k in patterns.get(tab, [])):
            return info.get('path') or path_key
    for ep in api_map.get('endpoints', []):
        p = (ep.get('path') or '').lower()
        if any(k in p for k in patterns.get(tab, [])):
            return ep.get('path')
    return None


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(Exception))
async def fetch_binary(client: httpx.AsyncClient, url: str, headers: Dict) -> bytes:
    r = await client.get(url, headers=headers, timeout=240)
    r.raise_for_status()
    return r.content


async def export_tab_via_api(tab: str, base_url: str, headers: Dict, api_map: Optional[Dict]) -> Dict:
    endpoint = resolve_endpoint_for_tab(tab, api_map)
    file_path = EXPORT_DIR / f"{tab}.xlsx"
    if endpoint:
        url = f"{base_url}{endpoint}"
    else:
        url = f"{base_url}/api/export/TabData?tabName={tab.replace(' ', '%20')}"
    async with httpx.AsyncClient() as client:
        data = await fetch_binary(client, url, headers)
        file_path.write_bytes(data)
    if file_path.stat().st_size < 100:
        raise RuntimeError(f"Downloaded file too small for {tab}")
    return {"tab": tab, "status": "done", "file_size": file_path.stat().st_size, "method": "api"}


async def resolve_document_endpoint(candidate_id: str, doc_type: str, api_map: Optional[Dict]) -> Optional[str]:
    """Resolve document endpoint from API map or use default pattern"""
    if api_map and isinstance(api_map, dict):
        doc_endpoints = api_map.get('documentEndpoints', {})
        for path_key, info in doc_endpoints.items():
            path_lower = (info.get('path') or path_key or '').lower()
            if doc_type.lower() in path_lower or candidate_id.lower() in path_lower:
                return info.get('path') or path_key
    return None


async def download_document_via_api(client: httpx.AsyncClient, base_url: str, headers: Dict, candidate_id: str, doc_id: str, doc_name: str, api_map: Optional[Dict]) -> Optional[bytes]:
    """Download a document via API, trying multiple endpoint patterns"""
    endpoints_to_try = [
        resolve_document_endpoint(candidate_id, doc_id, api_map),
        f"/api/document/{candidate_id}/{doc_id}",
        f"/api/candidate/{candidate_id}/document/{doc_id}",
        f"/api/candidate/{candidate_id}/documents/{doc_id}/download",
    ]
    
    for endpoint in endpoints_to_try:
        if not endpoint:
            continue
        try:
            url = f"{base_url}{endpoint}" if not endpoint.startswith('http') else endpoint
            return await fetch_binary(client, url, headers)
        except Exception as e:
            logger.debug(f"API endpoint {endpoint} failed: {e}")
            continue
    return None


async def process_candidate_via_playwright(page: Page, candidate: Dict, base_url: str, drive: DriveClient, parent_folder: str) -> Dict:
    """Process candidate using Playwright when API fails"""
    cid = str(candidate.get('CandidateID') or candidate.get('id') or candidate.get('candidateId'))
    name = str(candidate.get('CandidateName') or candidate.get('name') or 'Unknown')
    folder_name = f"{cid} - {name}"
    local_dir = CAND_DIR / folder_name
    ensure_dir(local_dir)
    
    # Save candidate details
    details_path = local_dir / 'details.json'
    details_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2))
    
    try:
        # Navigate to candidate profile/preview page
        # Try multiple URL patterns
        profile_urls = [
            f"{base_url}/BGVAdmin/Candidate/Preview/{cid}",
            f"{base_url}/BGVAdmin/Candidate/Details/{cid}",
            f"{base_url}/Candidate/{cid}",
            f"{base_url}/api/candidate/{cid}",
        ]
        
        profile_loaded = False
        for url in profile_urls:
            try:
                await page.goto(url, wait_until='networkidle', timeout=30000)
                # Check if page loaded successfully (not error page)
                if 'error' not in page.url.lower() and 'accessdenied' not in page.url.lower():
                    profile_loaded = True
                    break
            except Exception:
                continue
        
        if not profile_loaded:
            raise Exception("Could not load candidate profile page")
        
        # Wait for page to load
        await asyncio.sleep(2)
        
        # Try to find and download PIF
        pif_downloaded = False
        pif_selectors = [
            'a:has-text("PIF")',
            'a:has-text("Personal Information Form")',
            'button:has-text("Download PIF")',
            '[href*="pif" i]',
            '[href*="PersonalInformationForm" i]',
            '#downloadPIF',
            '.download-pif',
        ]
        
        for sel in pif_selectors:
            try:
                element = page.locator(sel).first
                if await element.is_visible(timeout=3000):
                    async with page.expect_download(timeout=30000) as download_info:
                        await element.click(force=True)
                    download = await download_info.value
                    pif_path = local_dir / 'pif.pdf'
                    await download.save_as(pif_path)
                    
                    if pif_path.exists() and pif_path.stat().st_size > 100:
                        # Convert PIF to JSON
                        try:
                            pif_json = pdf_to_json(str(pif_path))
                            (local_dir / 'pif.json').write_text(json.dumps(pif_json, ensure_ascii=False, indent=2))
                        except Exception as e:
                            logger.warning(f"PIF to JSON conversion failed: {e}")
                        pif_downloaded = True
                        break
            except Exception:
                continue
        
        # Try to find and download other documents
        documents_downloaded = []
        doc_selectors = [
            'a[href*="download" i]',
            'button:has-text("Download")',
            'a[href*=".pdf" i]',
            'a[href*=".doc" i]',
            '.document-download',
            '[data-document-id]',
        ]
        
        for sel in doc_selectors:
            try:
                elements = page.locator(sel).all()
                # NO LIMIT - download all documents found
                for idx, element in enumerate(elements):
                    try:
                        if await element.is_visible(timeout=2000):
                            doc_name = await element.get_attribute('href') or await element.text_content() or f"document_{idx+1}"
                            # Clean filename
                            doc_name = ''.join(c for c in doc_name if c.isalnum() or c in (' ', '-', '_', '.'))[:50]
                            if not doc_name.endswith(('.pdf', '.doc', '.docx')):
                                doc_name += '.pdf'
                            
                            async with page.expect_download(timeout=15000) as download_info:
                                await element.click(force=True)
                            download = await download_info.value
                            doc_path = local_dir / 'documents' / doc_name
                            ensure_dir(doc_path.parent)
                            await download.save_as(doc_path)
                            
                            if doc_path.exists() and doc_path.stat().st_size > 100:
                                documents_downloaded.append(doc_name)
                            # Configurable delay between document downloads
                            await asyncio.sleep(DOCUMENT_DELAY)
                    except Exception:
                        continue
            except Exception:
                continue
        
        # Upload to Drive
        folder_id = await drive.ensure_child_folder(parent_folder, folder_name)
        await drive.upload_file(folder_id, str(details_path), 'application/json')
        
        pif_pdf_path = local_dir / 'pif.pdf'
        if pif_pdf_path.exists():
            await drive.upload_file(folder_id, str(pif_pdf_path), 'application/pdf')
            pif_json_path = local_dir / 'pif.json'
            if pif_json_path.exists():
                await drive.upload_file(folder_id, str(pif_json_path), 'application/json')
        
        # Upload documents
        documents_dir = local_dir / 'documents'
        if documents_dir.exists():
            for doc_file in documents_dir.iterdir():
                if doc_file.is_file():
                    mime_type = 'application/pdf' if doc_file.suffix == '.pdf' else 'application/octet-stream'
                    await drive.upload_file(folder_id, str(doc_file), mime_type)
        
        return {
            "candidate_id": cid,
            "name": name,
            "folder": str(local_dir),
            "method": "playwright",
            "pif_downloaded": pif_downloaded,
            "documents_count": len(documents_downloaded)
        }
        
    except Exception as e:
        logger.exception(f"Playwright processing failed for candidate {cid}: {e}")
        raise


async def process_candidate_via_api(candidate: Dict, client: httpx.AsyncClient, headers: Dict, base_url: str, drive: DriveClient, parent_folder: str, api_map: Optional[Dict]) -> Dict:
    """Process candidate using API (preferred method)"""
    cid = str(candidate.get('CandidateID') or candidate.get('id') or candidate.get('candidateId'))
    name = str(candidate.get('CandidateName') or candidate.get('name') or 'Unknown')
    folder_name = f"{cid} - {name}"
    local_dir = CAND_DIR / folder_name
    ensure_dir(local_dir)
    details_path = local_dir / 'details.json'
    details_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2))

    # Download PIF
    pif_pdf_path = local_dir / 'pif.pdf'
    pif_downloaded = False
    try:
        pif_bytes = await download_document_via_api(client, base_url, headers, cid, 'pif', 'PIF', api_map)
        if pif_bytes and len(pif_bytes) > 100:
            pif_pdf_path.write_bytes(pif_bytes)
            # Convert to JSON
            try:
                pif_json = pdf_to_json(str(pif_pdf_path))
                (local_dir / 'pif.json').write_text(json.dumps(pif_json, ensure_ascii=False, indent=2))
            except Exception as e:
                logger.warning(f"PIF to JSON conversion failed: {e}")
            pif_downloaded = True
    except Exception as e:
        logger.debug(f"PIF API download failed for {cid}: {e}")

    # Download other documents (try to get document list first)
    documents_downloaded = []
    documents_dir = local_dir / 'documents'
    ensure_dir(documents_dir)
    
    try:
        # Try to get document list
        doc_list_urls = [
            f"{base_url}/api/candidate/{cid}/documents",
            f"{base_url}/api/document/list?candidateId={cid}",
        ]
        
        doc_list = []
        for url in doc_list_urls:
            try:
                r = await client.get(url, headers=headers, timeout=30)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        doc_list = data
                    elif isinstance(data, dict) and 'items' in data:
                        doc_list = data['items']
                    break
            except Exception:
                continue
        
        # Download each document (NO LIMIT - download all documents)
        for doc in doc_list:
            try:
                doc_id = str(doc.get('id') or doc.get('docId') or doc.get('documentId'))
                doc_name = str(doc.get('name') or doc.get('fileName') or f"document_{doc_id}")
                if not doc_name.endswith(('.pdf', '.doc', '.docx')):
                    doc_name += '.pdf'
                
                doc_bytes = await download_document_via_api(client, base_url, headers, cid, doc_id, doc_name, api_map)
                if doc_bytes and len(doc_bytes) > 100:
                    doc_path = documents_dir / doc_name
                    doc_path.write_bytes(doc_bytes)
                    documents_downloaded.append(doc_name)
                    # Configurable delay between document downloads
                    await asyncio.sleep(DOCUMENT_DELAY)
            except Exception as e:
                logger.debug(f"Document {doc.get('id')} download failed: {e}")
    except Exception as e:
        logger.debug(f"Document list fetch failed for {cid}: {e}")

    # Upload to Drive
    folder_id = await drive.ensure_child_folder(parent_folder, folder_name)
    await drive.upload_file(folder_id, str(details_path), 'application/json')
    if pif_pdf_path.exists():
        await drive.upload_file(folder_id, str(pif_pdf_path), 'application/pdf')
        pj = local_dir / 'pif.json'
        if pj.exists():
            await drive.upload_file(folder_id, str(pj), 'application/json')
    
    # Upload documents
    if documents_dir.exists():
        for doc_file in documents_dir.iterdir():
            if doc_file.is_file():
                mime_type = 'application/pdf' if doc_file.suffix == '.pdf' else 'application/octet-stream'
                await drive.upload_file(folder_id, str(doc_file), mime_type)

    return {
        "candidate_id": cid,
        "name": name,
        "folder": str(local_dir),
        "method": "api",
        "pif_downloaded": pif_downloaded,
        "documents_count": len(documents_downloaded)
    }


async def trigger_full_export(session_id: str, storage_state: Optional[Dict], api_map: Optional[Dict], spreadsheet_id: Optional[str], drive_folder_id: Optional[str]) -> Dict:
    base_url = 'https://compliancenominationportal.in.pwc.com'
    headers = storage_state_to_cookie_header(storage_state)

    results = []
    for tab in [
        "Today's allocated",
        "Not started",
        "Draft",
        "Rejected / Insufficient",
        "Submitted",
        "Work in progress",
        "BGV closed",
    ]:
        try:
            r = await export_tab_via_api(tab, base_url, headers, api_map)
            results.append(r)
            await asyncio.sleep(1)
        except Exception as e:
            results.append({"tab": tab, "status": "error", "error": str(e)})

    # Candidate processing - prefer API, fallback to Playwright
    drive = DriveClient()
    parent_folder = drive_folder_id or await drive.ensure_root_folder('PwC Candidates')
    candidates_processed: List[Dict] = []
    
    # Get candidate list from exported Excel files
    candidate_list = []
    for tab_file in EXPORT_DIR.glob('*.xlsx'):
        try:
            df = pd.read_excel(tab_file)
            if 'CandidateID' in df.columns:
                for _, row in df.iterrows():
                    candidate_list.append({
                        'CandidateID': str(row.get('CandidateID', '')),
                        'CandidateName': str(row.get('CandidateName', row.get('Name', 'Unknown')))
                    })
        except Exception as e:
            logger.debug(f"Could not read candidates from {tab_file}: {e}")
    
    # Remove duplicates
    seen_ids = set()
    unique_candidates = []
    for cand in candidate_list:
        cid = cand.get('CandidateID', '')
        if cid and cid not in seen_ids:
            seen_ids.add(cid)
            unique_candidates.append(cand)
    
    logger.info(f"Found {len(unique_candidates)} unique candidates to process (NO LIMIT - processing all)")
    
    # Process candidates with bounded concurrency for scalability
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CANDIDATES)
    
    async def process_single_candidate(candidate: Dict, index: int, total: int) -> Dict:
        """Process a single candidate with semaphore for concurrency control"""
        async with semaphore:
            cid = candidate.get('CandidateID', '')
            if not cid:
                return {"error": "No CandidateID", "candidate": cid}
            
            logger.info(f"🔄 Processing candidate {index+1}/{total}: {cid}")
            
            try:
                # Try API first
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                        pr = await process_candidate_via_api(candidate, client, headers, base_url, drive, parent_folder, api_map)
                    logger.info(f"✅ [{index+1}/{total}] Processed candidate {cid} via API (PIF: {pr.get('pif_downloaded', False)}, Docs: {pr.get('documents_count', 0)})")
                    return pr
                except Exception as api_err:
                    logger.warning(f"⚠️ [{index+1}/{total}] API processing failed for {cid}, trying Playwright: {api_err}")
                    # Fallback to Playwright
                    try:
                        async with async_playwright() as p:
                            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
                            context = await browser.new_context(storage_state=storage_state)
                            page = await context.new_page()
                            try:
                                pr = await process_candidate_via_playwright(page, candidate, base_url, drive, parent_folder)
                                logger.info(f"✅ [{index+1}/{total}] Processed candidate {cid} via Playwright (PIF: {pr.get('pif_downloaded', False)}, Docs: {pr.get('documents_count', 0)})")
                                return pr
                            finally:
                                await browser.close()
                    except Exception as pw_err:
                        logger.error(f"❌ [{index+1}/{total}] Both API and Playwright failed for candidate {cid}: {pw_err}")
                        return {
                            "candidate_id": cid,
                            "error": f"API: {api_err}, Playwright: {pw_err}"
                        }
            except Exception as ce:
                logger.exception(f"❌ [{index+1}/{total}] Unexpected error processing candidate {cid}: {ce}")
                return {"error": str(ce), "candidate": cid}
            finally:
                # Rate limiting delay between candidates
                if index < total - 1:  # Don't delay after last candidate
                    await asyncio.sleep(CANDIDATE_DELAY)
    
    # Process all candidates concurrently (with bounded parallelism)
    # Each task will create its own HTTP client inside process_single_candidate
    tasks = [
        process_single_candidate(candidate, idx, len(unique_candidates))
        for idx, candidate in enumerate(unique_candidates)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results and handle exceptions
    for result in results:
        if isinstance(result, Exception):
            logger.error(f"Task exception: {result}")
            candidates_processed.append({"error": str(result)})
        elif isinstance(result, dict):
            candidates_processed.append(result)
    
    logger.info(f"✅ Completed processing {len(candidates_processed)} candidates")

    sheets_result = None
    if spreadsheet_id:
        sheets_result = await upload_existing_to_sheets(spreadsheet_id)

    return {
        "ok": True,
        "message": "Export run triggered",
        "tabs": results,
        "candidates": candidates_processed,
        "sheets": sheets_result or {"status": "skipped"}
    }


