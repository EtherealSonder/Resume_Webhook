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

def calculate_experience_years(experiences: List[Any]) -> float:
    total_months = 0
    for exp in experiences:
        start_year = get_value(getattr(exp, "start_year", None))
        start_month = get_value(getattr(exp, "start_month", None))
        end_year = get_value(getattr(exp, "end_year", datetime.now().year))
        end_month = get_value(getattr(exp, "end_month", datetime.now().month))
        try:
            if start_year and start_month:
                start_date = datetime(year=int(start_year), month=int(start_month), day=1)
                end_date = datetime(year=int(end_year), month=int(end_month), day=1)
                delta_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
                if delta_months > 0:
                    total_months += delta_months
        except Exception:
            continue
    return round(total_months / 12, 1)

def extract_education_level(education_str: str) -> str:
    education_str = education_str.lower()
    if "phd" in education_str:
        return "PhD"
    elif "master" in education_str or "msc" in education_str:
        return "Master's"
    elif "bachelor" in education_str or "bsc" in education_str:
        return "Bachelor's"
    elif "diploma" in education_str:
        return "Diploma"
    else:
        return "Other"

def compute_skill_match(resume_skills: List[str], job_description: str) -> float:
    job_description = job_description.lower()
    match_count = sum(1 for skill in resume_skills if skill.lower() in job_description)
    return round((match_count / len(resume_skills)) * 100, 2) if resume_skills else 0.0

def check_cover_letter_authenticity(cover_letter: str) -> bool:
    if not cover_letter.strip():
        return False
    try:
        prompt = f"""
        You are an AI assistant. Decide whether the following cover letter is likely AI-generated or generic.
        Return true if it is suspiciously generic, otherwise false.

        Cover Letter:
        {cover_letter.strip()}
        """
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        return "true" in response.choices[0].message.content.lower()
    except:
        return False

def extract_links_from_text(text: str) -> Dict[str, str]:
    links = {"portfolio_url": "", "github_url": "", "linkedin_url": ""}
    for line in text.splitlines():
        if "github.com" in line:
            links["github_url"] = line.strip()
        elif "linkedin.com" in line:
            links["linkedin_url"] = line.strip()
        elif "http" in line and not any(k in line for k in ["github.com", "linkedin.com"]):
            links["portfolio_url"] = line.strip()
    return links

def compute_resume_quality_score(text: str) -> int:
    length = len(text.split())
    score = 0
    if 300 < length < 1500:
        score += 50
    if any(word in text.lower() for word in ["experience", "education", "skills"]):
        score += 25
    if "objective" not in text.lower():
        score += 25
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
        skills_list.extend(technical_skills.values)
    if soft_skills:
        skills_list.extend(soft_skills.values)

    certifications_list = certifications.values if certifications else []
    experience_entries = experience_field.values if experience_field and hasattr(experience_field, "values") else []

    experience_years = calculate_experience_years(experience_entries)
    education_level = extract_education_level(get_value(education_raw))
    skill_match_pct = compute_skill_match(skills_list, job_description)
    cover_letter_flag = check_cover_letter_authenticity(cover_letter)

    formatted_resume = '\n'.join([
        f"{k}: {get_value(v)}" for k, v in resume_data.items()
    ])

    links = extract_links_from_text(formatted_resume)
    quality_score = compute_resume_quality_score(formatted_resume)

    prompt = f"""
    You are an experienced technical recruiter. You will be given:
    - A job description
    - Resume data
    - An optional cover letter

    Return a JSON object:
    {{
      "score": <0-100>,
      "summary": "<short summary>",
      "strengths": "<points>",
      "weaknesses": "<points>"
    }}

    ### Job Description:
    {job_description.strip()}

    ### Resume:
    {formatted_resume}
    """
    if cover_letter:
        prompt += f"\n### Cover Letter:\n{cover_letter}"

    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt}
        ]
    )

    try:
        gpt_data = json.loads(response.choices[0].message.content.strip())
        gpt_data.update({
            "experience_years": experience_years,
            "education_level": education_level,
            "skills_matched_pct": skill_match_pct,
            "certifications": format_list(certifications_list),
            "cover_letter_flag": cover_letter_flag,
            "technical_skills": skills_list,
            "soft_skills": soft_skills.values if soft_skills else [],
            "resume_quality_score": quality_score,
            **links
        })
        return gpt_data
    except Exception as e:
        print("Failed to parse GPT output:", e)
        print("Raw output:", response.choices[0].message.content)
        return {"score": 0, "summary": "Error.", "strengths": "", "weaknesses": ""}

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
        experience_years, education_level, skills_matched_pct, certifications, cover_letter_flag, resume_source,
        portfolio_url, github_url, linkedin_url, technical_skills, soft_skills, resume_quality_score)
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
        cover_letter_flag = EXCLUDED.cover_letter_flag,
        resume_source = EXCLUDED.resume_source,
        portfolio_url = EXCLUDED.portfolio_url,
        github_url = EXCLUDED.github_url,
        linkedin_url = EXCLUDED.linkedin_url,
        technical_skills = EXCLUDED.technical_skills,
        soft_skills = EXCLUDED.soft_skills,
        resume_quality_score = EXCLUDED.resume_quality_score;
    """, (
        job_id, name, email, phone, resume_url, gpt_result["score"], gpt_result["summary"],
        gpt_result["strengths"], gpt_result["weaknesses"], gpt_result["experience_years"],
        gpt_result["education_level"], gpt_result["skills_matched_pct"], gpt_result["certifications"],
        gpt_result["cover_letter_flag"], resume_source, gpt_result["portfolio_url"],
        gpt_result["github_url"], gpt_result["linkedin_url"], gpt_result["technical_skills"],
        gpt_result["soft_skills"], gpt_result["resume_quality_score"]
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
