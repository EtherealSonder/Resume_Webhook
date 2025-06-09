# -*- coding: utf-8 -*-

from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from Resume_Parser import process_resume_file
from job_parser import parse_job_description_with_gpt

from s3_utils import upload_to_s3 
import tempfile
import os
import logging
import json

import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import threading
load_dotenv()

app = Flask(__name__)
CORS(app, supports_credentials=True, origins=["https://lupiq-frontend-i5a7qre0f-gunabalan-lingams-projects.vercel.app"])
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

@app.route("/update_user", methods=["POST"])
def update_user():
    data = request.get_json()
    client_id = data.get("id")
    field = data.get("field")
    value = data.get("value")

    if field not in ["name", "email"]:
        return jsonify({"success": False, "message": "Invalid field"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        # Debug logging
        print("Attempting to update:", field, "=", value)

        # Email conflict check
        if field == "email":
            cur.execute("SELECT id FROM clients WHERE email = %s AND id != %s", (value, client_id))
            if cur.fetchone():
                return jsonify({"success": False, "message": "Email already exists"}), 400

        if field == "name":
            cur.execute("SELECT id FROM clients WHERE name = %s AND id != %s", (value, client_id))
            if cur.fetchone():
                return jsonify({"success": False, "message": "Name already exists"}), 400

        cur.execute(f"UPDATE clients SET {field} = %s WHERE id = %s", (value, client_id))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

from werkzeug.security import check_password_hash, generate_password_hash

@app.route("/verify_password", methods=["POST"])
def verify_password():
    data = request.get_json()
    email = data.get("email")
    password = data.get("password")

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("SELECT password FROM clients WHERE email = %s", (email,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row and check_password_hash(row[0], password):
            return jsonify({"valid": True})
        return jsonify({"valid": False})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"valid": False, "error": str(e)}), 500


@app.route("/update_password", methods=["POST"])
def update_password():
    data = request.get_json()
    email = data.get("email")
    current_password = data.get("current_password")
    new_password = data.get("new_password")

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        cur.execute("SELECT password FROM clients WHERE email = %s", (email,))
        row = cur.fetchone()
        if not row or not check_password_hash(row[0], current_password):
            cur.close()
            conn.close()
            return jsonify({"success": False, "message": "Incorrect current password"}), 403

        hashed = generate_password_hash(new_password)
        cur.execute("UPDATE clients SET password = %s WHERE email = %s", (hashed, email))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/delete_user", methods=["POST"])
def delete_user():
    data = request.get_json()
    user_id = data.get("id")
    print("DELETE USER ID:", user_id)

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        cur.execute("DELETE FROM clients WHERE id = %s;", (user_id,))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/client_preferences", methods=["GET"])
def get_client_preferences_route():
    client_id = request.args.get("client_id")

    if not client_id:
        return jsonify({"success": False, "error": "Missing client ID"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))        
        cur = conn.cursor()
        cur.execute("""
            SELECT custom_eval_prompt,
                   skill_match_weight,
                   education_match_weight,
                   experience_match_weight,
                   portfolio_match_weight,
                   certifications_match_weight,
                   soft_skills_weight,
                   language_weight,
                   previous_role_alignment_weight
            FROM clients WHERE id = %s
        """, (client_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"success": False, "error": "Client not found"}), 404

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

        return jsonify({"success": True, "prompt": prompt, "weights": weights})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/client_preferences/update", methods=["POST"])
def update_client_preferences():
    data = request.get_json()
    client_id = data.get("client_id")
    prompt = data.get("custom_eval_prompt")
    weights = data.get("weights", {})

    if not client_id:
        return jsonify({"success": False, "error": "Missing client ID"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))        
        cur = conn.cursor()

        cur.execute("""
            UPDATE clients SET
                custom_eval_prompt = %s,
                skill_match_weight = %s,
                education_match_weight = %s,
                experience_match_weight = %s,
                portfolio_match_weight = %s,
                certifications_match_weight = %s,
                soft_skills_weight = %s,
                language_weight = %s,
                previous_role_alignment_weight = %s
            WHERE id = %s
        """, (
            prompt,
            weights.get("skill_match"),
            weights.get("education_match"),
            weights.get("experience_match"),
            weights.get("portfolio_match"),
            weights.get("certifications_match"),
            weights.get("soft_skills_match"),
            weights.get("language_match"),
            weights.get("previous_role_alignment"),
            client_id
        ))

        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


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
                   r.resume_url, r.resume_quality_score, r.resume_quality_breakdown,
                   r.skill_match_breakdown, r.score_breakdown  
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
                "resume_quality_score": row[21] or 0,
                "resume_quality_breakdown": row[22] or {},
                "skill_match_breakdown": row[23] or "",
                "score_breakdown": row[24] or {}  
            }
            for row in rows
        ]
        return jsonify(candidates)

    except Exception as e:
        logging.exception("Error fetching candidates")
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

        # 2. Most Applied Jobs
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

        # 3. Applications Timeline — Include job_title for job-based filter
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
                "date": r[0].isoformat(),
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


from datetime import timedelta, datetime

@app.route("/dashboard", methods=["GET"])
def get_dashboard_metrics():
    client_id = request.args.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()

        # Build 7-day window
        today = datetime.today().date()
        days_of_week = [(today - timedelta(days=i)).strftime("%a") for i in reversed(range(7))]
        daily_candidates_data = [{"name": day, "value": 0} for day in days_of_week]
        daily_jobs_data = [{"name": day, "value": 0} for day in days_of_week]

        # Candidates - counts per day for the last 7 days
        cur.execute("""
            SELECT DATE(r.application_date), COUNT(*) FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s AND r.application_date >= CURRENT_DATE - INTERVAL '6 days'
            GROUP BY DATE(r.application_date)
            ORDER BY DATE(r.application_date);
        """, (client_id,))
        for row in cur.fetchall():
            day_name = row[0].strftime("%a")
            for entry in daily_candidates_data:
                if entry["name"] == day_name:
                    entry["value"] = row[1]

        # Candidates this week
        cur.execute("""
            SELECT COUNT(*) FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s AND DATE_PART('week', r.application_date) = DATE_PART('week', CURRENT_DATE)
            AND DATE_PART('year', r.application_date) = DATE_PART('year', CURRENT_DATE);
        """, (client_id,))
        candidates_this_week = cur.fetchone()[0]

        # Candidates last week
        cur.execute("""
            SELECT COUNT(*) FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            WHERE j.client_id = %s AND DATE_PART('week', r.application_date) = DATE_PART('week', CURRENT_DATE) - 1
            AND DATE_PART('year', r.application_date) = DATE_PART('year', CURRENT_DATE);
        """, (client_id,))
        candidates_last_week = cur.fetchone()[0]

        # Jobs - counts per day for the last 7 days
        cur.execute("""
            SELECT DATE(created_at), COUNT(*) FROM jobs
            WHERE client_id = %s AND created_at >= CURRENT_DATE - INTERVAL '6 days'
            GROUP BY DATE(created_at)
            ORDER BY DATE(created_at);
        """, (client_id,))
        for row in cur.fetchall():
            day_name = row[0].strftime("%a")
            for entry in daily_jobs_data:
                if entry["name"] == day_name:
                    entry["value"] = row[1]

        # Jobs this week
        cur.execute("""
            SELECT COUNT(*) FROM jobs
            WHERE client_id = %s AND DATE_PART('week', created_at) = DATE_PART('week', CURRENT_DATE)
            AND DATE_PART('year', created_at) = DATE_PART('year', CURRENT_DATE);
        """, (client_id,))
        jobs_this_week = cur.fetchone()[0]

        # Jobs last week
        cur.execute("""
            SELECT COUNT(*) FROM jobs
            WHERE client_id = %s AND DATE_PART('week', created_at) = DATE_PART('week', CURRENT_DATE) - 1
            AND DATE_PART('year', created_at) = DATE_PART('year', CURRENT_DATE);
        """, (client_id,))
        jobs_last_week = cur.fetchone()[0]

        # Percentage change calculation
        def calc_pct_change(this_week, last_week):
            if last_week == 0:
                return 100.0 if this_week > 0 else 0.0
            return ((this_week - last_week) / last_week) * 100

        candidates_pct = calc_pct_change(candidates_this_week, candidates_last_week)
        jobs_pct = calc_pct_change(jobs_this_week, jobs_last_week)

        cur.close()
        conn.close()

        return jsonify({
            "candidates": {
                "this_week": candidates_this_week,
                "last_week": candidates_last_week,
                "percentage_change": round(candidates_pct, 1),
                "mini_graph_data": daily_candidates_data
            },
            "jobs": {
                "this_week": jobs_this_week,
                "last_week": jobs_last_week,
                "percentage_change": round(jobs_pct, 1),
                "mini_graph_data": daily_jobs_data
            }
        })

    except Exception as e:
        print("Error in /dashboard:", e)
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
        cur.execute("""
            SELECT id, job_title, job_description, created_at,
                expected_technical_skills, expected_soft_skills, expected_education_level,
                expected_experience_range, expected_certifications, expected_responsibilities,
                expected_portfolio_required, expected_languages, expected_tools,
                expected_work_environment, expected_availability, expected_salary_range,
                job_location_country, job_location_city, job_type,
                experience_level, application_deadline
            FROM jobs WHERE client_id = %s
        """, (client_id,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        jobs = []
        for row in rows:
            jobs.append({
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "created_at": row[3],
                "expected_technical_skills": row[4] or [],
                "expected_soft_skills": row[5] or [],
                "expected_education_level": row[6],
                "expected_experience_range": row[7],
                "expected_certifications": row[8] or [],
                "expected_responsibilities": row[9],
                "expected_portfolio_required": row[10],
                "expected_languages": row[11] or [],
                "expected_tools": row[12] or [],
                "expected_work_environment": row[13],
                "expected_availability": row[14],
                "expected_salary_range": row[15],
                "job_location_country": row[16],
                "job_location_city": row[17],
                "job_type": row[18],
                "experience_level": row[19],
                "application_deadline": row[20].isoformat() if row[20] else None
            })
        return jsonify(jobs)
    except Exception as e:
        logging.exception("Error in /jobs")
        return jsonify([])


@app.route("/jobs/<int:job_id>", methods=["GET"])
def get_job_by_id(job_id):
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            SELECT id, job_title, job_description, created_at,
                expected_technical_skills, expected_soft_skills, expected_education_level,
                expected_experience_range, expected_certifications, expected_responsibilities,
                expected_portfolio_required, expected_languages, expected_tools,
                expected_work_environment, expected_availability, expected_salary_range,
                job_location_country, job_location_city, job_type,
                experience_level, application_deadline
            FROM jobs WHERE id = %s;
        """, (job_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            job = {
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "created_at": row[3],
                "expected_technical_skills": row[4] or [],
                "expected_soft_skills": row[5] or [],
                "expected_education_level": row[6],
                "expected_experience_range": row[7],
                "expected_certifications": row[8] or [],
                "expected_responsibilities": row[9],
                "expected_portfolio_required": row[10],
                "expected_languages": row[11] or [],
                "expected_tools": row[12] or [],
                "expected_work_environment": row[13],
                "expected_availability": row[14],
                "expected_salary_range": row[15],
                "job_location_country": row[16],
                "job_location_city": row[17],
                "job_type": row[18],
                "experience_level": row[19],
                "application_deadline": row[20].isoformat() if row[20] else None
            }
            return jsonify(job)
        else:
            return jsonify({"error": "Job not found"}), 404

    except Exception as e:
        logging.exception("Error in GET /jobs/<job_id>")
        return jsonify({"error": str(e)}), 500



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

    # New fields from frontend (optional - fallback to GPT parsing or defaults)
    job_location_country = data.get("job_location_country", "")
    job_location_city = data.get("job_location_city", "")
    job_type = data.get("job_type", "")
    experience_level = data.get("experience_level", "No expectation on experience")
    application_deadline = data.get("application_deadline", None)  # format: YYYY-MM-DD

    # First, parse job description with GPT to fill expected fields
    parsed_data = parse_job_description_with_gpt(description)

    # Use recruiter-provided expected fields if present, otherwise fallback to parsed data
    expected_technical_skills = data.get("expected_technical_skills", parsed_data["expected_technical_skills"])
    expected_soft_skills = data.get("expected_soft_skills", parsed_data["expected_soft_skills"])
    expected_education_level = data.get("expected_education_level", parsed_data["expected_education_level"])
    expected_experience_range = data.get("expected_experience_range", parsed_data["expected_experience_range"])
    expected_certifications = data.get("expected_certifications", parsed_data["expected_certifications"])
    expected_responsibilities = data.get("expected_responsibilities", parsed_data["expected_responsibilities"])
    expected_portfolio_required = data.get("expected_portfolio_required", parsed_data["expected_portfolio_required"])
    expected_languages = data.get("expected_languages", parsed_data["expected_languages"])
    expected_tools = data.get("expected_tools", parsed_data["expected_tools"])
    expected_work_environment = data.get("expected_work_environment", parsed_data["expected_work_environment"])
    expected_availability = data.get("expected_availability", parsed_data["expected_availability"])
    expected_salary_range = data.get("expected_salary_range", parsed_data["expected_salary_range"])

    if not all([title, description, client_id]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO jobs (
                job_title, job_description, client_id,
                expected_technical_skills, expected_soft_skills, expected_education_level,
                expected_experience_range, expected_certifications, expected_responsibilities,
                expected_portfolio_required, expected_languages, expected_tools,
                expected_work_environment, expected_availability, expected_salary_range,
                job_location_country, job_location_city, job_type,
                experience_level, application_deadline
            )
            VALUES (
                %s, %s, %s,
                %s::jsonb, %s::jsonb, %s,
                %s, %s::jsonb, %s,
                %s, %s::jsonb, %s::jsonb,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s
            )
            RETURNING id;
        """, (
            title, description, client_id,
            json.dumps(expected_technical_skills), json.dumps(expected_soft_skills), expected_education_level,
            expected_experience_range, json.dumps(expected_certifications), expected_responsibilities,
            expected_portfolio_required, json.dumps(expected_languages), json.dumps(expected_tools),
            expected_work_environment, expected_availability, expected_salary_range,
            job_location_country, job_location_city, job_type,
            experience_level, application_deadline
        ))
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
    job_location_country = data.get("job_location_country", "")
    job_location_city = data.get("job_location_city", "")
    job_type = data.get("job_type", "")
    experience_level = data.get("experience_level", "No expectation on experience")
    application_deadline = data.get("application_deadline", None)

    expected_technical_skills = data.get("expected_technical_skills", [])
    expected_soft_skills = data.get("expected_soft_skills", [])
    expected_education_level = data.get("expected_education_level", "No expectation on education level")
    expected_experience_range = data.get("expected_experience_range", "No expectation on experience")
    expected_certifications = data.get("expected_certifications", [])
    expected_responsibilities = data.get("expected_responsibilities", "No specific responsibilities provided")
    expected_portfolio_required = data.get("expected_portfolio_required", False)
    expected_languages = data.get("expected_languages", [])
    expected_tools = data.get("expected_tools", [])
    expected_work_environment = data.get("expected_work_environment", "No specific work environment provided")
    expected_availability = data.get("expected_availability", "No specific availability provided")
    expected_salary_range = data.get("expected_salary_range", "No salary expectation provided")

    if not all([title, description]):
        return jsonify({"error": "Missing required fields: title and description"}), 400

    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            UPDATE jobs
            SET job_title = %s,
                job_description = %s,
                job_location_country = %s,
                job_location_city = %s,
                job_type = %s,
                experience_level = %s,
                application_deadline = %s,
                expected_technical_skills = %s::jsonb,
                expected_soft_skills = %s::jsonb,
                expected_education_level = %s,
                expected_experience_range = %s,
                expected_certifications = %s::jsonb,
                expected_responsibilities = %s,
                expected_portfolio_required = %s,
                expected_languages = %s::jsonb,
                expected_tools = %s::jsonb,
                expected_work_environment = %s,
                expected_availability = %s,
                expected_salary_range = %s
            WHERE id = %s
        """, (
            title, description,
            job_location_country, job_location_city, job_type,
            experience_level, application_deadline,
            json.dumps(expected_technical_skills), json.dumps(expected_soft_skills), expected_education_level,
            expected_experience_range, json.dumps(expected_certifications), expected_responsibilities,
            expected_portfolio_required, json.dumps(expected_languages), json.dumps(expected_tools),
            expected_work_environment, expected_availability, expected_salary_range,
            job_id
        ))
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"message": "Job updated successfully"})
    except Exception as e:
        logging.exception("Error in PATCH /jobs/<job_id>")
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

        return jsonify({"message": f"Job ID {job_id} deleted successfully"})
    except Exception as e:
        logging.exception(f"Error deleting job ID {job_id}")
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


@app.route("/analytics/trust_scores", methods=["GET"])
def get_trust_scores():
    job_id = request.args.get("job_id")
    min_score = request.args.get("min_score", 0, type=float)
    max_score = request.args.get("max_score", 100, type=float)
    sort_by = request.args.get("sort_by", "trust_score")

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    query = """
    SELECT
        r.candidate_name,
        r.email,
        r.job_id,
        j.job_title,
        r.human_likeness_score,
        r.plagiarism_pct,
        r.trust_score,
        r.resume_url,
        r.application_date
    FROM resumes r
    JOIN jobs j ON r.job_id = j.id
    WHERE r.trust_score BETWEEN %s AND %s
    """
    params = [min_score, max_score]

    if job_id:
        query += " AND r.job_id = %s"
        params.append(job_id)

    # Sort results
    if sort_by in ["trust_score", "human_likeness_score", "plagiarism_pct"]:
        query += f" ORDER BY r.{sort_by} DESC"

    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # Build response
    results = []
    for row in rows:
        results.append({
            "candidate_name": row[0],
            "email": row[1],
            "job_id": row[2],
            "job_title": row[3],
            "human_likeness_score": row[4],
            "plagiarism_pct": row[5],
            "trust_score": row[6],
            "resume_url": row[7],
            "submitted_on": row[8].strftime("%Y-%m-%d")
        })

    return jsonify(results)

@app.route("/analytics/cover_letter_quality", methods=["GET"])
def get_cover_letter_quality():
    client_id = request.args.get("client_id")
    job_title = request.args.get("job_title")

    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    # Determine the top job
    cur.execute("""
        SELECT j.job_title, COUNT(*) as cnt
        FROM resumes r
        JOIN jobs j ON r.job_id = j.id
        WHERE j.client_id = %s
        GROUP BY j.job_title
        ORDER BY cnt DESC
        LIMIT 1;
    """, (client_id,))
    top_job_row = cur.fetchone()
    top_job = top_job_row[0] if top_job_row else None

    # Main data query, only with cover letters
    query = """
    SELECT
        r.id,
        r.candidate_name,
        r.email,
        j.job_title,
        r.ai_writing_score AS cover_letter_score,
        r.resume_quality_score,
        r.application_date
    FROM resumes r
    JOIN jobs j ON r.job_id = j.id
    WHERE j.client_id = %s AND r.cover_letter_analysis IS NOT NULL
    """
    params = [client_id]

    if job_title:
        query += " AND j.job_title = %s"
        params.append(job_title)

    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    cover_letter_scores = [float(r[4]) for r in rows if r[4] is not None]
    avg_cover_letter_score = round(sum(cover_letter_scores) / len(cover_letter_scores), 2) if cover_letter_scores else 0

    bottom_candidates = sorted(
        [
            {
                "id": r[0],
                "name": r[1],
                "email": r[2],
                "job_title": r[3],
                "cover_letter_score": float(r[4]) if r[4] else 0,
                "resume_quality_score": float(r[5]) if r[5] else 0,
                "submitted_at": r[6].isoformat() if r[6] else ""
            }
            for r in rows
        ],
        key=lambda x: (x["cover_letter_score"] + x["resume_quality_score"])
    )[:10]

    return jsonify({
        "average_cover_letter_score": avg_cover_letter_score,
        "bottom_candidates": bottom_candidates,
        "top_job": top_job
    })






if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
