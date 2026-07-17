from pathlib import Path
import sqlite3
import sys

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR/"rag_index.sqlite"

query = " ".join(sys.argv[1:]).strip()
if not query:
    raise SystemExit("Usage: python search_mock_docs.py <search_query>")

connect = sqlite3.connect(DB_PATH)
connect.row_factory = sqlite3.Row
cursor = connect.cursor()

#Search fts table
cursor.execute(
    """
    SELECT 
        chunk_id, 
        title,
        heading_path,
        text,
        bm25(chunks_fts) AS score
    FROM chunks_fts
    WHERE chunks_fts MATCH ?
    ORDER BY score
    LIMIT 5
    """,
    (query,),
)

rows = cursor.fetchall()
if not rows:
    print ("No results found")
else:
    for index, row in enumerate(rows, start =1):
        print(f"\n Result {index}")
        print(f"Title: {row['title']}")
        print(f"Heading: {row['heading_path']}")
        print(f"Score: {row['score']}")
        print(row["text"])
    

connect.close()
