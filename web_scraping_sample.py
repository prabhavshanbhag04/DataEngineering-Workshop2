import time
from datetime import datetime
from io import BytesIO
from urllib.parse import urljoin, urlparse

import psycopg2
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


URL = "https://sample-files.com/documents/pdf/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

DB_CONFIG = {
    "host": "psql-db",
    "port": 5432,
    "database": "scraping_db",
    "user": "postgres",
    "password": "123456"
}


def create_http_session():
    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False
    )

    adapter = HTTPAdapter(max_retries=retry)

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update(HEADERS)

    return session


def connect_to_database():
    for attempt in range(10):
        try:
            connection = psycopg2.connect(**DB_CONFIG)
            print("Connected to PostgreSQL")
            return connection

        except psycopg2.OperationalError:
            print(
                f"PostgreSQL not ready. "
                f"Retry {attempt + 1}/10..."
            )
            time.sleep(2)

    raise Exception("Could not connect to PostgreSQL")


def create_table(connection):
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdf_documents (
            id SERIAL PRIMARY KEY,
            file_name VARCHAR(255) NOT NULL,
            pdf_url TEXT UNIQUE NOT NULL,
            page_count INTEGER,
            content TEXT,
            scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    cursor.close()

    print("pdf_documents table is ready")


def get_pdf_links(session):
    print("Requesting webpage...")

    response = session.get(
        URL,
        timeout=(15, 30)
    )

    response.raise_for_status()

    print("Website status:", response.status_code)

    soup = BeautifulSoup(
        response.content,
        "html.parser"
    )

    pdf_links = []

    for link in soup.find_all("a", href=True):

        href = link["href"]

        if href.lower().endswith(".pdf"):

            full_url = urljoin(URL, href)

            if full_url not in pdf_links:
                pdf_links.append(full_url)

    return pdf_links


def download_pdf_and_extract_text(session, pdf_url):

    for attempt in range(3):

        try:
            print(f"Downloading: {pdf_url}")
            print(f"Attempt {attempt + 1}/3")

            pdf_response = session.get(
                pdf_url,
                timeout=(15, 30)
            )

            pdf_response.raise_for_status()

            reader = PdfReader(
                BytesIO(pdf_response.content)
            )

            page_count = len(reader.pages)

            text_parts = []

            for page in reader.pages:

                text = page.extract_text()

                if text:
                    text_parts.append(text)

            full_text = "\n".join(text_parts)

            return page_count, full_text

        except requests.RequestException as error:

            print("Download failed:", error)

            if attempt < 2:
                time.sleep(3)

    return None, None


def save_to_postgres(
    connection,
    pdf_url,
    page_count,
    content
):
    cursor = connection.cursor()

    file_name = urlparse(pdf_url).path.split("/")[-1]

    cursor.execute("""
        INSERT INTO pdf_documents
            (
                file_name,
                pdf_url,
                page_count,
                content,
                scraped_at
            )
        VALUES
            (%s, %s, %s, %s, %s)
        ON CONFLICT (pdf_url)
        DO UPDATE SET
            page_count = EXCLUDED.page_count,
            content = EXCLUDED.content,
            scraped_at = EXCLUDED.scraped_at
    """, (
        file_name,
        pdf_url,
        page_count,
        content,
        datetime.now()
    ))

    connection.commit()
    cursor.close()

    print("Saved to PostgreSQL:", file_name)


def main():

    session = create_http_session()

    connection = connect_to_database()

    create_table(connection)

    try:

        pdf_links = get_pdf_links(session)

    except requests.RequestException as error:

        print("Could not retrieve webpage:")
        print(error)

        connection.close()
        return

    print(
        "Total unique PDF links found:",
        len(pdf_links)
    )

    for index, pdf_url in enumerate(
        pdf_links,
        start=1
    ):

        print("\n" + "=" * 60)
        print(
            f"PDF {index}/{len(pdf_links)}"
        )
        print(pdf_url)

        page_count, content = (
            download_pdf_and_extract_text(
                session,
                pdf_url
            )
        )

        if content is None:

            print(
                "Skipping PDF because "
                "download failed."
            )

            continue

        print("Pages:", page_count)
        print(
            "Characters extracted:",
            len(content)
        )

        save_to_postgres(
            connection,
            pdf_url,
            page_count,
            content
        )

    connection.close()

    print("\nScraping completed.")


if __name__ == "__main__":
    main()

