from pathlib import Path
import hashlib
import sqlite3
import sys
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR/"one_file_rag.sqlite"

if len(sys.argv) != 2:
    raise SystemExit("Usage: python index_one_file.py <html_file>")
html_path = Path(sys.argv[1])

if not html_path.exists():
    raise SystemExit(f"File not found: {html_path}")
                     
if html_path.suffix.lower() not in {".html", ".htm"}:
    raise SystemExit("Expected an HTML file.")

#Hash for IDs
def sha(text: str)-> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() #chunk ids

def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest() #source files

#Read HTML
html_bytes = html_path.read_bytes()
html = html_bytes.decode("utf-8", errors = "replace")
source_hash = sha_bytes(html_bytes)

#Parse
soup = BeautifulSoup(html, "html.parser")
for tag in soup(["script", "style", "template", "nav", "footer", "header"]):
    tag.decompose()
if soup.title:
    title = soup.title.get_text(" ", strip = True)
else:
    title = html_path.stem

#doc_id
#one file run with absolute path
normalized_path = str(html_path.resolve())
doc_id = sha(normalized_path)

#extract chunks with proper categorization
def extract_chunks(soup: BeautifulSoup, title: str) -> list[tuple[str,str,str]]:
    chunks = []

    #process each document (div#oContent)
    for content_div in soup.find_all("div", id="oContent"):
        doc_title = content_div.find_previous("div", class_="header")
        if doc_title:
            doc_title_text = doc_title.get_text(strip=True)
        else:
            doc_title_text = title

        #metadata section (table.KeyValue)
        kv_table = content_div.find("table", class_="KeyValue")
        if kv_table:
            kv_text = extract_table_content(kv_table)
            if kv_text:
                chunks.append(("metadata", doc_title_text, kv_text))

        #sections with SubHeader3
        for subheader in content_div.find_all("table", class_="SubHeader3"):
            section_id = subheader.find("td", id=True)
            section_name = section_id.get("id", "unnamed").strip("_") if section_id else "unnamed"

            #find next sibling that's data (table.List, table.SubList, or span.NA)
            next_elem = subheader.find_next_sibling()

            if next_elem and next_elem.name == "span" and "NA" in next_elem.get("class", []):
                #empty section
                chunks.append(("section_empty", f"{doc_title_text} > {section_name}", "No data"))
            elif next_elem and next_elem.name == "table":
                category = classify_table(next_elem)
                text = extract_table_content(next_elem)
                if text:
                    chunks.append((category, f"{doc_title_text} > {section_name}", text))
            elif next_elem and next_elem.string and next_elem.string.strip() == "None":
                chunks.append(("section_empty", f"{doc_title_text} > {section_name}", "No data"))

    return chunks

def classify_table(table_elem) -> str:
    """Classify table by CSS class."""
    classes = table_elem.get("class", [])
    if "SubList" in classes:
        return "column_info"
    elif "List" in classes:
        return "table_data"
    elif "KeyValue" in classes:
        return "metadata"
    else:
        return "table_generic"

def extract_table_content(table_elem) -> str:
    """Extract and format table content, handling T1Head/T1Value pairs."""
    rows = table_elem.find_all("tr")
    parts = []

    for row in rows:
        cells = row.find_all(["th", "td"])
        row_text = []

        for cell in cells:
            cell_text = cell.get_text(" ", strip=True)
            if not cell_text:
                continue

            #check if this is a T1Head/T1Value pair
            if "T1Head" in cell.get("class", []):
                row_text.append(f"{cell_text}:")
            elif "T1Value" in cell.get("class", []):
                row_text.append(cell_text)
            else:
                row_text.append(cell_text)

        if row_text:
            parts.append(" ".join(row_text))

    return " | ".join(parts) if parts else ""

#create sqlite tables
con = sqlite3.connect(DB_PATH)
cur = con.cursor()
cur.executescript("""
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
    chunk_index INTEGER NOT NULL,
    category TEXT,
    heading_path TEXT,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_id,
    title,
    category,
    heading_path,
    text
);
""")

#doc row in db
cur.execute(
    """
    INSERT INTO docs (doc_id, source_path, title, source_hash)
    VALUES (?, ?, ?, ?)
    """,
    (doc_id, str(html_path), title, source_hash),
)

#chunk row in db
chunks = extract_chunks(soup, title)
for chunk_index, (category, heading_path, text) in enumerate(chunks):
    text_hash = sha(text)
    chunk_id = sha(f"{doc_id}:{chunk_index}:{category}:{heading_path}:{text_hash}")
    cur.execute(
        """
        INSERT INTO chunks(chunk_id, doc_id, chunk_index, category, heading_path, text, text_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (chunk_id, doc_id, chunk_index, category, heading_path, text, text_hash),
    )
    cur.execute(
        """
        INSERT INTO chunks_fts(chunk_id, title, category, heading_path, text)
        VALUES (?, ?, ?, ?, ?)
        """,
        (chunk_id, title, category, heading_path, text),
    )

con.commit()
con.close()
print(f"Indexed 1 document")
print(f"Title: {title}")
print(f"Chunks: {len(chunks)}")
print(f"Database: {DB_PATH}")