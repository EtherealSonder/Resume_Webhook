# -*- coding: utf-8 -*-
import os
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from Resume_Parser import process_resume_file

FOLDER_ID = "1JoBXIRCzMFHsKCAY1IBz9zZ9I7Cr709lOcyaKcUiQN829Zl3EANSE9Om5NKsnM_Au9RDyw85"
SERVICE_ACCOUNT_FILE = r"D:\AI Resume Screener\resumescreener-458121-72ac2607bb54.json"
DOWNLOAD_DIR = "D:/AI Resume Screener/resumes"

def get_drive_service():
    scopes = ['https://www.googleapis.com/auth/drive']
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=scopes)
    return build('drive', 'v3', credentials=creds)

def download_file(file_id, file_name, drive_service):
    request = drive_service.files().get_media(fileId=file_id)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, file_name)
    with io.FileIO(file_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return file_path

def get_file_to_jobtitle_mapping(sheet_name="Form Responses 1"):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("resumescreener-458121-72ac2607bb54.json", scope)
    client = gspread.authorize(creds)

    sheet = client.open("Resume Upload Form").worksheet(sheet_name)
    rows = sheet.get_all_values()[1:]

    mapping = {}
    for row in rows:
        if len(row) >= 5:
            job_title = row[3].strip()
            file_link = row[4].strip()
            cover_letter = row[5].strip() if len(row) >= 6 else ""
            client_id = row[6].strip() if len(row) >= 7 else ""
            if "id=" in file_link:
                file_id = file_link.split("id=")[-1]
                mapping[file_id] = (job_title, cover_letter, client_id)
    return mapping

def scan_drive_for_resumes():
    drive_service = get_drive_service()
    file_to_job = get_file_to_jobtitle_mapping()

    query = f"'{FOLDER_ID}' in parents and mimeType='application/pdf'"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get('files', [])

    for file in files:
        file_id = file['id']
        file_name = file['name']
        local_path = os.path.join(DOWNLOAD_DIR, file_name)

        if os.path.exists(local_path):
            continue

        downloaded_path = download_file(file_id, file_name, drive_service)
        job_info = file_to_job.get(file_id, ("Unknown Role", "", ""))
        job_title, cover_letter, client_id = job_info

        process_resume_file(downloaded_path, job_title, cover_letter=cover_letter, client_id=client_id, resume_source="gdrive")

if __name__ == "__main__":
    scan_drive_for_resumes()
