import re
import json
from typing import Dict

import pdfplumber
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
import pytesseract


def extract_text_pdfplumber(path: str) -> str:
    try:
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or '' for page in pdf.pages)
    except Exception:
        return ''


def extract_text_pypdf2(path: str) -> str:
    try:
        reader = PdfReader(path)
        text = []
        for page in reader.pages:
            text.append(page.extract_text() or '')
        return "\n".join(text)
    except Exception:
        return ''


def extract_text_ocr(path: str) -> str:
    try:
        images = convert_from_path(path)
        parts = []
        for img in images:
            parts.append(pytesseract.image_to_string(img))
        return "\n".join(parts)
    except Exception:
        return ''


def parse_fields(text: str) -> Dict:
    fields = {}
    patterns = {
        'CandidateID': r'(Candidate\s*ID)\s*[:\-\s]*([A-Za-z0-9\-_/]+)',
        'CandidateName': r'(Candidate\s*Name|Name)\s*[:\-\s]*([A-Za-z ,.]+)',
        'DOB': r'(Date\s*of\s*Birth|DOB)\s*[:\-\s]*([0-9]{2,4}[\-/][0-9]{1,2}[\-/][0-9]{1,2})',
        'Employer': r'(Employer|Company)\s*[:\-\s]*([A-Za-z0-9 &,.]+)',
        'Role': r'(Role|Designation|Position)\s*[:\-\s]*([A-Za-z0-9 &,.]+)',
        'Education': r'(Education|Qualification)\s*[:\-\s]*([A-Za-z0-9 &,.]+)'
    }
    for k, pat in patterns.items():
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            fields[k] = m.group(2).strip()
    return fields


def pdf_to_json(path: str) -> Dict:
    text = extract_text_pdfplumber(path)
    if not text.strip():
        text = extract_text_pypdf2(path)
    if not text.strip():
        text = extract_text_ocr(path)
    parsed = parse_fields(text)
    return {
        'raw_text': text[:100000],
        'parsed': parsed
    }


