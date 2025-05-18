def extract_education_level(text):
    text = text.lower()
    if "phd" in text or "doctorate" in text:
        return "PhD"
    elif "master" in text or "msc" in text:
        return "Master's"
    elif "bachelor" in text or "b.tech" in text or "bsc" in text:
        return "Bachelor's"
    elif "12th" in text or "high school" in text:
        return "High School"
    else:
        return "Other"

def calculate_experience_years(experience_entries):
    return len(experience_entries)  # You can improve this by parsing actual durations
