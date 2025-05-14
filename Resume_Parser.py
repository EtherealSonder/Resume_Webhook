# -*- coding: utf-8 -*-


from dotenv import load_dotenv
load_dotenv()

import mindee
import openai
import gspread
import os
import psycopg2
import urllib.parse as up
import json 


from itertools import dropwhile
from oauth2client.service_account import ServiceAccountCredentials
from openai import OpenAI
from mindee import Client, AsyncPredictResponse, product
from typing import Dict, Any, List
from datetime import datetime




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
   

def show_result(read_resume):
  for field_name, field_value in read_resume.inference.prediction.fields.items():
        print(f"{field_name}: {field_value}")


def format_list(items: List[Any]) -> str:
    safe_items = []
    for item in items:
        try:
            if item and hasattr(item, "value") and item.value is not None:
                safe_items.append(str(item.value))
        except Exception:
            continue
    return ', '.join(safe_items)



def get_value(x):
    return x.value if hasattr(x, "value") else x


def calculate_experience_years(experiences: List[Any]) -> float:
    def get_value(x):
        return x.value if hasattr(x, "value") else x

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



def evaluate_resume(resume_data: Dict[str, Any], job_description: str, cover_letter: str = "") -> Dict[str, Any]:
    technical_skills = resume_data.get("technical_skills")
    soft_skills = resume_data.get("soft_skills")

    skills_list = []
    if technical_skills:
        skills_list.extend(technical_skills.values)
    if soft_skills:
        skills_list.extend(soft_skills.values)

    certifications = resume_data.get("certifications")
    certifications_list = []
    if certifications:
        certifications_list.extend(certifications.values)

    experience_field = resume_data.get("professional_experience", None)
    experience_entries = experience_field.values if experience_field and hasattr(experience_field, "values") else []
    experience_years = calculate_experience_years(experience_entries)

    candidate_info = {
        "Full Name": resume_data.get("full_name", ""),
        "Email": resume_data.get("email", ""),
        "Phone": resume_data.get("phone_number", ""),
        "Skills": format_list(skills_list),
        "Experience Years": experience_years,
        "Certifications": format_list(certifications_list),
        "Education": resume_data.get("education", "")
    }

    formatted_resume = '\n'.join(
        f"{key}: {value}" for key, value in candidate_info.items() if value
    )

    prompt = (
                "You are an experienced technical recruiter. You will be given:\n"
                "- A job description\n"
                "- Resume data\n"
                "- An optional cover letter\n\n"
                "Return a JSON object in the **exact** format below:\n\n"
                "{\n"
                    "  \"score\": <integer from 0 to 100>,\n"
                    "  \"summary\": \"<2 4 sentence summary>\",\n"
                    "  \"strengths\": \"<bullet point list or short text>\",\n"
                    "  \"weaknesses\": \"<bullet point list or short text>\"\n"
                "}\n\n"
                 f"### Job Description:\n{job_description.strip()}\n\n"
                 f"### Candidate Resume:\n{formatted_resume}\n"
             )

    if cover_letter:
        prompt += f"\n### Cover Letter:\n{cover_letter.strip()}\n"

    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You return valid JSON output only. No extra explanations."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.4
    )

    # Try parsing JSON safely
    import json
    try:
        gpt_data = json.loads(response.choices[0].message.content.strip())
        gpt_data["experience_years"] = experience_years 
        return gpt_data
    except Exception as e:
        print("Failed to parse GPT output:", e)
        print("Raw output:", response.choices[0].message.content)
        return {
            "score": 0,
            "summary": "Could not parse GPT output.",
            "strengths": "",
            "weaknesses": ""
        }

    
def save_to_postgresql(parsed_data, gpt_result, job_title, resume_url,client_id):
    
    # Read connection string from environment variable
    db_url = os.getenv("DATABASE_URL")

    # Parse connection string (required by psycopg2 in some cases)
    up.uses_netloc.append("postgres")

    # Connect using the full URL
    conn = psycopg2.connect(db_url)

    cur = conn.cursor()

    def safe_val(x):
        return x.value if hasattr(x, 'value') else x or ""

    name = safe_val(parsed_data.get("full_name"))
    email = safe_val(parsed_data.get("email"))
    phone = safe_val(parsed_data.get("phone_number"))

    score = gpt_result.get("score", 0)
    summary = gpt_result.get("summary", "")
    experience_years = gpt_result.get("experience_years", 0.0)
    experience_years = float(experience_years) if experience_years else 0.0
    strength = gpt_result.get("strengths", "")
    
    weakness = gpt_result.get("weaknesses", "")


    # Get job_id from title
    cur.execute("SELECT id FROM jobs WHERE job_title = %s AND client_id = %s LIMIT 1;", (job_title, client_id))
    job_row = cur.fetchone()
    if not job_row:
        cur.execute("INSERT INTO jobs (job_title, job_description, client_id) VALUES (%s, %s, %s) RETURNING id;",
                    (job_title, "Placeholder description", client_id))
        job_id = cur.fetchone()[0]
    else:
        job_id = job_row[0]

# Ensure all values are safe strings
    name = str(name or "")
    email = str(email or "")
    phone = str(phone or "")
    resume_url = str(resume_url or "")
    score = float(score) if score not in (None, "") else 0.0
    summary = str(summary or "")
    strength = str(strength or "")
    weakness = str(weakness or "")

    cur.execute("""
    INSERT INTO resumes (job_id, candidate_name, email, phone, resume_url, score, summary, strengths, weaknesses, experience_years)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (email, job_id) DO UPDATE 
    SET phone = EXCLUDED.phone,
        score = EXCLUDED.score,
        summary = EXCLUDED.summary,
        strengths = EXCLUDED.strengths,
        weaknesses = EXCLUDED.weaknesses,
        experience_years = EXCLUDED.experience_years;
""", (job_id, name, email, phone, resume_url, score, summary, strength, weakness, experience_years))

    print(f"Saving to PostgreSQL: {name}, {email}, Job: {job_title}")

    conn.commit()
    cur.close()
    conn.close()


def main():
    resume_path = r"D:\AI Resume Screener\Resume - King- C++ Developer.pdf"
    process_resume_file(resume_path)
    


def process_resume_file(file_path: str, job_title="Unknown Role", cover_letter="",client_id=""):
    parsed_resume = read_resume(file_path)
    job_description = get_job_description_from_db(job_title)
    gpt_result = evaluate_resume(parsed_resume.inference.prediction.fields, job_description, cover_letter)
    experience_years = gpt_result.get("experience_years", 0.0)

    score = gpt_result.get("score", "")
    summary = gpt_result.get("summary", "")
    strengths = gpt_result.get("strengths", "")
    weaknesses = gpt_result.get("weaknesses", "")

    print("GPT Raw Result:\n", gpt_result)
    print("Extracted Score:", score)
    print("Extracted Strengths:", strengths)

    save_to_postgresql(parsed_resume.inference.prediction.fields, gpt_result, job_title, "",client_id)
    
    return {
        "score": score,
        "summary": summary,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "experience_years": experience_years
    }

def get_job_description_from_db(job_title):
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("SELECT job_description FROM jobs WHERE job_title = %s LIMIT 1;", (job_title,))
    result = cur.fetchone()
    cur.close()
    conn.close()

    if result:
        return result[0]
    else:
        return "No job description available."
    
if __name__ == "__main__":
    main()



