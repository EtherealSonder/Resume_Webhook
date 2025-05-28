# copyleaks_client.py

import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

COPYLEAKS_API_KEY = os.getenv("COPYLEAKS_API_KEY")
COPYLEAKS_EMAIL = os.getenv("COPYLEAKS_EMAIL")

# Correct subdomains for Copyleaks endpoints
AUTH_URL = "https://id.copyleaks.com/v3/account/login/api"
API_BASE_URL = "https://api.copyleaks.com"
AI_DETECTION_URL = "https://id.copyleaks.com/v3/ai/content/detect"

def get_access_token():
    response = requests.post(
        AUTH_URL,
        json={"email": COPYLEAKS_EMAIL, "key": COPYLEAKS_API_KEY}
    )
    response.raise_for_status()
    return response.json()["access_token"]

def check_ai_content(text):
    if not text.strip():
        print("Warning: Empty text submitted to AI content detection.")
        return 0

    MAX_TEXT_LENGTH = 100000
    if len(text) > MAX_TEXT_LENGTH:
        print("Text too large for Copyleaks AI detection.")
        return 0

    token = get_access_token()
    try:
        response = requests.post(
            AI_DETECTION_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"text": text}
        )
        response.raise_for_status()
        result = response.json()
        return result.get("aiScore", 0)
    except requests.exceptions.HTTPError as e:
        print("Copyleaks AI detection error:", e)
        print("Skipping AI detection for this document and continuing without it.")
        return 0
    except Exception as e:
        print("Unexpected error during Copyleaks AI detection:", e)
        return 0


def check_plagiarism(text):
    if not text.strip():
        print("Warning: Empty text submitted to plagiarism detection.")
        return 0

    token = get_access_token()
    try:
        # Step 1: Submit text for plagiarism scan
        submit_response = requests.post(
            f"{API_BASE_URL}/v3/scans/submit/text",
            headers={"Authorization": f"Bearer {token}"},
            json={"base64": False, "text": text}
        )
        submit_response.raise_for_status()
        scan_id = submit_response.json()["scanId"]

        # Step 2: Poll for results (wait up to 30 seconds)
        for _ in range(30):
            result_response = requests.get(
                f"{API_BASE_URL}/v3/scans/{scan_id}/result",
                headers={"Authorization": f"Bearer {token}"}
            )
            if result_response.status_code == 200:
                result_data = result_response.json()
                plagiarism_pct = result_data.get("results", {}).get("totalPercent", 0)
                return plagiarism_pct
            elif result_response.status_code == 404:
                time.sleep(1)
            else:
                break
        # If polling times out
        return 0
    except requests.exceptions.HTTPError as e:
        print("Copyleaks plagiarism detection error:", e)
        print("Skipping plagiarism detection for this document and continuing.")
        return 0
    except Exception as e:
        print("Unexpected error during Copyleaks plagiarism detection:", e)
        return 0

