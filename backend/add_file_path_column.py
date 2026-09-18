import sqlite3

conn = sqlite3.connect("risk.db")
cursor = conn.cursor()

cursor.execute(
    "ALTER TABLE assessment_documents ADD COLUMN file_path VARCHAR(500)"
)

conn.commit()
conn.close()

print("Column added successfully.")