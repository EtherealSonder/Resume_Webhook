# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()

import mindee
import openai
import os
import psycopg2
import urllib.parse as up
import json
from datetime import datetime
from typing import Dict, Any, List
from openai import OpenAI
from mindee import Client, AsyncPredictResponse, product
import re
import spacy
from rapidfuzz import fuzz
import re
from urlextract import URLExtract
import fitz  # PyMuPDF
import validators
import tldextract
import socket
from typing import Tuple, Dict
import language_tool_python  # grammar/spell-check
import textstat
from copyleaks_client import check_ai_content, check_plagiarism



mindee_api_key = os.getenv("MINDEE_API_KEY")
mindee_client = mindee.Client(api_key=mindee_api_key)
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Load the small English model
nlp = spacy.load("en_core_web_sm")

my_endpoint = mindee_client.create_endpoint(
    account_name="EtherealSonder",
    endpoint_name="resume_screener",
    version="1"
)

def read_resume(file_path):
    document = mindee_client.source_from_path(file_path)
    result: AsyncPredictResponse = mindee_client.enqueue_and_parse(
        product.GeneratedV1,
        document,
        endpoint=my_endpoint
    )
    return result.document

def get_value(x):
    return x.value if hasattr(x, "value") else x

#EXPERIENCE YEARS CALCULATION

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12
}

def to_int_month(val):
    if isinstance(val, int):
        return val
    val_str = str(val).strip().lower()
    return MONTH_MAP.get(val_str, None)

def calculate_experience_years(experiences: List[Any]) -> float:
    total_months = 0
    now = datetime.now()

    for exp in experiences:
        try:
            start_year = get_value(getattr(exp, "start_year", None))
            start_month_raw = get_value(getattr(exp, "start_month", None))
            end_year = get_value(getattr(exp, "end_year", None))
            end_month_raw = get_value(getattr(exp, "end_month", None))

            start_month = to_int_month(start_month_raw)
            end_month = to_int_month(end_month_raw)

            if not start_year or not start_month:
                continue

            if not end_year or str(end_year).lower() in ["present", "ongoing", "now"]:
                end_year = now.year
            if not end_month or str(end_month).lower() in ["present", "ongoing", "now"]:
                end_month = now.month

            start_date = datetime(year=int(start_year), month=int(start_month), day=1)
            end_date = datetime(year=int(end_year), month=int(end_month), day=1)

            if end_date > start_date:
                months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
                total_months += months
        except Exception as e:
            print("Skipping entry due to error:", e)
            continue

    return round(total_months / 12, 1)

def check_experience_match(
    expected_range: str,
    expected_level: str,
    candidate_years: float,
    candidate_job_titles: List[str],
    job_title: str
) -> Tuple[bool, str, float]:
    """
    Final, robust version:
    - Retains numeric range/level mapping logic.
    - Adds job title alignment scoring.
    - Returns: (experience match bool, explanation, previous role alignment score)
    """

    # Helper to parse expected_range text to numeric lower bound (e.g., '2+ years' -> 2)
    def parse_range_to_min_years(text: str) -> float:
        match = re.search(r"(\d+(\.\d+)?)", text)
        if match:
            return float(match.group(1))
        return 0.0

    expected_range_min_years = parse_range_to_min_years(expected_range or "")

    level_to_range = {
        "Fresher": (0, 0),
        "Beginner": (0, 1),
        "Junior": (1, 2),
        "Mid-Level": (2, 4),
        "Experienced": (4, 7),
        "Advanced": (7, 10),
        "Expert": (10, 15),
        "Veteran": (15, 100)
    }

    level_range = level_to_range.get(expected_level, (0, 100))  # default if unknown

    # Initial explanation string
    explanation = ""

    # Decision logic: expected range has priority
    if expected_range != "No expectation on experience" and expected_range.strip():
        experience_match = candidate_years >= expected_range_min_years
        explanation += f"Expected experience: {expected_range}. Candidate has {candidate_years} years. "
    elif expected_level != "No expectation on experience" and expected_level.strip():
        experience_match = level_range[0] <= candidate_years <= level_range[1]
        explanation += f"Expected level: {expected_level} ({level_range[0]}-{level_range[1]} yrs). Candidate has {candidate_years} yrs. "
    else:
        experience_match = True
        explanation += "No explicit experience expectation. "

    # Add job title alignment scoring
    alignment_scores = []
    for prev_title in candidate_job_titles:
        score = fuzz.partial_ratio(prev_title.lower(), job_title.lower())
        alignment_scores.append(score)
    best_alignment = max(alignment_scores) if alignment_scores else 0
    explanation += f"Best previous job title alignment: {best_alignment}%."

    return experience_match, explanation, best_alignment



#EDUCATION LEVEL CALCULATION


def extract_education_level(education_input, resume_text="") -> str:
    """
    Extract the highest-priority education level from structured input or fallback text.
    PhD is only matched if explicit keywords (like 'phd', 'doctoral') are found.
    """

    # Flatten and normalize input
    if hasattr(education_input, "values"):
        values = [v.value.lower() for v in education_input.values if hasattr(v, "value") and v.value]
        education_str = " ".join(values)
    elif isinstance(education_input, str):
        education_str = education_input.lower()
    else:
        education_str = str(education_input).lower()

    print("\nFlattened education string:", education_str)

    # If structured data is empty, fallback to resume text
    use_resume_fallback = False
    if not education_str.strip() and resume_text:
        print("No structured education data found, scanning full resume text instead.")
        education_str = resume_text.lower()
        use_resume_fallback = True

    # Clean up text for matching
    education_str = re.sub(r"[^a-z\s]", "", education_str)

    # Priority level mapping (with clear and strict keywords)
    priority_levels = [
        ("PhD", ["phd", "doctorate", "doctoral", "doctor of philosophy"]),  # STRICT
        ("Master's", ["master", "msc", "m sc", "m a", "mfa", "meng", "ms", "mtech"]),
        ("Bachelor's", ["bachelor", "bsc", "b sc", "ba", "bfa", "beng", "btech", "b e"]),
        ("Diploma", ["diploma", "associate", "pg diploma"]),
        ("High School", ["high school", "secondary", "intermediate", "12th", "10th", "senior school"])
    ]

    found_levels = set()

    # spaCy token matching
    doc = nlp(education_str)
    for token in doc:
        token_text = token.text.lower()
        for level, keywords in priority_levels:
            for keyword in keywords:
                if keyword == "phd" and use_resume_fallback:
                    continue  # Don't allow PhD guess from fallback text
                if keyword in token_text:
                    print(f"Matched {level} in token: '{token.text}' (keyword: '{keyword}')")
                    found_levels.add(level)

    # Regex-based fallback
    for level, keywords in priority_levels:
        for keyword in keywords:
            if keyword == "phd" and use_resume_fallback:
                continue  # Same PhD block from resume_text
            if re.search(rf"\b{re.escape(keyword)}\b", education_str):
                print(f"Regex matched {level} (keyword: '{keyword}')")
                found_levels.add(level)

    # Pick highest priority match
    for level, _ in priority_levels:
        if level in found_levels:
            print(f"Selected highest priority level: {level}")
            return level

    print("No match found. Returning 'Other'")
    return "Other"
    
def check_education_level_match(expected_level: str, candidate_level: str) -> bool:
    """
    Determine if the candidate's education level meets or exceeds the expected level.
    If expected level is 'No expectation on education level', always return True.
    """

    # Priority order for comparison (higher number = higher level)
    priority = {
        "PhD": 5,
        "Master's": 4,
        "Bachelor's": 3,
        "Diploma": 2,
        "High School": 1,
        "Other": 0
    }

    if expected_level == "No expectation on education level":
        return True

    # Handle missing or unknown candidate education level
    if candidate_level not in priority:
        candidate_level = "Other"

    # Compare priority
    return priority.get(candidate_level, 0) >= priority.get(expected_level, 0)

def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", " ", text.lower()).strip()



#COMPUTE SKILL MATCH

def compute_skill_match(resume_technical_skills, job_expected_technical_skills, candidate_name, job_id):
    """
    Compute % skill match and structured breakdown.
    """
    resume_skills_lower = [skill.lower() for skill in resume_technical_skills]
    job_skills_lower = [skill.lower() for skill in job_expected_technical_skills]

    matched_skills = []
    missing_skills = []

    for job_skill in job_skills_lower:
        matched = False
        for resume_skill in resume_skills_lower:
            fuzzy_score = fuzz.partial_ratio(job_skill, resume_skill)
            if fuzzy_score > 80:
                matched_skills.append(job_skill)
                matched = True
                break
        if not matched:
            job_doc = nlp(job_skill)
            for resume_skill in resume_skills_lower:
                resume_doc = nlp(resume_skill)
                if job_doc.similarity(resume_doc) > 0.8:
                    matched_skills.append(job_skill)
                    matched = True
                    break
        if not matched:
            missing_skills.append(job_skill)

    matched_skills = list(set(matched_skills))
    match_pct = (len(matched_skills) / len(job_skills_lower)) * 100 if job_skills_lower else 0.0

    breakdown = {
        "expected_skills": job_expected_technical_skills,
        "candidate_skills": resume_technical_skills,
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "match_pct": round(match_pct, 2)
    }

    return match_pct, breakdown



#ANALYZE COVER LETTER

def generate_cover_letter_analysis_prompt(cover_letter: str, job: dict, resume_text: str = "") -> str:
    """
    Generates a structured GPT prompt to analyze a candidate's cover letter using
    metrics based on industry best practices, including JD alignment, specificity,
    company fit, and structure/tone.
    """

    system_instructions = (
        "You are a professional recruiter AI evaluating cover letters for job applications.\n"
        "Your goal is to rate the cover letter using the following four metrics: JD Alignment,\n"
        "Evidence & Specificity, Company Fit & Motivation, and Structure & Tone.\n"
        "You must return your evaluation as valid JSON.\n"
        "Do not output prose, comments, or markdown formatting.\n"
        "All scores must be integers between 0 and 100.\n"
        "For each score, give a 1-2 sentence explanation.\n"
        "Think step by step internally before generating your final JSON output."
    )

    examples = """
### Examples:

- Example 1 (Excellent Cover Letter):
{
  "jd_alignment_score": {"score": 95, "explanation": "Candidate clearly addressed multiple core responsibilities from the JD, such as executive stakeholder engagement and technical debugging."},
  "evidence_score": {"score": 90, "explanation": "Gives specific achievements ($10M in revenue), technical projects, and quantified impact."},
  "company_fit_score": {"score": 92, "explanation": "References Adyen's vision and diversity, showing personal alignment with values."},
  "structure_tone_score": {"score": 88, "explanation": "Structured with intro, skill alignment, motivation, and conclusion. Tone is confident and professional."},
  "final_ai_score": 91
}

- Example 2 (Mid-level Cover Letter):
{
  "jd_alignment_score": {"score": 70, "explanation": "Mentions responsibilities generally but not tied to specific job bullet points."},
  "evidence_score": {"score": 60, "explanation": "Has some examples, but lacks numbers and depth."},
  "company_fit_score": {"score": 50, "explanation": "No company-specific mention, just generic excitement."},
  "structure_tone_score": {"score": 75, "explanation": "Follows intro-body-close, tone is clear and formal."},
  "final_ai_score": 64
}

- Example 3 (Weak Cover Letter):
{
  "jd_alignment_score": {"score": 40, "explanation": "Barely refers to the job posting, only vague skills mentioned."},
  "evidence_score": {"score": 30, "explanation": "No numbers, impact, or projects are described."},
  "company_fit_score": {"score": 25, "explanation": "Completely generic, could apply to any company."},
  "structure_tone_score": {"score": 55, "explanation": "Some basic structure, tone feels stiff or robotic."},
  "final_ai_score": 38
}
"""

    jd_details = f"""
### Job Description Snapshot:
Responsibilities:
{job.get('expected_responsibilities', 'N/A')}

Required Technical Skills:
{job.get('expected_technical_skills', [])}

Required Soft Skills:
{job.get('expected_soft_skills', [])}

Required Certifications:
{job.get('expected_certifications', [])}

Company Culture / Environment:
{job.get('expected_work_environment', '')}
"""

    prompt = f"""
{system_instructions}

### Cover Letter to Evaluate:
{cover_letter.strip()}

{jd_details}

{examples}

### Evaluation Instructions:
- Base your evaluation strictly on the job expectations above.
- Rate each category 0 to 100 and explain each rating.
- Be detailed and non-generic. Avoid vague feedback.
- Return the output in this format:

{{
  "jd_alignment_score": {{"score": <int>, "explanation": "..."}},
  "evidence_score": {{"score": <int>, "explanation": "..."}},
  "company_fit_score": {{"score": <int>, "explanation": "..."}},
  "structure_tone_score": {{"score": <int>, "explanation": "..."}},
  "final_ai_score": <int>
}}

Return only valid JSON.
"""

    return prompt.strip()


def analyze_cover_letter_authenticity(resume_text: str, cover_letter: str, job: dict = None) -> dict:
    """
    Evaluates the cover letter using advanced GPT prompting based on job expectations.
    Returns structured scores across four categories with explanations, plus a final AI score.
    """

    if not cover_letter.strip():
        return {
            "jd_alignment_score": {"score": 0, "explanation": "No cover letter provided."},
            "evidence_score": {"score": 0, "explanation": "No cover letter provided."},
            "company_fit_score": {"score": 0, "explanation": "No cover letter provided."},
            "structure_tone_score": {"score": 0, "explanation": "No cover letter provided."},
            "final_ai_score": 0,
            "ai_writing_score": 0
        }

    # Build the evaluation prompt using the custom prompt generator
    prompt = generate_cover_letter_analysis_prompt(
        cover_letter=cover_letter,
        job=job or {},
        resume_text=resume_text
    )

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Return only valid JSON. Do not include any prose or formatting."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )

        output = response.choices[0].message.content.strip()

        # Clean up accidental markdown
        if output.startswith("```"):
            output = output.strip("`")
            if output.startswith("json"):
                output = output[4:].strip()

        gpt_data = json.loads(output)

        # Fallback field for backward compatibility
        gpt_data["ai_writing_score"] = gpt_data.get("final_ai_score", 0)

        return gpt_data

    except Exception as e:
        print("Cover letter GPT analysis failed:", e)
        return {
            "jd_alignment_score": {"score": 0, "explanation": "Evaluation error."},
            "evidence_score": {"score": 0, "explanation": "Evaluation error."},
            "company_fit_score": {"score": 0, "explanation": "Evaluation error."},
            "structure_tone_score": {"score": 0, "explanation": "Evaluation error."},
            "final_ai_score": 0,
            "ai_writing_score": 0
        }



#EXTRACT LINKS

def is_real_domain(url: str) -> bool:
    """
    Validates if the domain in the URL can actually be resolved in DNS.
    """
    try:
        hostname = url.replace("https://", "").replace("http://", "").split("/")[0]
        socket.gethostbyname(hostname)
        return True
    except Exception as e:
        print(f"Domain resolution failed for {url}: {e}")
        return False

def extract_links_from_resume(resume_text: str, pdf_path: str = None) -> dict:
    """
    Robust link extraction with domain resolution.
    Returns dict: {'portfolio_url', 'github_url', 'linkedin_url'}
    """
    links = {"portfolio_url": "", "github_url": "", "linkedin_url": ""}
    print("\n===== Starting FINAL robust link extraction =====")

    all_urls = set()

    #  Embedded hyperlinks
    if pdf_path:
        try:
            doc = fitz.open(pdf_path)
            for page_num, page in enumerate(doc):
                for link in page.get_links():
                    uri = link.get("uri")
                    if uri and validators.url(uri.strip()):
                        all_urls.add(uri.strip())
                        print(f"Embedded link found on page {page_num + 1}: {uri}")
            doc.close()
        except Exception as e:
            print("Error extracting embedded PDF links:", e)

    #  Text-based links (urlextract)
    extractor = URLExtract()
    try:
        extracted_urls = extractor.find_urls(resume_text)
        for url in extracted_urls:
            normalized_url = url if url.startswith("http") else "https://" + url
            if validators.url(normalized_url.strip()):
                all_urls.add(normalized_url.strip())
        print(f"URLs found by urlextract: {extracted_urls}")
    except Exception as e:
        print("Error with urlextract:", e)

    #  Fallback regex for real http(s) links
    regex_pattern = re.compile(r'https?://[^\s]+', re.IGNORECASE)
    regex_urls = regex_pattern.findall(resume_text)
    for url in regex_urls:
        if validators.url(url.strip()):
            all_urls.add(url.strip())
    print(f"URLs found by fallback regex: {regex_urls}")

    print(f"Unique valid URLs after normalization and syntax validation: {all_urls}")

    #  Final domain resolution validation and classification
    for url in all_urls:
        if not is_real_domain(url):
            print(f"Skipping fake URL (does not resolve): {url}")
            continue

        url_lower = url.lower()
        if "github" in url_lower and not links["github_url"]:
            links["github_url"] = url
            print("Classified as GitHub URL.")
        elif "linkedin" in url_lower and not links["linkedin_url"]:
            links["linkedin_url"] = url
            print("Classified as LinkedIn URL.")
        elif not links["portfolio_url"]:
            links["portfolio_url"] = url
            print("Classified as Portfolio URL.")

    print("\n===== Final Links Dictionary =====")
    print(links)
    return links



#COMPUTE RESUME QUALITY SCORE

def check_structure(text: str) -> Tuple[float, str]:
    required_sections = ["experience", "education", "skills", "contact"]
    optional_sections = ["certifications", "projects", "awards", "summary"]
    text_lower = text.lower()

    found_required = sum(1 for s in required_sections if s in text_lower)
    found_optional = sum(1 for s in optional_sections if s in text_lower)

    score = (found_required / len(required_sections)) * 0.7 + (found_optional / len(optional_sections)) * 0.3
    explanation = f"Found {found_required} of 4 required sections and {found_optional} optional sections."
    return score, explanation


def check_section_headers(text: str) -> Tuple[float, str]:
    headers = []
    header_keywords = ["experience", "education", "skills", "projects", "summary", "contact", "certifications"]
    lines = text.splitlines()

    for line in lines:
        clean_line = line.strip().lower().rstrip(":")
        if clean_line in header_keywords or line.strip().isupper() or line.strip().endswith(":"):
            headers.append(line.strip())

    score = min(len(headers) / 6, 1.0)
    return score, f"Detected {len(headers)} header-like lines: {headers[:5]}{'...' if len(headers) > 5 else ''}"


def check_word_count(text: str) -> Tuple[float, str]:
    words = re.findall(r"\b\w+\b", text)
    word_count = len(words)

    if 150 <= word_count <= 350:
        return 1.0, f"Word count is {word_count}, optimal range (150–350) for a concise resume."
    elif 100 <= word_count < 150 or 350 < word_count <= 500:
        return 0.7, f"Word count is {word_count}, slightly outside ideal range. Consider trimming or elaborating."
    else:
        return 0.4, f"Word count is {word_count}, outside expected range. May affect clarity or ATS parsing."



def check_bullet_points(text: str) -> Tuple[float, str]:
    bullet_symbols = r"[-•●▪‣*→➤◉⦿‣‣●•■▶➔‣∙‣⦾•★]"
    bullets = re.findall(rf"^\s*{bullet_symbols}", text, re.MULTILINE)
    bullet_count = len(bullets)

    if bullet_count >= 10:
        score = 1.0
    elif bullet_count >= 5:
        score = 0.7
    elif bullet_count >= 2:
        score = 0.5
    else:
        score = 0.3

    return score, f"Detected {bullet_count} bullet points using standard or common Unicode symbols."



def check_contact_info(text: str) -> Tuple[float, str]:
    score = 0
    explanation = []

    if re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text):
        score += 0.5
        explanation.append("Email found.")
    if re.search(r"\+?\d{7,}", text):
        score += 0.3
        explanation.append("Phone number found.")
    extractor = URLExtract()
    links = extractor.find_urls(text)
    if any("linkedin" in link.lower() or "github" in link.lower() for link in links):
        score += 0.2
        explanation.append("Professional link found.")
    
    return min(score, 1.0), " ".join(explanation) or "No contact info found."


def check_formatting(text: str) -> Tuple[float, str]:
    lines = text.splitlines()
    empty_lines = sum(1 for line in lines if line.strip() == "")
    long_lines = sum(1 for line in lines if len(line.strip()) > 120)

    if empty_lines >= 3 and long_lines <= 10:
        return 1.0, f"Good formatting: {empty_lines} line breaks found, and only {long_lines} very long lines detected."
    elif empty_lines >= 2:
        return 0.7, f"Moderate formatting: {empty_lines} breaks, {long_lines} long lines. Could improve spacing."
    else:
        return 0.4, f"Poor formatting: Only {empty_lines} blank lines and {long_lines} long lines. Resume may look dense."


PAST_TENSE_VERBS = {
    "managed", "led", "developed", "created", "implemented", "designed", "built", "organized",
    "analyzed", "engineered", "executed", "presented", "oversaw", "wrote", "launched", "conducted",
    "coordinated", "delivered", "debugged", "mentored", "supervised", "reviewed", "streamlined",
    "resolved", "translated", "integrated", "advised", "drafted", "initiated", "monitored"
}

PRESENT_TENSE_VERBS = {
    "manage", "lead", "develop", "create", "implement", "design", "build", "organize",
    "analyze", "engineer", "execute", "present", "oversee", "write", "launch", "conduct",
    "coordinate", "deliver", "debug", "mentor", "supervise", "review", "streamline",
    "resolve", "translate", "integrate", "advise", "draft", "initiate", "monitor"
}


def check_consistency(text: str) -> Tuple[float, str]:
    doc = nlp(text)

    past_matches = []
    present_matches = []

    for token in doc:
        if token.pos_ == "VERB":
            lemma = token.lemma_.lower()
            verb = token.text.lower()
            if lemma in PAST_TENSE_VERBS or verb in PAST_TENSE_VERBS:
                past_matches.append(token.text)
            elif lemma in PRESENT_TENSE_VERBS or verb in PRESENT_TENSE_VERBS:
                present_matches.append(token.text)

    past_count = len(past_matches)
    present_count = len(present_matches)
    total = past_count + present_count

    if total == 0:
        score = 0.6
        explanation = "Could not detect enough action verbs to evaluate consistency. May be due to short length or formatting."
    else:
        imbalance = abs(past_count - present_count) / total
        if imbalance < 0.2:
            score = 1.0
            explanation = f"Tense usage is consistent. Past: {past_count}, Present: {present_count}."
        elif imbalance < 0.5:
            score = 0.7
            explanation = f"Moderate inconsistency in verb tense. Past: {past_count}, Present: {present_count}."
        else:
            score = 0.4
            explanation = f"High tense inconsistency. Past: {past_count}, Present: {present_count}."

    if past_matches:
        explanation += f" Sample past verbs: {', '.join(past_matches[:5])}."
    if present_matches:
        explanation += f" Sample present verbs: {', '.join(present_matches[:5])}."

    return score, explanation



# Safe, default fallback
def check_readability(resume_text):
    try:
        import language_tool_python
        tool = language_tool_python.LanguageTool('en-US')
        matches = tool.check(resume_text)
        grammar_score = max(0, 1 - len(matches) / max(1, len(resume_text.split())))
        explanation = f"Found {len(matches)} grammar issues."
    except Exception as e:
        grammar_score = 0.5
        explanation = f"Grammar check skipped: {str(e)}"
    return grammar_score, explanation

def check_readability_flesch(text: str) -> Tuple[float, str]:
    flesch_score = textstat.flesch_reading_ease(text)

    # Resume-normalized interpretation
    if flesch_score >= 60:
        score = 1.0
        explanation = f"Very easy to read (Flesch score: {flesch_score:.1f}). Clear structure and phrasing."
    elif flesch_score >= 40:
        score = 0.8
        explanation = f"Readable (Flesch score: {flesch_score:.1f}). May contain some technical phrasing."
    elif flesch_score >= 20:
        score = 0.6
        explanation = f"Dense (Flesch score: {flesch_score:.1f}). Resume may use compact or technical phrases."
    else:
        score = 0.4
        explanation = f"Very dense (Flesch score: {flesch_score:.1f}). Could benefit from simpler sentence flow."

    return score, explanation

def check_ats_format(file_format: str) -> Tuple[float, str]:
    if file_format.lower() in ["pdf", "docx"]:
        return 1.0, f"File format is {file_format}, ATS-friendly."
    return 0.5, f"File format {file_format} may not be ATS-friendly."


def compute_resume_quality_score(resume_text: str, file_format: str = "pdf") -> Tuple[float, Dict[str, Dict]]:
    breakdown = {}

    # Run all quality check functions
    structure_score, structure_expl = check_structure(resume_text)
    formatting_score, formatting_expl = check_formatting(resume_text)
    word_count_score, word_count_expl = check_word_count(resume_text)
    consistency_score, consistency_expl = check_consistency(resume_text)
    contact_score, contact_expl = check_contact_info(resume_text)
    bullet_score, bullet_expl = check_bullet_points(resume_text)
    header_score, header_expl = check_section_headers(resume_text)
    ats_score, ats_expl = check_ats_format(file_format)
    flesch_score, flesch_expl = check_readability_flesch(resume_text)
    grammar_score, grammar_expl = check_readability(resume_text)

    # Assign weights
    weights = {
        "structure": 0.12,
        "formatting": 0.12,
        "word_count": 0.08,
        "consistency": 0.10,
        "contact_info": 0.08,
        "bullet_points": 0.00,  # still included in breakdown but doesn't affect final score
        "section_headers": 0.10,
        "ats_compatibility": 0.10,
        "readability_flesch": 0.10,
        "readability_grammar": 0.20
    }

    # Save breakdown with contribution
    for metric, (score, explanation) in {
        "structure": (structure_score, structure_expl),
        "formatting": (formatting_score, formatting_expl),
        "word_count": (word_count_score, word_count_expl),
        "consistency": (consistency_score, consistency_expl),
        "contact_info": (contact_score, contact_expl),
        "bullet_points": (bullet_score, bullet_expl),
        "section_headers": (header_score, header_expl),
        "ats_compatibility": (ats_score, ats_expl),
        "readability_flesch": (flesch_score, flesch_expl),
        "readability_grammar": (grammar_score, grammar_expl),
    }.items():
        score = round(score, 2)
        contribution = round(score * weights[metric] * 100, 2)
        breakdown[metric] = {
            "score": score,
            "explanation": explanation,
            "contribution": contribution
        }

    final_score = sum(breakdown[key]["score"] * weights[key] for key in weights)
    return round(final_score * 100, 2), breakdown




#EXTRACT SKILLS

def detect_technical_skills_from_text(text: str) -> List[str]:
    known_technical_skills = [
        # Programming Languages
        "Python", "C++", "C#", "C", "Java", "JavaScript", "TypeScript", "SQL", "HTML", "CSS", "Lua",
        # Engines & Frameworks
        "Unity", "Unreal Engine", "Godot", "Construct 3", "Phaser", "Node.js", "React", "Angular", "Django", "Flask",
        # Game Dev / Graphics
        "OpenGL", "Shader Programming", "Blender", "MagicaVoxel", "ZBrush", "Maya", "3ds Max", "Photoshop",
        # AI & ML
        "TensorFlow", "PyTorch", "Keras", "YOLOv5", "Machine Learning", "Reinforcement Learning", "ML-Agents",
        # Tools & Platforms
        "Git", "GitHub", "SVN", "Jira", "Notion", "Trello", "Docker", "AWS", "Firebase", "MongoDB", "PostgreSQL", "MySQL",
        # OS & Scripting
        "Linux", "Ubuntu", "Shell Scripting", "Bash", "PowerShell", "Windows", "MacOS",
        # Simulation & Robotics
        "ROS1", "ROS2", "Gazebo", "MoveIt", "Simulink", "MATLAB", "Autodesk Fusion 360", "AutoCAD",
        # Other
        "MS Office", "Excel Macros", "Marmoset Toolbag", "Mixamo", "Perforce", "PBR Workflow", "LOD Creation"
    ]

    text_lower = text.lower()
    found = set()

    for skill in known_technical_skills:
        # Check for token match or partial match in a case-insensitive way
        pattern = r'\b' + re.escape(skill.lower()) + r'\b'
        if re.search(pattern, text_lower):
            found.add(skill)

    return sorted(found)

def extract_technical_skills(text: str) -> List[str]:
    import unicodedata

    lines = text.splitlines()
    skills = []
    capture = False

    for line in lines:
        line = unicodedata.normalize("NFKC", line.strip())

        # Start capturing if we hit the technical skills header
        if re.search(r"technical\s+skills", line.lower()):
            capture = True
            continue

        # Stop capturing if we hit another section (non-bullet, non-empty)
        if capture and line and not (line.startswith("-") or line.startswith("*")):
            break

        # Accept only ASCII-compatible bullets ("-", "*")
        if capture and (line.startswith("-") or line.startswith("*")):
            cleaned = re.sub(r"^[-*]\s*", "", line).strip()
            if cleaned:
                skills.append(cleaned)

    return skills

def extract_soft_skills(resume_text: str, cover_letter: str = "") -> List[str]:
    soft_skills_keywords = [
        "communication", "teamwork", "collaboration", "adaptability", "leadership",
        "problem-solving", "creativity", "initiative", "critical thinking",
        "time management", "empathy", "work ethic", "attention to detail",
        "decision making", "multitasking", "flexibility", "dependability"
    ]

    combined_text = f"{resume_text}\n{cover_letter}".lower()
    found = set()

    for skill in soft_skills_keywords:
        if skill in combined_text:
            found.add(skill)

    return list(found)

def format_list(items: List[Any]) -> str:
    safe_items = []
    for item in items:
        try:
            if item and hasattr(item, "value") and item.value is not None:
                safe_items.append(str(item.value))
        except Exception:
            continue
    return ', '.join(safe_items)



def check_portfolio_match(expected_portfolio: str, extracted_portfolio: str) -> Tuple[bool, str]:
    """
    Returns boolean match and explanation.
    """
    if not expected_portfolio:
        return True, "No portfolio requirement specified by job."
    if extracted_portfolio and expected_portfolio.lower() in extracted_portfolio.lower():
        return True, f"Portfolio link matches expected portfolio: {extracted_portfolio}"
    return False, "No matching portfolio link found."


def check_certifications_match(expected_certs: list, candidate_certs: list) -> Tuple[float, str]:
    if not expected_certs:
        return 100.0, "No certifications required for this job."
    matched_scores = []
    for exp_cert in expected_certs:
        for cand_cert in candidate_certs:
            score = fuzz.partial_ratio(exp_cert.lower(), cand_cert.lower())
            if score >= 70:  # Accept partial matches above 70
                matched_scores.append(exp_cert)
                break
    percentage = (len(matched_scores) / len(expected_certs)) * 100
    explanation = f"Matched certifications: {matched_scores}" if matched_scores else "No relevant certifications found."
    return percentage, explanation

def check_soft_skills_match(expected_skills: list, candidate_skills: list) -> Tuple[float, str]:
    if not expected_skills:
        return 100.0, "No soft skills required for this job."
    matched_scores = []
    for exp_skill in expected_skills:
        for cand_skill in candidate_skills:
            score = fuzz.partial_ratio(exp_skill.lower(), cand_skill.lower())
            if score >= 70:
                matched_scores.append(exp_skill)
                break
    percentage = (len(matched_scores) / len(expected_skills)) * 100
    explanation = f"Matched soft skills: {matched_scores}" if matched_scores else "No relevant soft skills found."
    return percentage, explanation


def check_language_match(expected_language: str, candidate_languages: list) -> Tuple[bool, str]:
    """
    Check if candidate language matches expected language (if any).
    """
    if not expected_language:
        return True, "No language requirement specified."
    for lang in candidate_languages:
        if expected_language.lower() in lang.lower():
            return True, f"Matched expected language: {expected_language}"
    return False, f"Expected language ({expected_language}) not found in candidate languages."




#EVALUATE RESUME

def get_client_preferences(client_id):
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("""
        SELECT custom_eval_prompt, skill_match_weight, education_match_weight, experience_match_weight,
               portfolio_match_weight, certifications_match_weight, soft_skills_weight,
               language_weight, previous_role_alignment_weight
        FROM clients WHERE id = %s
    """, (client_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return None, {}

    prompt = row[0]
    weights = {
        "skill_match": row[1],
        "education_match": row[2],
        "experience_match": row[3],
        "portfolio_match": row[4],
        "certifications_match": row[5],
        "soft_skills_match": row[6],
        "language_match": row[7],
        "previous_role_alignment": row[8]
    }
    return prompt, weights


def compute_resume_final_score(job, candidate_data, client_weights=None):
    """
    Calculate a weighted final candidate score and breakdown per evaluation dimension.
    Uses custom client weights if provided and valid (sums to 100).
    """
    default_weights = {
        "skill_match": 0.35,
        "education_match": 0.10,
        "experience_match": 0.15,
        "portfolio_match": 0.05,
        "certifications_match": 0.10,
        "soft_skills_match": 0.05,
        "language_match": 0.05,
        "previous_role_alignment": 0.15,
    }

    weights = default_weights
    if client_weights:
        total = sum([v for v in client_weights.values() if v is not None])
        if round(total) == 100:
            weights = {k: (client_weights.get(k, 0) or 0) / 100 for k in default_weights}

    breakdown = {}

    # Skill Match
    skill_score = candidate_data.get("skills_matched_pct", 0)
    breakdown["skill_match"] = {
        "score": skill_score,
        "explanation": f"Skills match {round(skill_score, 1)}%, based on job and candidate's listed skills.",
    }

    # Education
    edu_match = candidate_data.get("education_level_match", False)
    edu_score = 100 if edu_match else 0
    breakdown["education_match"] = {
        "score": edu_score,
        "explanation": "Candidate has education level that meets job requirement." if edu_match else "Candidate's education does not meet the job requirement.",
    }

    # Experience
    expected_range = job.get("expected_experience_range", "No expectation on experience")
    expected_level = job.get("experience_level", "No expectation on experience")
    candidate_years = candidate_data.get("experience_years", 0)
    prev_titles = candidate_data.get("previous_job_titles", [])
    job_title = job.get("job_title", "Unknown")

    exp_match, exp_explanation, role_score = check_experience_match(
        expected_range, expected_level, candidate_years, prev_titles, job_title
    )
    exp_score = 100 if exp_match else max(0, int((candidate_years / 10) * 100))
    breakdown["experience_match"] = {
        "score": exp_score,
        "explanation": exp_explanation,
    }

    # Portfolio
    portfolio_required = job.get("requires_portfolio", False)
    has_portfolio = candidate_data.get("portfolio_url") != ""
    portfolio_score = 100 if (not portfolio_required or has_portfolio) else 0
    breakdown["portfolio_match"] = {
        "score": portfolio_score,
        "explanation": "Candidate has portfolio." if portfolio_score == 100 else "No portfolio submitted and the job requires one." if portfolio_required else "No portfolio requirement specified by job.",
    }

    # Certifications
    certs_required = job.get("expected_certifications", [])
    certs_candidate = candidate_data.get("certifications", [])
    cert_score = 100 if not certs_required else int((len(set(certs_required) & set(certs_candidate)) / len(certs_required)) * 100)
    cert_explanation = "No certifications required for this job." if not certs_required else f"Matched {cert_score}% of required certifications."
    breakdown["certifications_match"] = {
        "score": cert_score,
        "explanation": cert_explanation,
    }

    # Soft Skills
    soft_required = job.get("expected_soft_skills", [])
    soft_candidate = candidate_data.get("soft_skills", [])
    soft_score = 100 if not soft_required else int((len(set(soft_required) & set(soft_candidate)) / len(soft_required)) * 100)
    breakdown["soft_skills_match"] = {
        "score": soft_score,
        "explanation": "Candidate matches soft skill requirements." if soft_score == 100 else "No soft skills required for this job." if not soft_required else f"Matched {soft_score}% of required soft skills.",
    }

    # Language Match
    lang_required = job.get("expected_languages", [])
    lang_candidate = candidate_data.get("languages", [])
    lang_score = 100 if not lang_required else int((len(set(lang_required) & set(lang_candidate)) / len(lang_required)) * 100)
    breakdown["language_match"] = {
        "score": lang_score,
        "explanation": "Candidate meets language requirement." if lang_score == 100 else "No language requirement specified." if not lang_required else f"Matched {lang_score}% of required languages.",
    }

    # Previous Role Alignment
    previous_role_score = candidate_data.get("previous_role_score", 0)
    breakdown["previous_role_alignment"] = {
        "score": previous_role_score,
        "explanation": f"We also looked at similar roles and found an alignment of {previous_role_score}%.",
    }

    # Add contributions
    for key in breakdown:
        score = breakdown[key].get("score", 0)
        weight = weights.get(key, 0)
        breakdown[key]["contribution"] = round(score * weight, 2)

    final_score = round(sum(breakdown[k]["contribution"] for k in weights), 2)
    return final_score, breakdown


def evaluation_prompting(
    job: dict,
    candidate_name: str,
    experience_years: float,
    education_level: str,
    skill_match_pct: float,
    certifications: list,
    final_technical: list,
    final_soft: list,
    links: dict,
    quality_score: float,
    cover_letter_analysis_dict: dict,
    breakdown_text: str,
    quality_breakdown: dict,
    final_score: float,
    score_breakdown: dict,
    cover_letter: str = "",
    custom_prompt: str = ""  # new optional parameter
) -> str:
    """
    Generates GPT prompt for evaluating a resume. Returns strengths/weaknesses as JSON arrays (for frontend).
    """

    system_instructions = (
        "You are a professional recruiter AI evaluating a candidate for a specific job role.\n"
        "Return valid JSON only. No prose, no extra commentary.\n"
        "Think step by step internally, but only provide the final JSON.\n"
        "Focus exclusively on job expectations — no mention of generic traits like employment gaps unless explicitly related to job expectations.\n"
    )

    examples = """
### Examples:
- Example 1 (Excellent Fit):
{
    "summary": "Gunabalan Lingam has outstanding alignment with the job's technical and soft skills expectations, including repeated proven mentions of C# in both work experience and projects, making him an ideal candidate.",
    "strengths": ["Proven C# skills in work and projects", "Exceeds expected certifications", "Exceeds soft skills expectations"],
    "weaknesses": []
}

- Example 2 (Good Fit):
{
    "summary": "Gunabalan Lingam meets all essential technical and soft skills, with slightly above expected experience and clear evidence of certifications, making him a strong fit.",
    "strengths": ["Good skill match percentage", "Above expected experience", "Relevant certifications"],
    "weaknesses": []
}

- Example 3 (Moderate Fit):
{
    "summary": "Gunabalan Lingam demonstrates most required technical skills and experience, though missing some certifications and falling short in a few soft skill areas.",
    "strengths": ["Strong technical skills alignment", "Good experience", "Some relevant soft skills"],
    "weaknesses": ["Missing some required certifications", "Lacks a few expected soft skills"]
}

- Example 4 (Weak Fit):
{
    "summary": "Gunabalan Lingam lacks several of the job's key technical skills and has below the required experience.",
    "strengths": ["Some soft skills alignment"],
    "weaknesses": ["Missing key technical skills", "Below expected experience", "No relevant certifications"]
}

- Example 5 (Minimal Fit):
{
    "summary": "Gunabalan Lingam has very limited alignment with the job's technical and soft skills expectations, and does not meet the experience or certification requirements.",
    "strengths": [],
    "weaknesses": ["Missing all key technical skills", "No relevant certifications", "Significantly below experience requirement"]
}
"""

    prompt = f"""
{system_instructions}

### Few-shot Examples:
{examples}

### Job Details:
- Title: {job.get('job_title', 'Unknown')}
- Description: {job.get('job_description', 'N/A')}
- Expected Responsibilities: {job.get('expected_responsibilities', 'No specific responsibilities provided')}
- Expected Technical Skills: {job.get('expected_technical_skills', [])}
- Expected Soft Skills: {job.get('expected_soft_skills', [])}
- Expected Certifications: {job.get('expected_certifications', [])}
- Expected Education Level: {job.get('expected_education_level', 'No expectation')}
- Expected Experience Range: {job.get('expected_experience_range', 'No expectation')}
- Expected Languages: {job.get('expected_languages', [])}
- Expected Tools: {job.get('expected_tools', [])}

### Candidate Details:
- Name: {candidate_name}
- Experience Years: {experience_years}
- Education Level: {education_level}
- Skills Matched (%): {skill_match_pct}
- Certifications: {certifications}
- Technical Skills: {final_technical}
- Soft Skills: {final_soft}
- Portfolio URL: {links.get('portfolio_url', '')}
- Languages: {links.get('languages', [])}
- Resume Quality Score: {quality_score}
- Cover Letter Analysis: {cover_letter_analysis_dict if cover_letter else 'No cover letter'}

### Additional Data:
- Skills Match Breakdown: {breakdown_text}
- Resume Quality Breakdown: {quality_breakdown}
- Final Numeric Score: {final_score}
- Score Breakdown: {score_breakdown}

### Evaluation Rules:
{custom_prompt.strip() if custom_prompt else ""}

- Start the summary with the candidate's name.
- Make the summary longer and more technical — 1 to 2 sentences clearly stating why the candidate is or isn’t a good fit.
- Highlight “proven” skills when repeated in multiple sections.
- Provide strengths and weaknesses as **JSON arrays of strings** (not bullet string).
- If there are no genuine strengths or weaknesses based on job expectations, use an empty array `[]`.
- Do not mention generic gaps or traits unless relevant to job expectations.

Return your evaluation in this exact JSON format:

{{
  "summary": "<A clear, technical summary>",
  "strengths": ["<point1>", "<point2>", "..."],
  "weaknesses": ["<point1>", "<point2>", "..."]
}}

Only return valid JSON.
"""

    return prompt.strip()


def evaluate_resume(resume_data: Dict[str, Any], job: Dict[str, Any], cover_letter: str = "", pdf_path: str = None, client_id=None) -> Dict[str, Any]:
    from collections import defaultdict

    technical_skills_field = resume_data.get("technical_skills")
    certifications = resume_data.get("certifications")
    education_raw = resume_data.get("education", "")
    experience_field = resume_data.get("professional_experience", None)

    def get_value(x): return x.value if hasattr(x, "value") else x

    # Flatten parsed technical skills (from Mindee)
    raw_technical_skills = []
    if technical_skills_field:
        values = technical_skills_field.values
        if isinstance(values, list):
            for skill in values:
                val = get_value(skill)
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        raw_technical_skills.extend(parsed)
                    else:
                        raw_technical_skills.append(val)
                except:
                    raw_technical_skills.append(val)

    resume_text = '\n'.join([f"{k}: {get_value(v)}" for k, v in resume_data.items()])
    extracted_section_skills = extract_technical_skills(resume_text)
    extracted_soft_skills = extract_soft_skills(resume_text, cover_letter)
    inferred_from_body = detect_technical_skills_from_text(resume_text)
    final_technical = sorted(set(extracted_section_skills + inferred_from_body + raw_technical_skills))
    final_soft = sorted(set(extracted_soft_skills))

    certifications_list = certifications.values if certifications else []
    experience_entries = experience_field.values if experience_field and hasattr(experience_field, "values") else []
    experience_years = calculate_experience_years(experience_entries)

    education_level = extract_education_level(get_value(education_raw), resume_text)
    expected_experience_range = job.get("expected_experience_range", "No expectation on experience")
    expected_experience_level = job.get("experience_level", "No expectation on experience")
    expected_education_level = job.get("expected_education_level", "No expectation on education level")
    education_level_match = check_education_level_match(expected_education_level, education_level)

    links = extract_links_from_resume(resume_text, pdf_path)
    quality_score, quality_breakdown = compute_resume_quality_score(resume_text, file_format="pdf")
    cover_letter_analysis_dict = analyze_cover_letter_authenticity(resume_text, cover_letter, job)
    ai_score = cover_letter_analysis_dict.get("ai_writing_score", 0)

    expected_technical_skills = job.get("expected_technical_skills", [])
    candidate_name = get_value(resume_data.get("full_name", "unknown"))
    job_id = job.get("id")
    
    # CHANGED: get structured breakdown instead of plain text
    skill_match_pct, structured_breakdown = compute_skill_match(
        final_technical,
        expected_technical_skills,
        candidate_name,
        job_id
    )

    candidate_data = {
        "skills_matched_pct": skill_match_pct,
        "education_level_match": education_level_match,
        "experience_years": experience_years,
        "previous_job_titles": [get_value(exp.job_title) for exp in experience_entries if hasattr(exp, "job_title")],
        "portfolio_url": links.get("portfolio_url", ""),
        "certifications": format_list(certifications_list).split(", "),
        "soft_skills": final_soft,
        "languages": [get_value(resume_data.get("languages", ""))]
    }
    
    custom_prompt = ""
    custom_weights = {}

    if client_id:
        custom_prompt, custom_weights = get_client_preferences(client_id)


    final_score, score_breakdown = compute_resume_final_score(job, candidate_data, client_weights=custom_weights)

    # Use structured breakdown for prompting but also stringify for GPT context
    breakdown_text = json.dumps(structured_breakdown, indent=2)

    prompt = evaluation_prompting(
        job=job,
        candidate_name=candidate_name,
        experience_years=experience_years,
        education_level=education_level,
        skill_match_pct=skill_match_pct,
        certifications=format_list(certifications_list).split(", "),
        final_technical=final_technical,
        final_soft=final_soft,
        links=links,
        quality_score=quality_score,
        cover_letter_analysis_dict=cover_letter_analysis_dict,
        breakdown_text=breakdown_text,
        quality_breakdown=quality_breakdown,
        final_score=final_score,
        score_breakdown=score_breakdown,
        cover_letter=cover_letter,
        custom_prompt=custom_prompt
        
    )

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Return valid JSON only. No prose or comments."},
            {"role": "user", "content": prompt}
        ],
        temperature=0
    )

    raw_output = response.choices[0].message.content.strip()
    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        if raw_output.startswith("json"):
            raw_output = raw_output[4:].strip()

    try:
        gpt_data = json.loads(raw_output)
        gpt_data["strengths"] = gpt_data.get("strengths", [])
        gpt_data["weaknesses"] = gpt_data.get("weaknesses", [])
        gpt_data["summary"] = gpt_data.get("summary", "Not provided")

        gpt_data.update({
            "score": final_score,
            "score_breakdown": score_breakdown,
            "experience_years": experience_years,
            "education_level": education_level,
            "skills_matched_pct": skill_match_pct,
            "certifications": format_list(certifications_list),
            "cover_letter_analysis": cover_letter_analysis_dict,
            "ai_writing_score": ai_score,
            "technical_skills": final_technical,
            "soft_skills": final_soft,
            "resume_quality_score": quality_score,
            "resume_quality_breakdown": quality_breakdown,
            "skill_match_breakdown": structured_breakdown,
            **links
        })

        return gpt_data

    except Exception as e:
        print("Failed to parse GPT output:", e)
        return {
            "score": final_score,
            "score_breakdown": score_breakdown,
            "summary": "Qualitative evaluation error.",
            "strengths": "Not provided",
            "weaknesses": "Not provided",
            "experience_years": experience_years,
            "education_level": education_level,
            "skills_matched_pct": skill_match_pct,
            "certifications": format_list(certifications_list),
            "technical_skills": final_technical,
            "soft_skills": final_soft,
            "resume_quality_score": quality_score,
            "resume_quality_breakdown": quality_breakdown,
            "portfolio_url": links.get("portfolio_url", ""),
            "github_url": links.get("github_url", ""),
            "linkedin_url": links.get("linkedin_url", ""),
            "skill_match_breakdown": structured_breakdown
        }



#STORE TO POSTGRESQL DATABASE

def save_to_postgresql(parsed_data, gpt_result, job_title, resume_url, client_id, resume_source="form"):
    db_url = os.getenv("DATABASE_URL")
    up.uses_netloc.append("postgres")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    def safe_val(x): return x.value if hasattr(x, "value") else x or ""

    name = safe_val(parsed_data.get("full_name"))
    email = safe_val(parsed_data.get("email"))
    phone = safe_val(parsed_data.get("phone_number"))

    # Get or create job ID
    cur.execute("SELECT id FROM jobs WHERE job_title = %s AND client_id = %s LIMIT 1;", (job_title, client_id))
    row = cur.fetchone()
    job_id = row[0] if row else None
    if not job_id:
        cur.execute("INSERT INTO jobs (job_title, job_description, client_id) VALUES (%s, %s, %s) RETURNING id;",
                     (job_title, "Placeholder description", client_id))
        job_id = cur.fetchone()[0]

    # Normalize skill lists
    def normalize_skill_list(value):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except:
                return [value]
        elif isinstance(value, list):
            return [str(v) for v in value if isinstance(v, str)]
        return []

    technical_skills_list = normalize_skill_list(gpt_result.get("technical_skills", []))
    soft_skills_list = normalize_skill_list(gpt_result.get("soft_skills", []))

    # Convert strengths and weaknesses arrays to JSON strings if needed
    strengths = json.dumps(gpt_result.get("strengths", [])) if isinstance(gpt_result.get("strengths"), (list, dict)) else str(gpt_result.get("strengths", ""))
    weaknesses = json.dumps(gpt_result.get("weaknesses", [])) if isinstance(gpt_result.get("weaknesses"), (list, dict)) else str(gpt_result.get("weaknesses", ""))

    args = (
        job_id, name, email, phone, resume_url,
        gpt_result.get("score", 0), gpt_result.get("summary", ""), strengths, weaknesses,
        gpt_result.get("experience_years", 0), gpt_result.get("education_level", ""), gpt_result.get("skills_matched_pct", 0),
        gpt_result.get("certifications", ""), resume_source, gpt_result.get("portfolio_url", ""), gpt_result.get("github_url", ""),
        gpt_result.get("linkedin_url", ""), technical_skills_list, soft_skills_list,
        gpt_result.get("resume_quality_score", 0), json.dumps(gpt_result.get("resume_quality_breakdown", {})),
        json.dumps(gpt_result.get("cover_letter_analysis", {})),
        gpt_result.get("ai_writing_score", 0), json.dumps(gpt_result.get("skill_match_breakdown", {})),
        json.dumps(gpt_result.get("score_breakdown", {})),
        datetime.utcnow()
    )

    cur.execute("""
        INSERT INTO resumes (
            job_id, candidate_name, email, phone, resume_url, score, summary, strengths, weaknesses,
            experience_years, education_level, skills_matched_pct, certifications, resume_source,
            portfolio_url, github_url, linkedin_url, technical_skills, soft_skills,
            resume_quality_score, resume_quality_breakdown, cover_letter_analysis,
            ai_writing_score, skill_match_breakdown, score_breakdown, application_date
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (email, job_id) DO UPDATE
        SET phone = EXCLUDED.phone,
            score = EXCLUDED.score,
            summary = EXCLUDED.summary,
            strengths = EXCLUDED.strengths,
            weaknesses = EXCLUDED.weaknesses,
            experience_years = EXCLUDED.experience_years,
            education_level = EXCLUDED.education_level,
            skills_matched_pct = EXCLUDED.skills_matched_pct,
            certifications = EXCLUDED.certifications,
            resume_source = EXCLUDED.resume_source,
            portfolio_url = EXCLUDED.portfolio_url,
            github_url = EXCLUDED.github_url,
            linkedin_url = EXCLUDED.linkedin_url,
            technical_skills = EXCLUDED.technical_skills,
            soft_skills = EXCLUDED.soft_skills,
            resume_quality_score = EXCLUDED.resume_quality_score,
            resume_quality_breakdown = EXCLUDED.resume_quality_breakdown,
            cover_letter_analysis = EXCLUDED.cover_letter_analysis,
            ai_writing_score = EXCLUDED.ai_writing_score,
            skill_match_breakdown = EXCLUDED.skill_match_breakdown,
            score_breakdown = EXCLUDED.score_breakdown,
            application_date = EXCLUDED.application_date;
    """, args)

    conn.commit()
    cur.close()
    conn.close()




#PROCESS RESUME

def get_job_description_from_db(job_title):
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("SELECT job_description FROM jobs WHERE job_title = %s LIMIT 1;", (job_title,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result[0] if result else "No job description available."

def process_resume_file(file_path: str, job_title="Unknown Role", cover_letter="", client_id="", resume_source="form", resume_url=""):
    parsed_resume = read_resume(file_path)
    job = get_job_record_from_db(job_title, client_id)  # Fetch full job record as dict

    gpt_result = evaluate_resume(parsed_resume.inference.prediction.fields, job, cover_letter, pdf_path=file_path, client_id=client_id)

    # Save to DB (no Copyleaks metrics)
    save_to_postgresql(parsed_resume.inference.prediction.fields, gpt_result, job_title, resume_url, client_id, resume_source)
    return gpt_result

def get_job_record_from_db(job_title: str, client_id: str) -> Dict[str, Any]:
    db_url = os.getenv("DATABASE_URL")
    up.uses_netloc.append("postgres")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE job_title = %s AND client_id = %s LIMIT 1;", (job_title, client_id))
    row = cur.fetchone()
    colnames = [desc[0] for desc in cur.description]
    job_record = dict(zip(colnames, row)) if row else {}
    cur.close()
    conn.close()
    return job_record



if __name__ == "__main__":
    sample_path = r"/path/to/sample_resume.pdf"
    process_resume_file(sample_path)
