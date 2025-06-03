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

def check_experience_match(expected_range: str, expected_level: str, candidate_years: float) -> bool:
    """
    Determine if the candidate's experience years meet or exceed the expected range or level.
    If both expected_range and expected_level are 'No expectation on experience', return True.
    """

    # If no expectations are set, always return True
    if expected_range == "No expectation on experience" and expected_level == "No expectation on experience":
        return True

    # Helper to parse expected_range text to a numeric lower bound (e.g., '2+ years' -> 2)
    def parse_range_to_min_years(text: str) -> float:
        match = re.search(r"(\d+(\.\d+)?)", text)
        if match:
            return float(match.group(1))
        return 0.0

    # Convert expected_range to numeric min years
    expected_range_min_years = parse_range_to_min_years(expected_range or "")

    # Mapping from experience_level to numeric ranges
    level_to_range = {
        "Fresher": (0, 0),
        "Beginner": (0, 1),
        "Junior": (1, 2),
        "Mid-Level": (2, 4),
        "Experienced": (4, 7),
        "Advanced": (7, 10),
        "Expert": (10, 15),
        "Veteran": (15, 100)  # 100 as an arbitrary upper limit
    }

    # Check against experience level
    level_range = level_to_range.get(expected_level, (0, 100))  # default to 0–100 if unknown

    # Decision logic:
    # If expected_range is not default, use it
    if expected_range != "No expectation on experience" and expected_range.strip():
        return candidate_years >= expected_range_min_years

    # Else if expected_level is not default, check against level range
    if expected_level != "No expectation on experience" and expected_level.strip():
        return level_range[0] <= candidate_years <= level_range[1]

    # If both expectations are somehow empty, fallback to True
    return True




#EDUCATION LEVEL CALCULATION

def extract_education_level(education_input, resume_text="") -> str:
    """
    Extract education level from structured education input or fallback to scanning the entire resume text.
    Select the highest-priority degree level if multiple are found.
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

    # If structured data is empty, fallback to scanning full resume text
    if not education_str.strip() and resume_text:
        print("No structured education data found, scanning full resume text instead.")
        education_str = resume_text.lower()

    # Clean up text: remove punctuation for simpler matching
    education_str = re.sub(r"[^a-z\s]", "", education_str)

    # Priority order mapping
    priority_levels = [
        ("PhD", ["phd", "doctorate", "doctoral", "doctor of philosophy"]),
        ("Master's", ["master", "msc", "m sc", "m a", "mfa", "meng", "ms", "mtech"]),
        ("Bachelor's", ["bachelor", "bsc", "b sc", "ba", "bfa", "beng", "btech", "b e"]),
        ("Diploma", ["diploma", "associate", "pg diploma"]),
        ("High School", ["high school", "secondary", "intermediate", "12th", "10th", "senior school"])
    ]

    # Initialize found levels
    found_levels = set()

    # spaCy token-level matching
    doc = nlp(education_str)
    for token in doc:
        token_text = token.text.lower()
        for level, keywords in priority_levels:
            for keyword in keywords:
                if keyword in token_text:
                    print(f"Matched {level} in token: '{token.text}' (keyword: '{keyword}')")
                    found_levels.add(level)

    # Regex-based fallback scanning whole text
    for level, keywords in priority_levels:
        for keyword in keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", education_str):
                print(f"Regex matched {level} (keyword: '{keyword}')")
                found_levels.add(level)

    # Select the highest priority level found
    for level, _ in priority_levels:
        if level in found_levels:
            print(f"Selected highest priority level: {level}")
            return level

    print(" No match found. Returning 'Other'")
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
    Compute % skill match between resume and job expectations using advanced fuzzy matching and spaCy similarity.
    Return match % and breakdown text (as a string, not a file path).
    """

    # Lowercase for normalization
    resume_skills_lower = [skill.lower() for skill in resume_technical_skills]
    job_skills_lower = [skill.lower() for skill in job_expected_technical_skills]

    matched_skills = []
    missing_skills = []

    for job_skill in job_skills_lower:
        matched = False

        #  Fuzzy match (RapidFuzz partial_ratio)
        for resume_skill in resume_skills_lower:
            fuzzy_score = fuzz.partial_ratio(job_skill, resume_skill)
            if fuzzy_score > 80:  # threshold for “good enough” match
                matched_skills.append(job_skill)
                matched = True
                break

        #  If no fuzzy match, fallback to spaCy similarity (optional)
        if not matched:
            job_doc = nlp(job_skill)
            for resume_skill in resume_skills_lower:
                resume_doc = nlp(resume_skill)
                similarity = job_doc.similarity(resume_doc)
                if similarity > 0.8:  # threshold for semantic similarity
                    matched_skills.append(job_skill)
                    matched = True
                    break

        if not matched:
            missing_skills.append(job_skill)

    # Remove duplicates
    matched_skills = list(set(matched_skills))

    # Compute match %
    match_pct = (len(matched_skills) / len(job_skills_lower)) * 100 if job_skills_lower else 0.0

    # Create breakdown
    breakdown_text = f"""
Skill Match Breakdown for {candidate_name} (Job ID: {job_id})

Expected Technical Skills ({len(job_skills_lower)}):
{', '.join(job_expected_technical_skills)}

Candidate Technical Skills ({len(resume_skills_lower)}):
{', '.join(resume_technical_skills)}

Matched Skills ({len(matched_skills)}):
{', '.join(matched_skills)}

Missing Skills ({len(missing_skills)}):
{', '.join(missing_skills)}

Skill Match Percentage: {match_pct:.2f}%
""".strip()

    return match_pct, breakdown_text



#ANALYZE COVER LETTER

def analyze_cover_letter_authenticity(resume_text: str, cover_letter: str) -> dict:
    if not cover_letter.strip():
        return {
            "analysis": "No cover letter provided.",
            "relevance": 0,
            "originality": 0,
            "tone_consistency": 0,
            "clarity": 0,
            "engagement": 0,
            "ai_writing_score": 0,
            "recommendation": "Cover letter missing - request one from candidate."
        }

    # GPT-4 Prompt to get nuanced metrics
    prompt = f"""
You are a recruiter AI evaluating a cover letter for the following criteria (0-100 scale):

- relevance
- originality
- tone consistency
- clarity
- engagement

Return a JSON with these numeric fields and a short analysis.

### Resume:
{resume_text.strip()}

### Cover Letter:
{cover_letter.strip()}
"""

    try:
        import json
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Return valid JSON only, no extra comments."},
                {"role": "user", "content": prompt}
            ]
        )
        gpt_data = json.loads(response.choices[0].message.content.strip())

        # Calculate ai_writing_score (weighted formula based on 5 metrics)
        ai_writing_score = (
            gpt_data.get("relevance", 0) * 0.2 +
            gpt_data.get("originality", 0) * 0.2 +
            gpt_data.get("tone_consistency", 0) * 0.2 +
            gpt_data.get("clarity", 0) * 0.2 +
            gpt_data.get("engagement", 0) * 0.2
        )
        ai_writing_score = round(min(max(ai_writing_score, 0), 100), 2)

        return {
            "analysis": gpt_data.get("analysis", ""),
            "relevance": gpt_data.get("relevance", 0),
            "originality": gpt_data.get("originality", 0),
            "tone_consistency": gpt_data.get("tone_consistency", 0),
            "clarity": gpt_data.get("clarity", 0),
            "engagement": gpt_data.get("engagement", 0),
            "ai_writing_score": ai_writing_score,
            "recommendation": "Cover letter evaluated successfully."
        }
    except Exception as e:
        print("Error analyzing cover letter:", e)
        return {
            "analysis": "Analysis failed due to error.",
            "relevance": 0,
            "originality": 0,
            "tone_consistency": 0,
            "clarity": 0,
            "engagement": 0,
            "ai_writing_score": 0,
            "recommendation": "Unable to evaluate authenticity."
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
    lines = text.splitlines()
    header_lines = [line for line in lines if line.strip().isupper() or line.endswith(":")]
    score = min(len(header_lines) / 6, 1.0)
    explanation = f"Detected {len(header_lines)} header-like lines."
    return score, explanation


def check_word_count(text: str) -> Tuple[float, str]:
    word_count = len(text.split())
    if 300 <= word_count <= 600:
        return 1.0, f"Word count is {word_count}, ideal range."
    elif 150 < word_count < 300 or 600 < word_count <= 1000:
        return 0.7, f"Word count is {word_count}, acceptable but could improve."
    else:
        return 0.3, f"Word count is {word_count}, outside recommended range."


def check_bullet_points(text: str) -> Tuple[float, str]:
    # Count various bullet styles
    bullet_points = text.count("- ") + text.count("•") + text.count("–")
    if bullet_points >= 10:
        score = 1.0
    elif bullet_points >= 5:
        score = 0.7
    else:
        score = 0.3
    explanation = f"Found {bullet_points} bullet points."
    return score, explanation



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
    has_spacing = any(line.strip() == "" for line in lines)
    if has_spacing:
        return 1.0, "Good line spacing detected."
    return 0.5, "Minimal spacing detected."


def check_consistency(text: str) -> Tuple[float, str]:
    if re.search(r"present|current", text.lower()):
        return 1.0, "Consistent tense usage detected."
    return 0.7, "Could improve consistency of tense."



def check_readability(text: str) -> Tuple[float, str]:
    tool = language_tool_python.LanguageTool('en-US')
    matches = tool.check(text)
    error_count = len(matches)
    if error_count <= 5:
        return 1.0, f"Found {error_count} minor grammar/spelling errors."
    elif error_count <= 15:
        return 0.7, f"Found {error_count} grammar/spelling errors."
    else:
        return 0.4, f"Found {error_count} significant grammar/spelling issues."



def check_ats_format(file_format: str) -> Tuple[float, str]:
    if file_format.lower() in ["pdf", "docx"]:
        return 1.0, f"File format is {file_format}, ATS-friendly."
    return 0.5, f"File format {file_format} may not be ATS-friendly."


def compute_resume_quality_score(text: str, file_format: str = "pdf") -> Tuple[int, Dict]:
    weights = {
        "structure": 0.2,
        "section_headers": 0.05,
        "word_count": 0.1,
        "bullet_points": 0.1,
        "contact_info": 0.1,
        "formatting": 0.1,
        "consistency": 0.1,
        "readability": 0.15,
        "ats_compatibility": 0.1
    }

    # Evaluate all modules
    breakdown = {}
    modules = {
        "structure": check_structure,
        "section_headers": check_section_headers,
        "word_count": check_word_count,
        "bullet_points": check_bullet_points,
        "contact_info": check_contact_info,
        "formatting": check_formatting,
        "consistency": check_consistency,
        "readability": check_readability,
        "ats_compatibility": lambda text: check_ats_format(file_format)
    }

    for key, func in modules.items():
        if key == "ats_compatibility":
            score, explanation = func(file_format)
        else:
            score, explanation = func(text)
        breakdown[key] = {"score": score, "explanation": explanation}

    # Compute weighted final score
    final_score = sum(breakdown[k]["score"] * weights[k] for k in weights)
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



#EVALUATE RESUME

def evaluate_resume(resume_data: Dict[str, Any], job: Dict[str, Any], cover_letter: str = "", pdf_path: str = None) -> Dict[str, Any]:
    from collections import defaultdict

    technical_skills_field = resume_data.get("technical_skills")
    soft_skills_field = resume_data.get("soft_skills")
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

    # Resume full text for scanning
    resume_text = '\n'.join([f"{k}: {get_value(v)}" for k, v in resume_data.items()])

    # Extract technical & soft skills from entire text
    extracted_section_skills = extract_technical_skills(resume_text)
    extracted_soft_skills = extract_soft_skills(resume_text, cover_letter)
    inferred_from_body = detect_technical_skills_from_text(resume_text)

    # Combine with fallback
    final_technical = sorted(set(extracted_section_skills + inferred_from_body + raw_technical_skills))
    final_soft = sorted(set(extracted_soft_skills))

    # Process other fields
    certifications_list = certifications.values if certifications else []
    experience_entries = experience_field.values if experience_field and hasattr(experience_field, "values") else []
    experience_years = calculate_experience_years(experience_entries)
    expected_experience_range = job.get("expected_experience_range", "No expectation on experience")
    expected_experience_level = job.get("experience_level", "No expectation on experience")

    # Call the new function to get the boolean
    experience_level_match = check_experience_match(expected_experience_range, expected_experience_level, experience_years)
    print("Experience level match (bool):", experience_level_match)

    education_level = extract_education_level(get_value(education_raw), resume_text)
    expected_education_level = job.get("expected_education_level", "No expectation on education level")

    # Use the new function
    education_level_match = check_education_level_match(expected_education_level, education_level)
    print("Education level match (bool):", education_level_match)
 
    

    links = extract_links_from_resume(resume_text, job.get("resume_pdf_path", None))
    
    print("Final links dictionary:", links)
    
    quality_score, quality_breakdown = compute_resume_quality_score(resume_text, file_format="pdf")


    cover_letter_analysis_dict = analyze_cover_letter_authenticity(resume_text, cover_letter)
    ai_score = cover_letter_analysis_dict.get("ai_writing_score", 0)

    # Compute technical skill match and breakdown text
    expected_technical_skills = job.get("expected_technical_skills", [])
    candidate_name = get_value(resume_data.get("full_name", "unknown"))
    job_id = job.get("id")
    skill_match_pct, breakdown_text = compute_skill_match(
        final_technical,
        expected_technical_skills,
        candidate_name,
        job_id
    )

    # GPT evaluation prompt
    prompt = f"""
You are an advanced technical recruiter AI.

Your task is to evaluate a resume and cover letter against a job description, and return a JSON with:
- A numerical score from 0 to 100 using the rubric below
- A short summary explaining *why* the candidate is a good fit
- Key strengths (if any)
- Weaknesses (if any)

SCORING RUBRIC:
- Resume Quality: {quality_score}/100 (20%)
- Years of Experience: {experience_years} (20%)
- Skill Match: {skill_match_pct}% (25%)
- Education Level: {education_level} (15%)
- Soft Skills: (10%)
- Certifications: (10%)

### Job Description:
{job['job_description'].strip()}

### Resume:
{resume_text.strip()}
"""
    if cover_letter:
        prompt += f"\n### Cover Letter:\n{cover_letter.strip()}"

    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Return valid JSON only. No prose or comments."},
            {"role": "user", "content": prompt}
        ]
    )

    raw_output = response.choices[0].message.content.strip()
    print("Raw GPT output:", repr(raw_output))  # Debug!

    # Remove Markdown fences if present
    if raw_output.startswith("```"):
        raw_output = raw_output.strip("`")
        if raw_output.startswith("json"):
            raw_output = raw_output[4:].strip()

    try:
        gpt_data = json.loads(raw_output)

        # Fallbacks for missing strengths and weaknesses
        gpt_data["strengths"] = gpt_data.get("strengths") or "Not provided"
        gpt_data["weaknesses"] = gpt_data.get("weaknesses") or "Not provided"

        for key in [
            "score", "summary", "strengths", "weaknesses",
            "experience_years", "education_level", "skills_matched_pct", "resume_quality_score",
            "cover_letter_report", "portfolio_url", "github_url", "linkedin_url",
            "technical_skills", "soft_skills", "certifications"
        ]:
            gpt_data.setdefault(key, "" if isinstance(gpt_data.get(key), str) else 0)

        gpt_data.update({
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
            "skill_match_breakdown": breakdown_text,  # now storing text directly
            **links
        })

        return gpt_data

    except Exception as e:
        print("Failed to parse GPT output:", e)
        print("Raw output after cleanup:", raw_output)
        return {
            "score": 0,
            "summary": "Evaluation error.",
            "strengths": "Not provided",
            "weaknesses": "Not provided",
            "experience_years": 0,
            "education_level": "",
            "skills_matched_pct": 0,
            "certifications": "",
            "technical_skills": [],
            "soft_skills": [],
            "resume_quality_score": 0,
            "resume_quality_breakdown": quality_breakdown,
            "portfolio_url": "",
            "github_url": "",
            "linkedin_url": "",
            "skill_match_breakdown": "" 
        }



def save_to_postgresql(parsed_data, gpt_result, job_title, resume_url, client_id, resume_source="form"):
    db_url = os.getenv("DATABASE_URL")
    up.uses_netloc.append("postgres")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    def safe_val(x): return x.value if hasattr(x, 'value') else x or ""

    name = safe_val(parsed_data.get("full_name"))
    email = safe_val(parsed_data.get("email"))
    phone = safe_val(parsed_data.get("phone_number"))

    cur.execute("SELECT id FROM jobs WHERE job_title = %s AND client_id = %s LIMIT 1;", (job_title, client_id))
    row = cur.fetchone()
    job_id = row[0] if row else None
    if not job_id:
        cur.execute("INSERT INTO jobs (job_title, job_description, client_id) VALUES (%s, %s, %s) RETURNING id;",
                     (job_title, "Placeholder description", client_id))
        job_id = cur.fetchone()[0]

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

    strengths = (
        json.dumps(gpt_result.get("strengths", "Not provided"))
        if isinstance(gpt_result.get("strengths", "Not provided"), (dict, list))
        else gpt_result.get("strengths", "Not provided")
    )
    weaknesses = (
        json.dumps(gpt_result.get("weaknesses", "Not provided"))
        if isinstance(gpt_result.get("weaknesses", "Not provided"), (dict, list))
        else gpt_result.get("weaknesses", "Not provided")
    )

    args = (
        job_id, name, email, phone, resume_url,
        gpt_result.get("score", 0), gpt_result.get("summary", ""), strengths, weaknesses,
        gpt_result.get("experience_years", 0), gpt_result.get("education_level", ""), gpt_result.get("skills_matched_pct", 0),
        gpt_result.get("certifications", ""), resume_source, gpt_result.get("portfolio_url", ""), gpt_result.get("github_url", ""),
        gpt_result.get("linkedin_url", ""), technical_skills_list, soft_skills_list,
        gpt_result.get("resume_quality_score", 0), json.dumps(gpt_result.get("resume_quality_breakdown", {})), 
        json.dumps(gpt_result.get("cover_letter_analysis", {})),
        gpt_result.get("ai_writing_score", 0), gpt_result.get("skill_match_breakdown", ""),
        datetime.utcnow()
    )

    cur.execute("""
        INSERT INTO resumes (
            job_id, candidate_name, email, phone, resume_url, score, summary, strengths, weaknesses,
            experience_years, education_level, skills_matched_pct, certifications, resume_source,
            portfolio_url, github_url, linkedin_url, technical_skills, soft_skills,
            resume_quality_score, resume_quality_breakdown, cover_letter_analysis,
            ai_writing_score, skill_match_breakdown, application_date
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
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
            application_date = EXCLUDED.application_date;
    """, args)

    conn.commit()
    cur.close()
    conn.close()






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

    gpt_result = evaluate_resume(parsed_resume.inference.prediction.fields, job, cover_letter, pdf_path=file_path)

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
