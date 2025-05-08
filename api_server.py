from flask import Flask, request, jsonify
from flask_cors import CORS
from Resume_Parser import process_resume_file  # adjust if your import path is different
import tempfile
import os
import logging
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # Allow frontend access

# Optional: helpful logging
logging.basicConfig(level=logging.INFO)

@app.route("/parse_resume", methods=["POST"])
def parse_resume():
    file = request.files.get("resume")
    job_title = request.form.get("job_title")
    cover_letter = request.form.get("cover_letter", "")

    if not file or not job_title:
        return jsonify({"error": "Missing required fields."}), 400

    # Save file temporarily
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temp:
        file_path = temp.name
        file.save(file_path)

    try:
        result = process_resume_file(file_path, job_title, cover_letter)
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Exception in /parse_resume: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500
    finally:
        # Clean up the temp file
        try:
            os.unlink(file_path)
        except Exception as e:
            app.logger.warning(f"Could not delete temp file {file_path}: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
