"""Incremental inventory of MinerU output; never scans or parses the original PDFs."""
import argparse
import hashlib
import logging
import os
from pathlib import Path
import re
import time
import unicodedata
from sqlalchemy import case, or_, select
from app.database import LibraryFile, connect
from app.literature import MAX_DOWNLOAD, SourceError

LOG = logging.getLogger('criteria.library')


def words(value):
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return [w for w in re.findall(r'[a-z0-9]+', value) if len(w) >= 3]


def parse_mineru(data, suffix):
    """Extract exact text from MinerU Markdown or content_list JSON into bounded segments."""
    import json
    try:
        if suffix == '.json':
            items = json.loads(data)
            if not isinstance(items, list):
                raise ValueError('content_list must be an array')
            lines = [item['text'] for item in items if isinstance(item, dict) and
                     isinstance(item.get('text'), str) and item.get('type') in ('text', 'title', 'table')]
        else:
            lines = data.decode('utf-8-sig').splitlines()
    except (UnicodeError, ValueError):
        raise SourceError('MINERU_OUTPUT_INVALID') from None
    segments, current = [], ''
    for raw in lines:
        line = ' '.join(raw.split())
        if len(line) < 3:
            continue
        if len(line) > 5000:
            line = line[:5000]
        if len(current) + len(line) > 5000 and current:
            segments.append(current)
            current = ''
        current += ('\n' if current else '') + line
    if current:
        segments.append(current)
    if not segments or len(segments) > 100 or sum(map(len, segments)) > 300000:
        raise SourceError('MINERU_OUTPUT_INSUFFICIENT_OR_TOO_LARGE')
    return segments


class Library:
    def __init__(self, sessions, root):
        self.sessions, self.root = sessions, Path(root).resolve()

    def scan(self, max_files=200000):
        if not self.root.is_dir():
            raise SourceError('LIBRARY_MOUNT_MISSING')
        seen, changed = 0, 0
        def walk_error(exc):
            raise exc
        for directory, dirs, files in os.walk(self.root, followlinks=False, onerror=walk_error):
            dirs[:] = [d for d in dirs if d != 'criteria_sources' and not (Path(directory) / d).is_symlink()]
            for name in files:
                if not (name.lower().endswith(('.md', '.markdown')) or name.lower().endswith('_content_list.json')):
                    continue
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                seen += 1
                if seen > max_files:
                    raise SourceError('LIBRARY_INVENTORY_LIMIT')
                try:
                    stat = path.stat()
                except OSError:
                    continue
                relative = path.relative_to(self.root).as_posix()
                key = hashlib.sha256(relative.encode()).hexdigest()
                with self.sessions.begin() as db:
                    row = db.get(LibraryFile, key)
                    if row and row.size == stat.st_size and row.mtime_ns == stat.st_mtime_ns:
                        continue
                    if row is None:
                        row = LibraryFile(id=key, relative_path=relative)
                        db.add(row)
                    try:
                        with path.open('rb') as input_file:
                            sample = input_file.read(16000).decode('utf-8', errors='ignore')
                    except OSError:
                        sample = ''
                    row.search_text = ' '.join(words(relative + ' ' + sample))[:3000]
                    row.size, row.mtime_ns = stat.st_size, stat.st_mtime_ns
                    changed += 1
        # Never delete indexed paths on a transient rclone outage.
        return {'seen': seen, 'changed': changed}

    def search(self, query, limit=8):
        tokens = set(words(query))
        if not tokens:
            return []
        focus = tokens - {'diagnostic', 'criteria', 'guideline', 'imaging', 'measurement', 'classification'}
        keys = sorted(focus or tokens)[:8]
        rank = sum((case((LibraryFile.search_text.contains(token), 1), else_=0) for token in keys))
        with self.sessions() as db:
            rows = db.scalars(select(LibraryFile).where(or_(
                *[LibraryFile.search_text.contains(token) for token in keys]
            )).order_by(rank.desc()).limit(300)).all()
            results = []
            for row in rows:
                overlap = (focus or tokens) & set(row.search_text.split())
                score = len(overlap) / len(focus or tokens)
                if score < 0.5 or row.size > MAX_DOWNLOAD:
                    continue
                results.append({'id': row.id, 'relative_path': row.relative_path,
                                'title': Path(row.relative_path).stem, 'size': row.size,
                                'score': round(score, 3)})
            return sorted(results, key=lambda r: (-r['score'], r['relative_path']))[:limit]

    def retrieve(self, candidate):
        with self.sessions() as db:
            row = db.get(LibraryFile, candidate['id'])
            if row is None or row.relative_path != candidate['relative_path']:
                raise SourceError('LIBRARY_CANDIDATE_CHANGED')
            relative, size, mtime_ns = row.relative_path, row.size, row.mtime_ns
        raw_path = self.root / relative
        if raw_path.is_symlink():
            raise SourceError('LIBRARY_PATH_INVALID')
        path = raw_path.resolve()
        if not path.is_relative_to(self.root):
            raise SourceError('LIBRARY_PATH_INVALID')
        stat = path.stat()
        if stat.st_size != size or stat.st_mtime_ns != mtime_ns or size > MAX_DOWNLOAD:
            raise SourceError('LIBRARY_FILE_CHANGED')
        data = path.read_bytes()
        if len(data) != size:
            raise SourceError('LIBRARY_FILE_CHANGED')
        segments = parse_mineru(data, path.suffix.lower())
        return segments, hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    url = os.environ['DATABASE_URL']
    if not url.startswith('postgresql+psycopg://'):
        raise ValueError('production requires PostgreSQL')
    engine, sessions = connect(url)
    library = Library(sessions, os.getenv('PARSED_LIBRARY_ROOT', '/parse_pdf'))
    interval = max(60, int(os.getenv('LIBRARY_SCAN_INTERVAL', '900')))
    try:
        while True:
            try:
                LOG.info('inventory %s', library.scan())
                Path('/tmp/library-heartbeat').write_text(str(time.time()))
            except (OSError, SourceError) as exc:
                LOG.warning('inventory failed: %s', exc)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(interval)
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
