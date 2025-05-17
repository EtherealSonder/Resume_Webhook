# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from Resume_Parser import process_resume_file
from s3_utils import upload_to_s3 
import tempfile
import os
import logging
import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

load_dotenv()

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

form_template = """
<!DOCTYPE html>
<html>
<head><title>Apply for {{ job_title }}</title></head>
<body>
<h2>Apply for {{ job_title }}</h2>
<form action="/parse_resume" method="post" enctype="multipart/form-data">
    <input type="hidden" name="job_id" value="{{ job_id }}">
    <label>Upload Resume (PDF):</label><br>
    <input type="file" name="resume"><br><br>
    <label>Cover Letter (optional):</label><br>
    <textarea name="cover_letter" rows="5" cols="50"></textarea><br><br>
    <input type="submit" value="Submit">
</form>
</body>
</html>
"""

@app.route("/apply/<int:job_id>")
def show_application_form(job_id):
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("SELECT job_title FROM jobs WHERE id = %s", (job_id,))
    job = cur.fetchone()
    cur.close()
    conn.close()
    if not job:
        return "Job not found.", 404
    return render_template_string(form_template, job_id=job_id, job_title=job[0])

@app.route("/parse_resume", methods=["POST"])
def parse_resume():
    resume_file = request.files.get("resume")
    job_id = request.form.get("job_id")
    cover_letter = request.form.get("cover_letter", "")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
        resume_file.save(temp.name)
        file_path = temp.name

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("SELECT job_title, client_id FROM jobs WHERE id = %s", (job_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"error": "Invalid job ID"}), 400

    job_title, client_id = row

    try:
        s3_url = upload_to_s3(file_path, job_id, resume_file.filename)
    except Exception as e:
        return jsonify({"error": f"S3 upload failed: {str(e)}"}), 500

    result = process_resume_file(file_path, job_title, cover_letter, client_id, resume_source="form", resume_url=s3_url)
    os.unlink(file_path)
    return jsonify(result)

@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json()
    name = data.get("name")
    email = data.get("email")
    password = data.get("password")

    if not all([name, email, password]):
        return jsonify({"error": "All fields are required."}), 400

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("SELECT id FROM clients WHERE email = %s", (email,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({"error": "Email already exists."}), 400

    hashed_pw = generate_password_hash(password)
    cur.execute("INSERT INTO clients (name, email, password) VALUES (%s, %s, %s) RETURNING id;",
                (name, email, hashed_pw))
    client_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"id": client_id, "name": name, "email": email})

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("SELECT id, name, password FROM clients WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row and check_password_hash(row[2], password):
        return jsonify({"id": row[0], "name": row[1], "email": email})
    else:
        return jsonify({"error": "Invalid credentials"}), 401

@app.route("/candidates", methods=["GET"])
def get_candidates():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            SELECT r.candidate_name, r.email, r.phone, r.score, r.experience_years, j.job_title,
                   r.education_level, r.skills_matched_pct, r.certifications,
                   r.cover_letter_analysis, r.ai_writing_score, r.application_date,
                   r.technical_skills, r.soft_skills,
                   r.portfolio_url, r.github_url, r.linkedin_url,
                   r.summary, r.strengths, r.weaknesses,
                   r.resume_url
            FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s
            ORDER BY r.score DESC;
        """, (client_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        candidates = [
            {
                "name": row[0],
                "email": row[1],
                "phone": row[2],
                "score": row[3],
                "experience": row[4],
                "job_title": row[5],
                "education": row[6],
                "skill_match": row[7],
                "certifications": row[8],
                "cover_letter_analysis": row[9] if row[9] else {
    "analysis": "No cover letter provided.",
    "issues": [],
    "recommendation": "Cover letter missing - request one from candidate",
},
"ai_writing_score": row[10] if row[10] is not None else 0,
                "submitted_at": row[11],
                "technical_skills": row[12] or "",
                "soft_skills": row[13] or "",
                "portfolio_url": row[14],
                "github_url": row[15],
                "linkedin_url": row[16],
                "summary": row[17] or "",
                "strengths": row[18] or "",
                "weaknesses": row[19] or "",
                "resume_url": row[20] or ""
            }
            for row in rows
        ]
        return jsonify(candidates)

    except Exception as e:
        logging.exception("Error fetching candidates")
        return jsonify({"error": str(e)}), 500

@app.route("/dashboard", methods=["GET"])
def dashboard():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*), AVG(score), AVG(experience_years), AVG(skills_matched_pct)
            FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s;
        """, (client_id,))
        count, avg_score, avg_exp, avg_skill = cur.fetchone()
        cur.close()
        conn.close()

        return jsonify({
            "totalCandidates": count or 0,
            "averageScore": round(avg_score or 0, 2),
            "averageExperience": round(avg_exp or 0, 2),
            "averageSkillMatch": round(avg_skill or 0, 2)
        })

    except Exception as e:
        logging.exception("Error in /dashboard")
        return jsonify({"error": str(e)}), 500

@app.route("/statistics", methods=["GET"])
def statistics():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        # Job volume chart
        cur.execute("""
            SELECT j.job_title, COUNT(*) FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s
            GROUP BY j.job_title;
        """, (client_id,))
        job_dist = [{"title": row[0] or "Unknown", "count": row[1]} for row in cur.fetchall()]

        # Education breakdown
        cur.execute("""
            SELECT r.education_level, COUNT(*) FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s
            GROUP BY r.education_level;
        """, (client_id,))
        edu_pie = [{"level": row[0] or "Unknown", "count": row[1]} for row in cur.fetchall()]

        # Application timeline
        cur.execute("""
            SELECT DATE(r.application_date), COUNT(*) FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s
            GROUP BY DATE(r.application_date)
            ORDER BY DATE(r.application_date);
        """, (client_id,))
        timeline = [{"date": str(row[0]), "count": row[1]} for row in cur.fetchall()]

        # Score vs Experience (scatter)
        cur.execute("""
            SELECT r.experience_years, r.score, j.job_title FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s;
        """, (client_id,))
        score_exp = [
            {
                "experience": float(row[0]) if row[0] is not None else 0.0,
                "score": float(row[1]) if row[1] is not None else 0.0,
                "job_title": row[2]
            }
            for row in cur.fetchall()
        ]

        # Cover letter authenticity pie based on report presence
        cur.execute("""
            SELECT
                CASE
                     WHEN r.cover_letter_analysis IS NULL THEN 'No Cover Letter'
                     WHEN r.ai_writing_score > 50 THEN 'Suspicious / Likely AI'
                     WHEN r.ai_writing_score BETWEEN 31 AND 50 THEN 'Possibly AI-Assisted'
                ELSE 'Authentic / Real'
                END AS label,
                COUNT(*)
            FROM resumes r
                JOIN jobs j ON r.job_id = j.id
                WHERE j.client_id = %s
                GROUP BY label;

        """, (client_id,))
        cover_pie = [{"label": row[0], "value": row[1]} for row in cur.fetchall()]

        cur.close()
        conn.close()

        return jsonify({
            "jobDistribution": job_dist,
            "educationPie": edu_pie,
            "applicationTrends": timeline,
            "scoreExperiencePlot": score_exp,
            "authenticityPie": cover_pie
        })

    except Exception as e:
        logging.exception("Error in /statistics")
        return jsonify({"error": str(e)}), 500


@app.route("/jobs", methods=["GET"])
def get_jobs():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify([])

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("SELECT id, job_title, job_description, created_at FROM jobs WHERE client_id = %s", (client_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify([
    {
        "id": r[0],
        "title": r[1],
        "description": r[2],
        "created_at": r[3]
    }
    for r in rows
])

    except Exception as e:
        logging.exception("Error in /jobs")
        return jsonify([])

@app.route("/jobs/<int:job_id>", methods=["GET"])
def get_job_by_id(job_id):
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("SELECT id, job_title, job_description FROM jobs WHERE id = %s;", (job_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row:
        return {
            "id": row[0],
            "job_title": row[1],
            "job_description": row[2]
        }
    else:
        return {"error": "Job not found"}, 404


@app.route("/resumes", methods=["GET"])
def get_resumes_by_client_id():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            SELECT id, job_id, candidate_name, email, score
            FROM resumes
            WHERE job_id IN (
                SELECT id FROM jobs WHERE client_id = %s
            );
        """, (client_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        resumes = [
            {
                "id": row[0],
                "job_id": row[1],
                "candidate_name": row[2],
                "email": row[3],
                "score": row[4]
            }
            for row in rows
        ]
        return jsonify(resumes)

    except Exception as e:
        print("Error in /resumes route:", str(e))
        return jsonify({"error": str(e)}), 500

@app.route("/jobs/create", methods=["POST"])
def create_job():
    data = request.get_json()
    title = data.get("title")
    description = data.get("description")
    client_id = data.get("client_id")

    if not all([title, description, client_id]):
        return jsonify({"error": "Missing fields"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO jobs (job_title, job_description, client_id)
            VALUES (%s, %s, %s) RETURNING id;
        """, (title, description, client_id))
        job_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": "Job created", "job_id": job_id})

    except Exception as e:
        logging.exception("Error in /jobs/create")
        return jsonify({"error": str(e)}), 500

@app.route("/jobs/<int:job_id>", methods=["PATCH"])
def update_job(job_id):
    data = request.get_json()
    title = data.get("title")
    description = data.get("description")

    if not all([title, description]):
        return jsonify({"error": "Missing fields"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            UPDATE jobs
            SET job_title = %s, job_description = %s
            WHERE id = %s
        """, (title, description, job_id))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": "Job updated successfully"})
    except Exception as e:
        logging.exception("Error in PATCH /jobs")
        return jsonify({"error": str(e)}), 500
    
@app.route("/jobs/<int:job_id>", methods=["DELETE"])
def delete_job(job_id):
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": "Job deleted successfully"})
    except Exception as e:
        logging.exception("Error in DELETE /jobs")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
