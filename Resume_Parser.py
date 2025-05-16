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


mindee_api_key = os.getenv("MINDEE_API_KEY")
mindee_client = mindee.Client(api_key=mindee_api_key)
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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


def extract_education_level(education_input) -> str:
    # Flatten and lowercase input
    if hasattr(education_input, "values"):
        values = [v.value.lower() for v in education_input.values if hasattr(v, "value") and v.value]
        education_str = " ".join(values)
    elif isinstance(education_input, str):
        education_str = education_input.lower()
    else:
        education_str = str(education_input).lower()

    # Normalize and search
    if re.search(r"ph\.?d|doctorate|doctoral", education_str):
        return "PhD"
    elif re.search(r"master|msc|m\.?a|mfa", education_str):
        return "Master's"
    elif re.search(r"bachelor|b\.?a|b\.?sc|bfa", education_str):
        return "Bachelor's"
    elif re.search(r"diploma|associate", education_str):
        return "Diploma"
    elif re.search(r"high school|secondary|intermediate", education_str):
        return "High School"
    else:
        return "Other"

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


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", " ", text.lower()).strip()

def compute_skill_match(resume_skills: List[str], job_description: str) -> float:
    if not resume_skills or not job_description:
        return 0.0

    job_desc_text = normalize(job_description)
    matched_skills = 0

    for skill in resume_skills:
        if not skill:
            continue
        skill_norm = normalize(skill)
        # Partial match
        if skill_norm in job_desc_text:
            matched_skills += 1
        else:
            # Try fuzzy multi-word match
            tokens = skill_norm.split()
            if any(token in job_desc_text for token in tokens if len(token) > 2):
                matched_skills += 0.5  # partial credit

    score = (matched_skills / len(resume_skills)) * 100
    return round(score, 2)

def analyze_cover_letter_authenticity(resume_text: str, cover_letter: str) -> str:
    if not cover_letter.strip():
        return "No cover letter provided."

    prompt = f"""
    You are a recruiter AI that detects inconsistencies or fake claims in cover letters.

    Given:
    - Resume (used as source of truth)
    - Cover Letter (provided by candidate)

    Tasks:
    1. Identify if the cover letter is consistent with the resume.
    2. Highlight any fabricated, exaggerated, or unverifiable claims.
    3. Evaluate how likely it is that the cover letter was written by an AI (in %).
    4. Return a professional report in plain text.

    ### Resume:
    {resume_text.strip()}

    ### Cover Letter:
    {cover_letter.strip()}
    """

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Return only your analysis, do not wrap in JSON or bullet list."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print("Error analyzing cover letter:", e)
        return "Analysis failed due to an error."


def extract_links_from_text(text: str) -> Dict[str, str]:
    links = {"portfolio_url": "", "github_url": "", "linkedin_url": ""}

    # General URL regex
    url_pattern = re.compile(
        r'(https?://)?(www\.)?[\w.-]+\.(com|net|org|io|design|art|dev)(/[^\s]*)?',
        re.IGNORECASE
    )

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        match = re.search(url_pattern, line)
        if match:
            url = match.group(0)
            if "github.com" in url and not links["github_url"]:
                links["github_url"] = "https://" + url if not url.startswith("http") else url
            elif "linkedin.com" in url and not links["linkedin_url"]:
                links["linkedin_url"] = "https://" + url if not url.startswith("http") else url
            elif any(domain in url for domain in [
                "artstation", "behance", "dribbble", "myportfolio", "deviantart", ".design"
            ]) and not links["portfolio_url"]:
                links["portfolio_url"] = "https://" + url if not url.startswith("http") else url
            elif not links["portfolio_url"] and url.endswith((".com", ".design")):
                links["portfolio_url"] = "https://" + url if not url.startswith("http") else url

    return links


def compute_resume_quality_score(text: str) -> int:
    score = 0
    text_lower = text.lower()

    # 1. Word Count (ideal range: 300–1500)
    word_count = len(text.split())
    if 300 <= word_count <= 1500:
        score += 20
    elif 150 < word_count < 300:
        score += 10  # Too short
    elif word_count > 1500:
        score += 5   # Too verbose

    # 2. Section Coverage: Experience, Skills, Education, Projects
    required_sections = ["experience", "education", "skills"]
    section_hits = sum(1 for sec in required_sections if sec in text_lower)
    score += section_hits * 7  # 3 sections x 7 = 21 max

    # 3. Bullet Points Usage
    bullet_count = text.count(".") + text.count("- ")
    if bullet_count >= 8:
        score += 15
    elif bullet_count >= 4:
        score += 8

    # 4. Contact Info Presence
    if re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}", text):
        score += 5
    if re.search(r"(linkedin\\.com|github\\.com|artstation\\.com)", text_lower):
        score += 5
    if re.search(r"\\+?\\d{7,}", text):  # phone number
        score += 5

    # 5. Link Presence (GitHub, Portfolio, LinkedIn)
    if any(link in text_lower for link in ["github", "linkedin", "portfolio", "artstation", "behance"]):
        score += 10

    # 6. Visual Formatting Heuristics
    if len(set(text)) > 30 and "." in text and bullet_count > 2:
        score += 15
    if len(text.split("\n")) >= 20:
        score += 5

    return min(score, 100)

def format_list(items: List[Any]) -> str:
    safe_items = []
    for item in items:
        try:
            if item and hasattr(item, "value") and item.value is not None:
                safe_items.append(str(item.value))
        except Exception:
            continue
    return ', '.join(safe_items)

def evaluate_resume(resume_data: Dict[str, Any], job_description: str, cover_letter: str = "") -> Dict[str, Any]:
    technical_skills = resume_data.get("technical_skills")
    soft_skills = resume_data.get("soft_skills")
    certifications = resume_data.get("certifications")
    education_raw = resume_data.get("education", "")
    experience_field = resume_data.get("professional_experience", None)

    skills_list = []
    if technical_skills:
        skills_list.extend([get_value(skill) for skill in technical_skills.values])
    if soft_skills:
        skills_list.extend([get_value(skill) for skill in soft_skills.values])

    certifications_list = certifications.values if certifications else []
    experience_entries = experience_field.values if experience_field and hasattr(experience_field, "values") else []

    experience_years = calculate_experience_years(experience_entries)
    education_level = extract_education_level(get_value(education_raw))
    skill_match_pct = compute_skill_match(skills_list, job_description)
    resume_text = '\n'.join([f"{k}: {get_value(v)}" for k, v in resume_data.items()])
    quality_score = compute_resume_quality_score(resume_text)
    soft_skills_inferred = extract_soft_skills(resume_text, cover_letter)
    links = extract_links_from_text(resume_text)
    cover_letter_report = analyze_cover_letter_authenticity(resume_text, cover_letter)

    # GPT prompt
    prompt = f"""
You are an advanced technical recruiter AI.

Your task is to evaluate a resume and cover letter against a job description, and return a JSON with:
- A numerical score from 0 to 100 using the rubric below
- A short summary explaining *why* the candidate is a good fit
- Key strengths (if any)
- Weaknesses (if any)
If no strengths or weaknesses are obvious, leave the field empty.

SCORING RUBRIC:
- Resume Quality (formatting, clarity, layout): {quality_score}/100 (weight: 20%)
- Years of Relevant Experience: {experience_years} (weight: 20%)
- Skill Match: {skill_match_pct}% (weight: 25%)
- Education Level: {education_level} (weight: 15%)
- Soft Skills/Communication (based on resume & cover letter): (weight: 10%)
- Certifications or Industry Tools: (weight: 10%)

Calculate the final score as a weighted average of the above.

### Job Description:
{job_description.strip()}

### Resume:
{resume_text.strip()}
"""

    if cover_letter:
        prompt += f"\n### Cover Letter:\n{cover_letter.strip()}"

    response = openai_client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are an expert resume evaluator. Return a valid JSON with keys: score, summary, strengths, weaknesses."},
            {"role": "user", "content": prompt}
        ]
    )

    try:
        try:
            gpt_data = json.loads(response.choices[0].message.content.strip())
        except json.JSONDecodeError as e:
            print("GPT response is not valid JSON:\n", response.choices[0].message.content)
            raise e

        # Ensure all required keys exist
        for key in [
            "experience_years", "education_level", "skills_matched_pct", "resume_quality_score",
            "score", "summary", "strengths", "weaknesses", "cover_letter_report",
            "portfolio_url", "github_url", "linkedin_url", "technical_skills", "soft_skills", "certifications"
        ]:
            gpt_data.setdefault(key, "" if isinstance(gpt_data.get(key), str) else 0)

        gpt_data.update({
            "experience_years": experience_years,
            "education_level": education_level,
            "skills_matched_pct": skill_match_pct,
            "certifications": format_list(certifications_list),
            "cover_letter_report": cover_letter_report,
            "technical_skills": skills_list,
            "soft_skills": soft_skills_inferred,
            "resume_quality_score": quality_score,
            **links
        })

        return gpt_data

    except Exception as e:
        print("Failed to parse GPT output:", e)
        print("Raw output:", response.choices[0].message.content)
        return {
            "score": 0,
            "summary": "Error in evaluation.",
            "strengths": "",
            "weaknesses": "",
            "cover_letter_report": "N/A",
            "experience_years": 0,
            "education_level": "",
            "skills_matched_pct": 0,
            "certifications": "",
            "technical_skills": [],
            "soft_skills": [],
            "resume_quality_score": 0,
            "portfolio_url": "",
            "github_url": "",
            "linkedin_url": ""
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
    job_row = cur.fetchone()
    if not job_row:
        cur.execute("INSERT INTO jobs (job_title, job_description, client_id) VALUES (%s, %s, %s) RETURNING id;",
                    (job_title, "Placeholder description", client_id))
        job_id = cur.fetchone()[0]
    else:
        job_id = job_row[0]

    cur.execute("""
    INSERT INTO resumes (job_id, candidate_name, email, phone, resume_url, score, summary, strengths, weaknesses,
        experience_years, education_level, skills_matched_pct, certifications, resume_source,
        portfolio_url, github_url, linkedin_url, technical_skills, soft_skills, resume_quality_score, cover_letter_report)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        cover_letter_report = EXCLUDED.cover_letter_report;
    """, (
        job_id, name, email, phone, resume_url, gpt_result["score"], gpt_result["summary"],
        gpt_result["strengths"], gpt_result["weaknesses"], gpt_result["experience_years"],
        gpt_result["education_level"], gpt_result["skills_matched_pct"], gpt_result["certifications"],
        resume_source, gpt_result["portfolio_url"], gpt_result["github_url"],
        gpt_result["linkedin_url"], gpt_result["technical_skills"], gpt_result["soft_skills"],
        gpt_result["resume_quality_score"], gpt_result["cover_letter_report"]
    ))

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
    job_description = get_job_description_from_db(job_title)
    gpt_result = evaluate_resume(parsed_resume.inference.prediction.fields, job_description, cover_letter)
    save_to_postgresql(parsed_resume.inference.prediction.fields, gpt_result, job_title, resume_url, client_id, resume_source)
    return gpt_result

if __name__ == "__main__":
    sample_path = r"/path/to/sample_resume.pdf"
    process_resume_file(sample_path)
