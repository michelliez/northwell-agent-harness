from pathlib import Path
import hashlib
import sqlite3

from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR/"docs"
DB_PATH = BASE_DIR/"rag_index.sqlite"

#hash function
def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


connect = sqlite3.connect(DB_PATH)
cursor = connect.cursor()

#Create tables
cursor.executescript("""
DROP TABLE IF EXISTS docs;
DROP TABLE IF EXISTS chunks;
DROP TABLE IF EXISTS chunks_fts;

CREATE TABLE docs (
    doc_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    title TEXT,
    source_hash TEXT NOT NULL
);

CREATE TABLE chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    heading_path TEXT,
    text TEXT NOT NULL
);

CREATE VIRTUAL TABLE chunks_fts USING fts5 (
    chunk_id,
    title,
    heading_path,
    text
);
""")

# #Read
# path = DOCS_DIR/"appointments.html"
# html = path.read_text(encoding="utf-8")
# soup = BeautifulSoup(html, "html.parser")

# #Get title
# title = soup.title.get_text(strip = True)
# doc_id = path.stem
# source_hash = sha(html)

# cursor.execute("""
# INSERT INTO docs (doc_id, source_path, title, source_hash)
# VALUES (?, ?, ?, ?)
# """,
# (doc_id, str(path), title, source_hash),
# )

# #Chunk row
# heading_path = "Appointments > Status"
# text = "The status field describes whether an appointment is scheduled, completed, cancelled, or no-show."
# chunk_id = sha(f"{doc_id}:{heading_path}:{text}")

# #insert into chunks:
# cursor.execute("""
# INSERT INTO chunks (chunk_id, doc_id, heading_path, text)
# VALUES (?, ?, ?, ?)
# """,
# (chunk_id, doc_id, heading_path, text),
# )

# cursor.execute("""
# INSERT INTO chunks_fts (chunk_id, title, heading_path, text)
# VALUES (?, ?, ?, ?)
# """,
# (chunk_id, title, heading_path, text),
# )

for path in DOCS_DIR.glob("*.html"):
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True)
    doc_id = path.stem
    source_hash = sha(html)

    cursor.execute("""
    INSERT INTO docs (doc_id, source_path, title, source_hash)
    VALUES (?, ?, ?, ?)            
    """,
    (doc_id, str(path), title, source_hash),
    )

    heading_path = title
    text = soup.get_text(" ", strip = True)
    chunk_id = sha(f"{doc_id}:{heading_path}:{text}")
    cursor.execute("""
    INSERT INTO chunks_fts (chunk_id, title, heading_path, text)
    VALUES (?, ?, ?, ?)
    """,
    (chunk_id, title, heading_path, text),)
    print(f"Indexed {path}")
connect.commit()
connect.close()