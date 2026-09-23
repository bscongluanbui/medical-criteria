"""Discover real open-access papers; never download an AI-invented URL."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from uuid import uuid4
from urllib.parse import urljoin, urlparse, parse_qs
import xml.etree.ElementTree as ET
import httpx

ALLOWED_HOSTS = {'www.ebi.ac.uk', 'pmc-oa-opendata.s3.amazonaws.com'}
MAX_DOWNLOAD = 30 * 1024 * 1024


class SourceError(ValueError):
    pass


def _fetch(url, params=None, limit=MAX_DOWNLOAD):
    """HTTPS only, fixed scientific hosts; revalidate every redirect, no credentials."""
    with httpx.Client(timeout=45, trust_env=False, follow_redirects=False) as client:
        for _ in range(5):
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
                raise SourceError('SOURCE_HOST_NOT_ALLOWED')
            with client.stream('GET', url, params=params, headers={'User-Agent': 'MedicalCriteria/1.0 (literature retrieval)'}) as response:
                params = None
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                if response.status_code != 200:
                    raise SourceError('SOURCE_HTTP_'+str(response.status_code))
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > limit:
                        raise SourceError('SOURCE_TOO_LARGE')
                return bytes(raw)
    raise SourceError('SOURCE_REDIRECT_LIMIT')


def fetch(url, params=None, limit=MAX_DOWNLOAD):
    try:
        return _fetch(url, params=params, limit=limit)
    except httpx.TimeoutException:
        raise SourceError('SOURCE_TIMEOUT') from None
    except httpx.HTTPError:
        raise SourceError('SOURCE_TRANSPORT_ERROR') from None


class Literature:
    def search(self, terms):
        # Words only: the model cannot inject a query that disables OA filtering.
        words = re.findall(r'[A-Za-z0-9-]+', terms)[:16]
        if not words:
            raise SourceError('EMPTY_SEARCH')
        payload = json.loads(fetch('https://www.ebi.ac.uk/europepmc/webservices/rest/search', {
            'query': '('+' '.join(words)+') AND OPEN_ACCESS:Y', 'format': 'json', 'pageSize': 8,
            'resultType': 'core'}, limit=2_000_000))
        if 'resultList' not in payload:
            raise SourceError('SEARCH_PROVIDER_INVALID_RESPONSE')
        items = []
        for row in payload.get('resultList', {}).get('result', []):
            pmcid = row.get('pmcid', '')
            if not re.fullmatch(r'PMC[0-9]+', pmcid) or row.get('isRetracted') == 'Y':
                continue
            items.append({'pmcid': pmcid, 'title': row.get('title', ''),
                          'year': row.get('pubYear', ''), 'doi': row.get('doi'),
                          'authors': row.get('authorString', ''),
                          'abstract': (row.get('abstractText') or '')[:12000]})
        return items

    def retrieve(self, candidate):
        pmcid = candidate['pmcid']
        if not re.fullmatch(r'PMC[0-9]+', pmcid):
            raise SourceError('INVALID_PMCID')
        base = 'https://pmc-oa-opendata.s3.amazonaws.com/'
        xml = fetch(base, {'list-type': '2', 'prefix': pmcid+'.', 'delimiter': '/', 'max-keys': 30}, limit=100000)
        if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
            raise SourceError('XML_ENTITIES_REJECTED')
        prefixes = [node.text for node in ET.fromstring(xml).findall('.//{*}CommonPrefixes/{*}Prefix')]
        records = []
        for prefix in prefixes[:10]:
            if not re.fullmatch(pmcid+r'\.[0-9]+/', prefix or ''):
                continue
            metadata = json.loads(fetch(base+'metadata/'+prefix.rstrip('/')+'.json', limit=100000))
            if metadata.get('pmcid') != pmcid or metadata.get('is_retracted') is not False:
                continue
            if metadata.get('is_pmc_openaccess') is not True or metadata.get('license_code') not in ('CC BY', 'CC BY-SA', 'CC0'):
                continue
            if metadata.get('pdf_url'):
                records.append(metadata)
        # Prefer a published edition over an author manuscript; never equate higher version with latest guideline.
        records.sort(key=lambda r: (r.get('is_manuscript') is not False, -int(r['version'])))
        if not records:
            raise SourceError('NO_RETAINABLE_PDF')
        metadata = records[0]
        url = metadata['pdf_url']
        parsed = urlparse(url)
        if parsed.scheme != 's3' or parsed.netloc != 'pmc-oa-opendata' or not parsed.path.startswith('/'+pmcid+'.'):
            raise SourceError('INVALID_PMC_PDF_LOCATION')
        data = fetch(base+parsed.path.lstrip('/'))
        checksum = parse_qs(parsed.query).get('md5', [''])[0]
        if not re.fullmatch(r'[a-f0-9]{32}', checksum) or hashlib.md5(data, usedforsecurity=False).hexdigest() != checksum:
            raise SourceError('PMC_PDF_CHECKSUM_MISMATCH')
        if not data.startswith(b'%PDF-'):
            raise SourceError('INVALID_PDF')
        return data, metadata['license_code']+'; PMC dataset version '+str(metadata['version'])

    def retrieve_web(self, candidate):
        return retrieve_web_article(candidate)


def pdf_from_archive(data):
    # Stream archive, never extract paths or links to the filesystem.
    with tarfile.open(fileobj=io.BytesIO(data), mode='r|gz') as archive:
        total = 0
        for index, member in enumerate(archive):
            total += member.size
            if index > 500 or total > 100 * 1024 * 1024:
                raise SourceError('ARCHIVE_LIMIT')
            if member.isfile() and member.name.lower().endswith('.pdf'):
                if member.size > MAX_DOWNLOAD:
                    raise SourceError('PDF_TOO_LARGE')
                stream = archive.extractfile(member)
                return stream.read(MAX_DOWNLOAD + 1)
    raise SourceError('ARCHIVE_HAS_NO_PDF')


def parse_pdf(data):
    with tempfile.TemporaryDirectory(prefix='criteria-pdf-') as directory:
        path = Path(directory) / 'source.pdf'
        path.write_bytes(data)
        try:
            output = subprocess.run([sys.executable, '-m', 'app.pdf_text', str(path)],
                                    capture_output=True, timeout=45, check=True)
            return json.loads(output.stdout)
        except (subprocess.SubprocessError, ValueError):
            raise SourceError('PDF_PARSE_OR_OCR_REQUIRED') from None


def retain_pdf(root, data):
    root = Path(root).resolve()
    if not root.is_dir():
        raise SourceError('SOURCE_PDF_MOUNT_MISSING')
    sha = hashlib.sha256(data).hexdigest()
    path = root / (sha + '.pdf')
    if path.is_symlink():
        raise SourceError('SOURCE_SYMLINK_REJECTED')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise SourceError('RETAINED_SOURCE_HASH_MISMATCH')
        return sha
    temp = root / ('.'+sha+'.'+uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return sha


def parse_full_text_xml(data):
    """Return bounded, numbered text segments from a licensed Europe PMC JATS article."""
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise SourceError('XML_ENTITIES_REJECTED')
    try:
        article = ET.fromstring(data)
    except ET.ParseError:
        raise SourceError('WEB_ARTICLE_INVALID_XML') from None
    license_nodes = article.findall('.//{*}article-meta/{*}permissions/{*}license')
    license_text = ' '.join(' '.join(node.itertext()) for node in license_nodes).lower()
    license_urls = ' '.join(str(value).lower() for node in license_nodes for value in node.attrib.values())
    license_label = license_text + ' ' + license_urls
    allowed = ('creativecommons.org/licenses/by/' in license_label or
               'creativecommons.org/licenses/by-sa/' in license_label or
               'creativecommons.org/publicdomain/zero/' in license_label)
    if not allowed:
        raise SourceError('WEB_ARTICLE_LICENSE_NOT_RETAINABLE')
    body = article.find('.//{*}body')
    if body is None:
        raise SourceError('WEB_ARTICLE_NO_BODY')
    paragraphs = []
    for node in body.iter():
        if node.tag.rsplit('}', 1)[-1] != 'p':
            continue
        line = ' '.join(' '.join(node.itertext()).split())
        if len(line) >= 20:
            paragraphs.append(line)
    if not paragraphs:
        raise SourceError('WEB_ARTICLE_NO_EXTRACTABLE_TEXT')
    segments, current = [], ''
    for line in paragraphs:
        if len(current) + len(line) > 5000 and current:
            segments.append(current)
            current = ''
        if len(line) > 5000:
            line = line[:5000]
        current += ('\n' if current else '') + line
    if current:
        segments.append(current)
    if len(segments) > 100 or sum(map(len, segments)) > 300000:
        raise SourceError('WEB_ARTICLE_TOO_LARGE')
    license_name = 'CC BY-SA' if 'creativecommons.org/licenses/by-sa/' in license_label else 'CC BY' if 'creativecommons.org/licenses/by/' in license_label else 'CC0'
    return segments, license_name


def retain_web(root, segments):
    root = Path(root).resolve()
    if not root.is_dir():
        raise SourceError('SOURCE_WEB_MOUNT_MISSING')
    data = ('\n\n'.join(f'[SEGMENT {index}]\n{text}' for index, text in enumerate(segments, 1)) + '\n').encode('utf-8')
    sha = hashlib.sha256(data).hexdigest()
    path = root / (sha + '.txt')
    if path.is_symlink():
        raise SourceError('SOURCE_SYMLINK_REJECTED')
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise SourceError('RETAINED_SOURCE_HASH_MISMATCH')
        return sha
    temp = root / ('.'+sha+'.'+uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return sha


def retrieve_web_article(candidate):
    pmcid = candidate['pmcid']
    if not re.fullmatch(r'PMC[0-9]+', pmcid):
        raise SourceError('INVALID_PMCID')
    data = fetch(f'https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML', limit=2_000_000)
    return parse_full_text_xml(data)
