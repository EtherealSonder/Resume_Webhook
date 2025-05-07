from flask import Flask, request
import json
import requests
import os
import traceback

from Resume_Parser import process_resume_file

app = Flask(__name__)
DOWNLOAD_DIR = "resumes"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def download_from_drive(share_link):
    if "id=" in share_link:
        file_id = share_link.split("id=")[-1]
    elif "/file/d/" in share_link:
        file_id = share_link.split("/file/d/")[1].split("/")[0]
    else:
        raise ValueError("Invalid Google Drive link format.")

    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    local_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.pdf")

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    with requests.get(download_url, headers=headers, stream=True) as r:
        if "text/html" in r.headers.get("Content-Type", ""):
            raise ValueError("Google Drive file is not publicly accessible or not a PDF.")
        with open(local_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    return local_path


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    print("\nReceived Data from Zapier:")
    print(json.dumps(data, indent=2))

    file_url = data.get("resume_url", "")
    job_title = data.get("job_title", "")

    try:
        local_path = download_from_drive(file_url)
        process_resume_file(local_path, job_title, file_url)
        return "Resume downloaded and processed", 200
    except Exception as e:
        print("Error:", e)
        traceback.print_exc() 
        return f"Error: {str(e)}", 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)