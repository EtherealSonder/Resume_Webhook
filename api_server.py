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
import threading
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

    if not resume_file:
        return jsonify({"error": "Missing resume"}), 400

    # Save resume to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
        resume_file.save(temp.name)
        file_path = temp.name

    # Lookup job title and client ID
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute("SELECT job_title, client_id FROM jobs WHERE id = %s", (job_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({"error": "Invalid job ID"}), 400

    job_title, client_id = row

    def background_task():
        try:
            from s3_utils import upload_to_s3
            s3_url = upload_to_s3(file_path, job_id, resume_file.filename)
            process_resume_file(file_path, job_title, cover_letter, client_id, resume_source="form", resume_url=s3_url)

            # Signal for frontend
            with open("new_resume_notification.flag", "w") as flag_file:
                flag_file.write("1")
        finally:
            os.unlink(file_path)

    threading.Thread(target=background_task).start()
    return jsonify({"message": "Application received. Processing in background."})


@app.route("/notification_status", methods=["GET"])
def notification_status():
    try:
        if os.path.exists("new_resume_notification.flag"):
            conn = psycopg2.connect(os.getenv("DATABASE_URL"))
            cur = conn.cursor()
            cur.execute("""
                SELECT candidate_name, application_date
                FROM resumes
                ORDER BY application_date DESC
                LIMIT 1;
            """)
            row = cur.fetchone()
            cur.close()
            conn.close()

            return jsonify({
                "new_resume": True,
                "latest_resume": {
                    "candidate_name": row[0],
                    "application_date": row[1].isoformat()
                }
            })
        else:
            return jsonify({"new_resume": False})
    except Exception as e:
        return jsonify({"error": str(e)})
    
@app.route("/clear_notification", methods=["POST"])
def clear_notification():
    try:
        if os.path.exists("new_resume_notification.flag"):
            os.remove("new_resume_notification.flag")
        return jsonify({"cleared": True})
    except Exception as e:
        return jsonify({"error": str(e)})

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
                   r.resume_url, r.resume_quality_score
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
                "resume_url": row[20] or "",
                "resume_quality_score": row[21] or 0
                
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
def get_statistics():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        # 1. Score vs Experience Plot
        cur.execute("""
            SELECT r.candidate_name, r.score, r.experience_years, j.job_title
            FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s;
        """, (client_id,))
        rows = cur.fetchall()
        score_experience_data = [
            {
                "candidate_name": r[0] or "Unnamed",
                "score": float(r[1]),
                "experience": float(r[2]),
                "job_title": r[3]
            }
            for r in rows if r[1] is not None and r[2] is not None
        ]

        # 2. Most Applied Jobs using CTE
        cur.execute("""
            WITH ranked_resumes AS (
                SELECT
                    j.job_title,
                    j.created_at,
                    r.candidate_name,
                    r.score,
                    ROW_NUMBER() OVER (PARTITION BY j.job_title ORDER BY r.score DESC) as rank
                FROM resumes r
                JOIN jobs j ON r.job_id = j.id
                WHERE j.client_id = %s
            )
            SELECT
                rr.job_title,
                COUNT(*) as application_count,
                ROUND(AVG(rr.score)::numeric, 1) as avg_score,
                MAX(rr.created_at) as created_at,
                MAX(rr.candidate_name) FILTER (WHERE rr.rank = 1) as top_candidate_name,
                MAX(rr.score) FILTER (WHERE rr.rank = 1) as top_candidate_score
            FROM ranked_resumes rr
            GROUP BY rr.job_title
            ORDER BY application_count DESC;
        """, (client_id,))
        most_applied_jobs_rows = cur.fetchall()
        most_applied_jobs = [
            {
                "job_title": r[0],
                "application_count": r[1],
                "avg_score": float(r[2]) if r[2] is not None else 0,
                "created_at": r[3] if isinstance(r[3], str) else r[3].isoformat() if r[3] else None,
                "top_candidate_name": r[4] or "None",
                "top_candidate_score": float(r[5]) if r[5] is not None else 0
            }
            for r in most_applied_jobs_rows
        ]

        # 3. Applications Timeline — now includes job_title
        cur.execute("""
            SELECT DATE(r.application_date), j.job_title, COUNT(*) as count
            FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s
            GROUP BY DATE(r.application_date), j.job_title
            ORDER BY DATE(r.application_date) ASC;
        """, (client_id,))
        timeline_rows = cur.fetchall()
        application_timeline = [
            {
                "date": r[0] if isinstance(r[0], str) else r[0].isoformat(),
                "job_title": r[1],
                "count": r[2]
            }
            for r in timeline_rows
        ]

        cur.close()
        conn.close()

        return jsonify({
            "scoreExperiencePlot": score_experience_data,
            "mostAppliedJobs": most_applied_jobs,
            "applicationTimeline": application_timeline
        })

    except Exception as e:
        print("Error in /statistics:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/statistics/distributions", methods=["GET"])
def get_distributions():
    client_id = request.args.get("client_id")
    job_titles = request.args.getlist("job_titles[]")
    
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        # Build filter condition
        if job_titles:
            cur.execute("""
                SELECT r.score, r.experience_years, r.education_level
                FROM resumes r
                JOIN jobs j ON r.job_id = j.id
                WHERE j.client_id = %s AND j.job_title = ANY(%s)
            """, (client_id, job_titles))
        else:
            cur.execute("""
                SELECT r.score, r.experience_years, r.education_level
                FROM resumes r
                JOIN jobs j ON r.job_id = j.id
                WHERE j.client_id = %s
            """, (client_id,))

        rows = cur.fetchall()
        cur.close()
        conn.close()

        scoreBuckets = {f"{i}-{i+9}": 0 for i in range(0, 100, 10)}
        scoreBuckets["100"] = 0

        experienceHistogram = {
                    "0": 0, "0 - 1": 0, "1 - 2": 0, "2 - 4": 0,
                    "4 - 7": 0, "7 - 10": 0, "10 - 15": 0, "15+": 0
        }
        educationLevels = {}

        for score, exp, edu in rows:
            if score is not None:
                if score == 100:
                    scoreBuckets["100"] += 1
                else:
                    bucket = f"{int(score // 10) * 10}-{int(score // 10) * 10 + 9}"
                    scoreBuckets[bucket] += 1

            if exp is not None:
                if exp == 0:
                    experienceHistogram["0"] += 1
                elif exp <= 1:
                    experienceHistogram["0 - 1"] += 1
                elif exp <= 2:
                    experienceHistogram["1 - 2"] += 1
                elif exp <= 4:
                    experienceHistogram["2 - 4"] += 1
                elif exp <= 7:
                    experienceHistogram["4 - 7"] += 1
                elif exp <= 10:
                    experienceHistogram["7 - 10"] += 1
                elif exp <= 15:
                    experienceHistogram["10 - 15"] += 1
                else:
                    experienceHistogram["15+"] += 1

            edu_clean = (edu or "Other").strip()
            educationLevels[edu_clean] = educationLevels.get(edu_clean, 0) + 1

        return jsonify({
            "scoreBuckets": [{"range": k, "count": v} for k, v in scoreBuckets.items()],
            "experienceHistogram": [{"range": k, "count": v} for k, v in experienceHistogram.items()],
            "educationLevels": [{"label": k, "value": v} for k, v in educationLevels.items()]
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/statistics/skills", methods=["GET"])
def get_skill_insights():
    client_id = request.args.get("client_id")
    job_titles = request.args.getlist("job_titles[]")

    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        if job_titles:
            cur.execute("""
                SELECT r.technical_skills, r.soft_skills
                FROM resumes r
                JOIN jobs j ON r.job_id = j.id
                WHERE j.client_id = %s AND j.job_title = ANY(%s);
            """, (client_id, job_titles))
        else:
            cur.execute("""
                SELECT r.technical_skills, r.soft_skills
                FROM resumes r
                JOIN jobs j ON r.job_id = j.id
                WHERE j.client_id = %s;
            """, (client_id,))
        
        rows = cur.fetchall()
        cur.close()
        conn.close()

        from collections import Counter

        tech_counter = Counter()
        soft_counter = Counter()

        for tech, soft in rows:
            tech_skills = tech if isinstance(tech, list) else []
            soft_skills = soft if isinstance(soft, list) else []

            tech_counter.update([s.strip() for s in tech_skills if s and isinstance(s, str)])
            soft_counter.update([s.strip() for s in soft_skills if s and isinstance(s, str)])

        top_tech = [{"skill": k, "count": v} for k, v in tech_counter.most_common(20)]
        top_soft = [{"skill": k, "count": v} for k, v in soft_counter.most_common(20)]

        return jsonify({
            "technical": top_tech,
            "soft": top_soft
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/statistics/skills/bubble", methods=["GET"])
def skill_bubble_data():
    client_id = request.args.get("client_id")
    skill_type = request.args.get("type", "technical")

    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        cur.execute(f"""
            SELECT j.job_title, r.{skill_type}_skills
            FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s;
        """, (client_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        from collections import defaultdict
        skill_by_job = defaultdict(lambda: defaultdict(int))

        for job_title, skills in rows:
            if not isinstance(skills, list):
                continue
            for s in skills:
                if isinstance(s, str) and s.strip():
                    skill_by_job[job_title][s.strip()] += 1

        result = []
        for job_title, skills in skill_by_job.items():
            for skill, count in skills.items():
                result.append({"job_title": job_title, "skill": skill, "count": count})

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/statistics/skills/grid", methods=["GET"])
def skill_grid_data():
    client_id = request.args.get("client_id")
    skill_type = request.args.get("type", "technical")

    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute(f"""
        SELECT j.job_title, r.{skill_type}_skills
        FROM resumes r
        JOIN jobs j ON r.job_id = j.id
        WHERE j.client_id = %s;
    """, (client_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    from collections import defaultdict
    grid = defaultdict(lambda: defaultdict(int))

    for job, skills in rows:
        if not isinstance(skills, list): continue
        for s in skills:
            if isinstance(s, str) and s.strip():
                grid[job][s.strip()] += 1

    # Format: [{ job_title: ..., skill: ..., count: ... }]
    result = []
    for job in grid:
        for skill in grid[job]:
            result.append({"job_title": job, "skill": skill, "count": grid[job][skill]})

    return jsonify(result)



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


@app.route("/analytics/rubric_breakdown", methods=["GET"])
def rubric_breakdown():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        # Fetch necessary data
        cur.execute("""
            SELECT
                resume_quality_score,
                skills_matched_pct,
                experience_years,
                education_level,
                certifications,
                soft_skills
            FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s;
        """, (client_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return jsonify({})

        total = len(rows)
        sum_resume_quality = sum(r[0] or 0 for r in rows)
        sum_skill_match = sum(r[1] or 0 for r in rows)
        sum_experience = sum(r[2] or 0 for r in rows)

        # Education to score
        edu_map = {"PhD": 100, "Master's": 80, "Bachelor's": 60, "Diploma": 40, "High School": 20, "Other": 10}
        edu_scores = [edu_map.get((r[3] or "").strip(), 0) for r in rows]
        sum_edu_score = sum(edu_scores)

        # Certifications = binary score
        cert_score = sum(10 if (r[4] and r[4].strip()) else 0 for r in rows)

        # Soft skills = count
        soft_skill_score = sum(len(r[5] or []) for r in rows)

        return jsonify({
            "avg_resume_quality": round(sum_resume_quality / total, 2),
            "avg_skill_match": round(sum_skill_match / total, 2),
            "avg_experience": round(sum_experience / total, 2),
            "avg_education_level_score": round(sum_edu_score / total, 2),
            "avg_certification_score": round(cert_score / total, 2),
            "avg_soft_skills_score": round(soft_skill_score / total, 2)
        })

    except Exception as e:
        print("Error in rubric_breakdown:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/statistics/skills/grouped_bar", methods=["GET"])
def skill_grouped_bar():
    client_id = request.args.get("client_id")
    skill_type = request.args.get("type", "technical")

    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute(f"""
        SELECT j.job_title, r.{skill_type}_skills
        FROM resumes r
        JOIN jobs j ON r.job_id = j.id
        WHERE j.client_id = %s;
    """, (client_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    from collections import defaultdict
    skill_data = defaultdict(lambda: defaultdict(int))

    for job, skills in rows:
        if not isinstance(skills, list): continue
        for s in skills:
            if isinstance(s, str) and s.strip():
                skill_data[s.strip()][job] += 1

    result = []
    for skill, jobs in skill_data.items():
        item = {"skill": skill}
        item.update(jobs)
        result.append(item)

    return jsonify(result)


@app.route("/statistics/skills/radar", methods=["GET"])
def skill_radar_data():
    client_id = request.args.get("client_id")
    job_titles = request.args.getlist("job_titles[]")
    skill_type = request.args.get("type", "technical")

    if not client_id or len(job_titles) != 2:
        return jsonify({"error": "Provide client_id and exactly 2 job_titles[]"}), 400

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()
    cur.execute(f"""
        SELECT j.job_title, r.{skill_type}_skills
        FROM resumes r
        JOIN jobs j ON r.job_id = j.id
        WHERE j.client_id = %s AND j.job_title = ANY(%s);
    """, (client_id, job_titles))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    from collections import defaultdict

    counter = defaultdict(lambda: defaultdict(int))
    for job, skills in rows:
        if not isinstance(skills, list): continue
        for s in skills:
            if isinstance(s, str) and s.strip():
                counter[job][s.strip()] += 1

    # Keep only common skills
    common_skills = set(counter[job_titles[0]].keys()) & set(counter[job_titles[1]].keys())

    # Build list of dicts with counts
    result = [
        {
            "skill": skill,
            job_titles[0]: counter[job_titles[0]][skill],
            job_titles[1]: counter[job_titles[1]][skill],
        }
        for skill in common_skills
    ]

    # Sort by total frequency and limit to top 15
    result = sorted(
        result,
        key=lambda x: x[job_titles[0]] + x[job_titles[1]],
        reverse=True
    )[:15]

    return jsonify(result)




if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
