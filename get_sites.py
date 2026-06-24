import requests
import pdfkit
import os
import tempfile
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from PyPDF2 import PdfMerger


def get_urls_from_navbar(base_site):
    response = requests.get(base_site)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    menu_nav = soup.find('nav', class_='menu-body')
    if not menu_nav:
        # fallback: try to find by id
        menu_nav = soup.find('nav', id='main-navigation')
    if not menu_nav:
        raise Exception('Could not find navbar/menu in the HTML')

    base_host = urlparse(base_site).netloc
    urls = []
    seen = set()

    def add(href):
        if not href or href.startswith('javascript'):
            return
        full = urljoin(base_site, href)
        # only crawl pages on the same host (skip external links like the PDF link)
        if urlparse(full).netloc != base_host:
            return
        if full not in seen:
            seen.add(full)
            urls.append(full)

    # 1st level links
    for a in menu_nav.find_all('a', class_='menu-item divider', href=True):
        add(a['href'])
    # 2nd level links (inside menu-folder > ul > li > a)
    for folder in menu_nav.find_all('div', class_='menu-folder'):
        ul = folder.find('ul')
        if ul:
            for li in ul.find_all('li'):
                a = li.find('a', class_='menu-item', href=True)
                if a:
                    add(a['href'])
    return urls


def download_as_pdf(url, output_path):
    try:
        # Create temporary CSS to hide header, navbar, and footer elements
        css = """
        footer, .footer, #footer, [class*='footer'], [id*='footer'],
        header, .header, #header, [class*='header'], [id*='header'],
        nav, .nav, #nav, [class*='nav'], [id*='nav'] {
            display: none !important;
        }
        """

        with tempfile.NamedTemporaryFile(suffix='.css', mode='w', delete=False) as f:
            f.write(css)
            css_path = f.name

        options = {
            'user-style-sheet': css_path
        }

        # Use wkhtmltopdf config if needed
        path_wkhtmltopdf_win = r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe'
        path_wkhtmltopdf_linux = '/usr/bin/wkhtmltopdf'
        config = None
        if os.path.exists(path_wkhtmltopdf_win):
            config = pdfkit.configuration(wkhtmltopdf=path_wkhtmltopdf_win)
        elif os.path.exists(path_wkhtmltopdf_linux):
            config = pdfkit.configuration(wkhtmltopdf=path_wkhtmltopdf_linux)

        pdfkit.from_url(url, output_path, options=options, configuration=config)

        os.remove(css_path)
        return True
    except Exception as e:
        print(f"Error converting {url} to PDF: {e}")
        return False


def merge_pdfs(pdf_files, output_path):
    merger = PdfMerger()
    for pdf in pdf_files:
        if os.path.exists(pdf):
            merger.append(pdf)
    merger.write(output_path)
    merger.close()


def upload_to_gcs(bucket_name, source_file, destination_blob, credentials_path):
    """Optional GCS upload. Skipped if credentials are missing/invalid."""
    if not credentials_path or not os.path.exists(credentials_path) or os.path.getsize(credentials_path) == 0:
        print("GCS credentials not provided; skipping GCS upload.")
        return False
    try:
        from google.cloud import storage
        client = storage.Client.from_service_account_json(credentials_path)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(destination_blob)
        blob.upload_from_filename(source_file)
        print(f"Uploaded {source_file} to gs://{bucket_name}/{destination_blob}")
        return True
    except Exception as e:
        print(f"GCS upload skipped/failed: {e}")
        return False


if __name__ == "__main__":
    # URL do site publicado (sobrescreva via variavel de ambiente SITE_BASE_URL).
    base_site = os.environ.get(
        "SITE_BASE_URL",
        "https://alesmelo74.github.io/guiaobservatorio/",
    )
    if not base_site.endswith('/'):
        base_site += '/'

    urls = get_urls_from_navbar(base_site)

    temp_dir = "temp_pdfs"
    os.makedirs(temp_dir, exist_ok=True)

    pdf_files = []
    print(f"Base site: {base_site}")
    print(f"Found {len(urls)} URLs to process (from navbar order)")

    for i, url in enumerate(urls):
        print(f"Processing {i+1}/{len(urls)}: {url}")
        output_pdf = os.path.join(temp_dir, f"page_{i}.pdf")
        if download_as_pdf(url, output_pdf):
            pdf_files.append(output_pdf)

    if pdf_files:
        # Grava em temp_pdfs (volume montado) para que o workflow consiga publicar.
        output_file = os.path.join(temp_dir, "merged_site.pdf")
        print(f"Merging {len(pdf_files)} PDFs into {output_file}")
        merge_pdfs(pdf_files, output_file)
        print(f"Successfully created {output_file}")

        # Upload opcional para GCS (so ocorre se as credenciais existirem).
        bucket_name = os.environ.get("GCS_BUCKET", "wwwdocpbi")
        credentials_path = "gcs-bucket.json"
        destination_blob = "merged_site.pdf"
        upload_to_gcs(bucket_name, output_file, destination_blob, credentials_path)
    else:
        print("No PDFs were created.")
        raise SystemExit(1)
