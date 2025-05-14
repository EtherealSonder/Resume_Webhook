from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from Resume_Parser import process_resume_file
import tempfile
import os
import logging
import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

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
    result = process_resume_file(file_path, job_title, cover_letter, client_id)
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
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("""
            SELECT r.candidate_name, r.email, r.score, r.experience_years, j.job_title
            FROM resumes r
            JOIN jobs j ON r.job_id = j.id
            ORDER BY r.score DESC;
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        candidates = [
            {
                "name": row[0],
                "email": row[1],
                "score": row[2],
                "experience": row[3],
                "job_title": row[4]
            }
            for row in rows
        ]
        return jsonify(candidates)

    except Exception as e:
        logging.exception("Error fetching candidates")
        return jsonify({"error": str(e)}), 500

@app.route("/dashboard", methods=["GET"])
def dashboard():
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), AVG(score) FROM resumes;")
        count, avg = cur.fetchone()
        cur.close()
        conn.close()

        return jsonify({
            "totalCandidates": count or 0,
            "averageScore": round(avg or 0, 2)
        })

    except Exception as e:
        logging.exception("Error in /dashboard")
        return jsonify({"error": str(e)}), 500

@app.route("/jobs", methods=["GET"])
def jobs():
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("SELECT job_title FROM jobs;")
        titles = cur.fetchall()
        cur.close()
        conn.close()

        return jsonify([{"title": row[0]} for row in titles])

    except Exception as e:
        logging.exception("Error in /jobs")
        return jsonify({"error": str(e)}), 500

@app.route("/statistics", methods=["GET"])
def statistics():
    try:
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        cur = conn.cursor()
        cur.execute("SELECT job_title, COUNT(*) FROM resumes r JOIN jobs j ON r.job_id = j.id GROUP BY job_title;")
        job_dist = cur.fetchall()
        cur.execute("SELECT AVG(score) FROM resumes;")
        avg_score = cur.fetchone()[0]
        cur.close()
        conn.close()

        return jsonify({
            "jobDistribution": {row[0]: row[1] for row in job_dist},
            "averageScore": round(avg_score or 0, 2)
        })

    except Exception as e:
        logging.exception("Error in /statistics")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
