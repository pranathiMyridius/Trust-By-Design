import os
import uuid

STORAGE_DIR = os.path.join(os.getcwd(), "uploaded_files")
os.makedirs(STORAGE_DIR, exist_ok=True)


def save_file(assessment_id: int, filename: str, file_content: bytes) -> str:
    safe_name = f"{assessment_id}_{uuid.uuid4().hex}_{filename}"
    full_path = os.path.join(STORAGE_DIR, safe_name)

    with open(full_path, "wb") as f:
        f.write(file_content)

    return full_path