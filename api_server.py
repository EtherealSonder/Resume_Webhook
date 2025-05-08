from flask import Flask, request, jsonify
from flask_cors import CORS
from Resume_Parser import process_resume_file  # adjust import path if needed
import tempfile
import os

app = Flask(__name__)
CORS(app)  # Allow requests from frontend

@app.route("/parse_resume", methods=["POST"])
def parse_resume():
    file = request.files.get("resume")
    job_title = request.form.get("job_title")
    cover_letter = request.form.get("cover_letter", "")

    if not file or not job_title:
        return jsonify({"error": "Missing required fields."}), 400

    # Save file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
        file.save(temp.name)
        result = process_resume_file(temp.name, job_title, cover_letter)
        os.unlink(temp.name)

    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)