# -*- coding: utf-8 -*-
import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Initialize OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def parse_job_description_with_gpt(job_description: str) -> dict:
    """
    Given a job description text, uses GPT to extract expected fields for the job posting.
    If GPT finds no relevant data for a field, it will set a default value explicitly.
    """

    # Truncate long job descriptions to avoid exceeding GPT context limits
    truncated_description = job_description.strip()[:2000]

    prompt = f"""
You are an expert recruiter AI. Given the following job description, extract these fields in a JSON format:

- expected_technical_skills: list of relevant technical skills for the job (or an empty list if not mentioned).
- expected_soft_skills: list of soft skills (or an empty list if not mentioned).
- expected_education_level: string (e.g., "Bachelor's", "Master's", "PhD", "No expectation on education level").
- expected_experience_range: string (e.g., "0-2 years", "3-5 years", "No expectation on experience").
- expected_certifications: list of relevant certifications (or an empty list if not mentioned).
- expected_responsibilities: string paragraph (or "No specific responsibilities provided" if not mentioned).
- expected_portfolio_required: true/false (whether a portfolio or project link is explicitly required in the job description).
- expected_languages: list of languages (or an empty list if not mentioned).
- expected_tools: list of tools/platforms (or an empty list if not mentioned).
- expected_work_environment: string (e.g., "Remote", "Hybrid", "On-site", or "No specific work environment provided").
- expected_availability: string (e.g., "Full-time", "Part-time", "No specific availability provided").
- expected_salary_range: string (e.g., "40,000 - 50,000", or "No salary expectation provided").

**Important instructions:**
- If any field is not mentioned in the job description, set it to the default value specified above (like "No expectation on education level" or empty list for lists).
- Return only a valid JSON object, with no extra commentary or explanations.

### Job Description:
{truncated_description}
"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Return only valid JSON. No extra comments or explanations."},
                {"role": "user", "content": prompt}
            ]
        )
        content = response.choices[0].message.content.strip()
        print("GPT raw output:", repr(content))  # Debugging output!

        if not content:
            print("GPT returned empty content.")
            raise ValueError("GPT response is empty.")

        # Remove Markdown code block fencing if present
        if content.startswith("```"):
            # Remove triple backticks
            content = content.strip("`")
            # Remove 'json' marker if present
            if content.startswith("json"):
                content = content[4:].strip()
        print("Cleaned GPT output:", repr(content))  # Debugging!

        try:
            parsed_data = json.loads(content)
            return parsed_data
        except json.JSONDecodeError as e:
            print("JSONDecodeError:", e)
            print("Cleaned GPT output was:", content)
            raise ValueError("GPT response is not valid JSON.")

    except Exception as e:
        print("Error parsing job description with GPT:", e)
        # Fallback default values if GPT parsing fails
        return {
            "expected_technical_skills": [],
            "expected_soft_skills": [],
            "expected_education_level": "No expectation on education level",
            "expected_experience_range": "No expectation on experience",
            "expected_certifications": [],
            "expected_responsibilities": "No specific responsibilities provided",
            "expected_portfolio_required": False,
            "expected_languages": [],
            "expected_tools": [],
            "expected_work_environment": "No specific work environment provided",
            "expected_availability": "No specific availability provided",
            "expected_salary_range": "No salary expectation provided"
        }

if __name__ == "__main__":
    sample_job_description = """
We're seeking a Frontend Developer with expertise in React and TypeScript. You'll collaborate with our designers and backend engineers to build beautiful user interfaces.

Responsibilities include:
- Building and maintaining scalable UI components
- Collaborating in an agile team environment
- Writing clean, well-tested code

Experience:
- 2-4 years in frontend development
- Bachelor's degree in Computer Science or related field
- Familiarity with Figma and design systems

Optional: Experience with GraphQL and Next.js is a plus.
"""
    parsed = parse_job_description_with_gpt(sample_job_description)
    print(json.dumps(parsed, indent=2))
