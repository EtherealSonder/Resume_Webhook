# -*- coding: utf-8 -*-


import mindee
import openai
import gspread
import os
import psycopg2

from itertools import dropwhile
from oauth2client.service_account import ServiceAccountCredentials
from openai import OpenAI
from mindee import Client, AsyncPredictResponse, product
from typing import Dict, Any, List
from datetime import datetime



job_descriptions = {
    "Frontend Developer": "We are hiring a Frontend Developer skilled in HTML, CSS, JavaScript, and frameworks like React or Vue. The ideal candidate should have experience with responsive design, RESTful APIs, and basic testing tools.",
    "Full Stack Developer": "Seeking a Full Stack Developer proficient in both frontend (React, Angular) and backend (Node.js, Django, or Express) technologies. Must be comfortable with databases like MongoDB or PostgreSQL, version control, and CI/CD pipelines.",
    "Machine Learning Engineer": "Looking for a Machine Learning Engineer with solid Python skills, experience with ML frameworks like TensorFlow or PyTorch, and knowledge of model evaluation and deployment techniques. Data preprocessing and statistical understanding are key.",
    "DevOps Engineer": "We need a DevOps Engineer with experience in CI/CD, Docker, Kubernetes, and cloud platforms like AWS or Azure. Knowledge of scripting (Bash, Python), infrastructure as code, and system monitoring is essential.",
    "AI Research Intern": "We are looking for an AI Research Intern with familiarity in Python, deep learning frameworks, and a strong academic foundation in machine learning or AI. Should be able to assist in experiments, research papers, and prototyping.",
    "Game Designer": "Hiring a Game Designer to conceptualize mechanics, levels, and player progression. Should be familiar with Unity or Unreal, basic scripting, and player psychology. Creative problem-solving is essential.",
    "Unity Technical Artist": "We are hiring a Unity Technical Artist skilled in Unity, shader development, optimization, and animation pipelines. Should be able to bridge the gap between art and code and work closely with artists and developers.",
    "Unreal Engine Developer": "Seeking an Unreal Engine Developer with C++ and Blueprints experience. Should have knowledge of real-time rendering, gameplay scripting, and performance optimization for PC and console.",
    "Cloud Engineer (AWS/GCP)": "We are hiring a Cloud Engineer experienced in AWS or GCP services including EC2, S3, Cloud Functions, and IAM. Should have infrastructure as code experience (Terraform, CloudFormation) and system security knowledge.",
    "Mobile App Developer": "Looking for a Mobile App Developer proficient in Android (Kotlin/Java) or iOS (Swift). Cross-platform experience with Flutter or React Native is a plus. Must understand UI/UX guidelines and mobile APIs.",
    "Computer Vision Engineer": "Seeking a Computer Vision Engineer skilled in Python, OpenCV, and deep learning libraries. Should have experience with object detection, segmentation, and real-time image processing.",
    "NLP Engineer": "Hiring an NLP Engineer with knowledge of NLTK, spaCy, transformers, and experience with text classification, sentiment analysis, and language modeling.",
    "QA Automation Engineer": "We are hiring a QA Automation Engineer proficient in Selenium, pytest, or Cypress. Should have experience designing test cases, writing scripts, and maintaining automation frameworks.",
    "Business Intelligence Analyst": "Looking for a BI Analyst experienced in SQL, data visualization tools like Power BI or Tableau, and business metrics. Should be able to prepare dashboards, reports, and work closely with stakeholders.",
    "Web Developer": "We are hiring a Web Developer skilled in HTML, CSS, JavaScript, and frameworks like Bootstrap or Tailwind. Should have experience with backend basics, hosting, and SEO-friendly development."
}


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



def evaluate_resume(resume_data: Dict[str, Any], job_description: str) -> str:
    
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
        f"{key}: {value}" for key, value in candidate_info.items() if value)

    prompt = (
    "You are an experienced technical recruiter tasked with screening candidates for a job opening.\n"
    "You will be given a job description and a candidate's resume data. Your job is to analyze the match between the two.\n\n"

    "### Instructions:\n"
    "1. Carefully analyze the candidate's skills, experience, certifications, and education.\n"
    "2. Compare them against the job requirements.\n"
    "3. Be critical and objective, but fair.\n\n"

    f"### Job Description:\n{job_description.strip()}\n\n"
    
    "### Candidate Resume:\n"
    f"{formatted_resume}\n\n"

    "### Response Format:\n"
    "Match Score: <score between 0 and 100>\n"
    "Summary: <a 2-4 sentence explanation of why the candidate is or isn't a good fit>\n"
    "Strengths: <list top 3 reasons they are a good match>\n"
    "Weaknesses: <list top 3 reasons they may not be a good match>\n")

    response = openai_client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.4)

    return response.choices[0].message.content.strip()


def extract_section(text: str, section_header: str) -> str:
    lines = text.splitlines()
    capture = False
    section_lines = []

    for line in lines:
        if line.lower().startswith(section_header.lower()):
            capture = True
            continue
        elif capture and (line.strip() == "" or any(line.lower().startswith(h) for h in ["match score", "summary", "strength", "weakness"])):
            break
        elif capture:
            section_lines.append(line.strip())

    return ' '.join(section_lines).strip()
    
def save_to_postgresql(parsed_data, gpt_result, job_title):
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        database="resumescreener",
        user="postgres",
        password="urunenam!@#"
    )
    cur = conn.cursor()

    def safe_val(x):
        return x.value if hasattr(x, 'value') else x or ""

    name = safe_val(parsed_data.get("full_name"))
    email = safe_val(parsed_data.get("email"))
    phone = safe_val(parsed_data.get("phone_number"))
    resume_url = ""  # Optional: populate from earlier in pipeline

    score, summary = "", ""
    for line in gpt_result.split("\n"):
        if line.lower().startswith("match score") and not score:
            score = float(line.split(":", 1)[-1].strip())
        elif line.lower().startswith("summary") and not summary:
            summary = line.split(":", 1)[-1].strip()

    strength = extract_section(gpt_result, "Strengths:")
    weakness = extract_section(gpt_result, "Weaknesses:")

    # Get job_id from title
    cur.execute("SELECT id FROM jobs WHERE job_title = %s LIMIT 1;", (job_title,))
    job_row = cur.fetchone()
    if not job_row:
        cur.execute("INSERT INTO jobs (job_title, job_description) VALUES (%s, %s) RETURNING id;",
                    (job_title, "Placeholder description"))
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

    # Insert or update resume row
    cur.execute("""
        INSERT INTO resumes (job_id, candidate_name, email, phone, resume_url, score, summary, strengths, weaknesses)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (email, job_id) DO UPDATE 
        SET phone = EXCLUDED.phone, score = EXCLUDED.score, summary = EXCLUDED.summary,
            strengths = EXCLUDED.strengths, weaknesses = EXCLUDED.weaknesses;
    """, (job_id, name, email, phone, resume_url, score, summary, strength, weakness))

    print(f"Saving to PostgreSQL: {name}, {email}, Job: {job_title}")

    conn.commit()
    cur.close()
    conn.close()


def main():
    resume_path = r"D:\AI Resume Screener\Resume - King- C++ Developer.pdf"
    process_resume_file(resume_path)
    

def job_description_for(title):
    return job_descriptions.get(title, "No job description found.")


def process_resume_file(file_path: str,job_title="Unknown Role"):
    parsed_resume = read_resume(file_path)
    show_result(parsed_resume)
    
    job_description = job_description_for(job_title)
    gpt_result = evaluate_resume(parsed_resume.inference.prediction.fields, job_description)
    
    print(f"\nProcessed: {file_path}\n")
    print(gpt_result)
    
    save_to_postgresql(parsed_resume.inference.prediction.fields, gpt_result, job_title)


    
if __name__ == "__main__":
    main()



