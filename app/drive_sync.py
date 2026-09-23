"""One-way audit exchange through an existing rclone mount, not database sync."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from uuid import uuid4
from sqlalchemy import select
from app.database import AuditPackage, AuditResult, CardHead, Revision, connect
from app.schemas import AuditImport
from app.service import KnowledgeService

LOG = logging.getLogger("criteria.drive_sync")
MAX_RESULT_BYTES = 512_000


def contained(root, path):
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("path escapes configured directory")
    return path


def immutable_write(root, path, payload):
    path = contained(root, path)
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path = contained(root, path)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("existing export differs; refusing to overwrite")
        return
    temp = contained(root, path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp'))
    try:
        with temp.open('xb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # Single worker is expected; remote completion still depends on rclone.
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


class DriveSync:
    def __init__(self, sessions, root, host_root=None, auto_prepare=True):
        self.sessions = sessions
        self.service = KnowledgeService(sessions)
        self.root = Path(root).resolve()
        self.host_root = host_root
        self.auto_prepare = auto_prepare
        # Never create the root: missing rclone mount must not silently become local storage.
        for name in ('source_pdf', 'audit_packages', 'audit_results'):
            path = contained(self.root, self.root / name)
            if not path.is_dir():
                raise ValueError(f"required mounted directory missing: {name}")

    def source_file(self, reference):
        reference = reference.replace('\\', '/')
        if self.host_root and reference.startswith(self.host_root.rstrip('/') + '/'):
            reference = reference[len(self.host_root.rstrip('/')) + 1:]
        prefix = reference.split('/', 1)[0]
        if prefix not in ('source_pdf', 'source_web', 'parse_pdf') or '/' not in reference:
            raise ValueError('archive_reference has an unsupported source directory')
        return contained(self.root / prefix, self.root / reference)

    def check_sources(self, package):
        checks = []
        for source in package['sources']:
            check = {'document_version_id': source['id'], 'sha256': source['sha256']}
            try:
                path = self.source_file(source['archive_reference'])
                with path.open('rb') as source_file:
                    if source.get('source_format', 'pdf') == 'pdf' and not source_file.read(5).startswith(b'%PDF-'):
                        raise ValueError('not a PDF header')
                    source_file.seek(0)
                    actual = hashlib.file_digest(source_file, 'sha256').hexdigest()
                check['status'] = 'HASH_MATCH' if actual == source['sha256'] else 'HASH_MISMATCH'
                check['relative_path'] = path.relative_to(self.root).as_posix()
            except (OSError, ValueError):
                check['status'] = 'UNAVAILABLE_OR_INVALID'
            checks.append(check)
        return checks

    def cycle(self):
        counts = {'prepared': 0, 'exported': 0, 'imported': 0, 'duplicates': 0, 'errors': 0}
        if self.auto_prepare:
            with self.sessions() as db:
                has_package = select(AuditPackage.id).where(AuditPackage.card_id == Revision.card_id, AuditPackage.revision == Revision.number).exists()
                candidates = db.scalars(select(Revision).join(CardHead, CardHead.id == Revision.card_id).where(Revision.number == CardHead.latest, ~has_package).limit(100)).all()
            for row in candidates:
                if row.payload['origin'] != 'ai_extracted':
                    continue
                try:
                    self.service.prepare_audit(row.card_id, row.number, 'drive-sync', reuse=True)
                    counts['prepared'] += 1
                except Exception as exc:
                    counts['errors'] += 1
                    LOG.warning('prepare rejected card=%s type=%s', row.card_id, type(exc).__name__)
        with self.sessions() as db:
            packages = db.scalars(select(AuditPackage).order_by(AuditPackage.id)).all()
        for package in packages:
            try:
                p = package.payload
                if not all(re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value) for value in [p['card_id'], package.id]):
                    raise ValueError('invalid path identifier')
                directory = self.root / 'audit_packages' / p['card_id'] / f"revision_{p['revision']}" / package.id
                immutable_write(self.root / 'audit_packages', directory / 'audit_package.json', p)
                # Receipt means local mount write completed, not confirmed remote upload.
                immutable_write(self.root / 'audit_packages', directory / 'READY.json', {
                    'audit_package_id': package.id, 'package_sha256': hashlib.sha256((directory / 'audit_package.json').read_bytes()).hexdigest(),
                    'meaning': 'Local mount write complete. Confirm files are visible in Google Drive before audit.'})
                counts['exported'] += 1
            except Exception as exc:
                counts['errors'] += 1
                LOG.warning('export rejected package=%s type=%s', package.id, type(exc).__name__)
        for candidate in sorted((self.root / 'audit_results').glob('*.json')):
            try:
                path = contained(self.root / 'audit_results', candidate)
                # Bounded read also handles files growing while cloud sync is in progress.
                with path.open('rb') as stream:
                    raw = stream.read(MAX_RESULT_BYTES + 1)
                if len(raw) > MAX_RESULT_BYTES:
                    raise ValueError('result exceeds size limit')
                body = AuditImport.model_validate_json(raw)
                if path.name != body.audit_package_id + '.json':
                    raise ValueError('filename must match audit_package_id')
                with self.sessions() as db:
                    pkg = db.get(AuditPackage, body.audit_package_id)
                    existing = db.get(AuditResult, body.audit_package_id)
                if pkg is None:
                    raise ValueError('unknown package')
                if not existing and body.overall == 'GPT_VERIFIED':
                    checks = self.check_sources(pkg.payload)
                    if not checks or any(c['status'] != 'HASH_MATCH' for c in checks):
                        raise ValueError('verified result requires retained source files with matching hashes')
                saved = self.service.import_audit(body, 'drive-sync')
                counts['duplicates' if saved['duplicate'] else 'imported'] += 1
            except Exception as exc:
                counts['errors'] += 1
                # Do not log source text, model output or credentials from malformed JSON.
                LOG.warning('result rejected file=%s type=%s', candidate.name, type(exc).__name__)
        return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    url = os.environ['DATABASE_URL']
    if not url.startswith('postgresql+psycopg://'):
        raise ValueError('production requires PostgreSQL')
    engine, sessions = connect(url)
    interval = max(15, int(os.getenv('DRIVE_SYNC_INTERVAL', '60')))
    try:
        while True:
            try:
                worker = DriveSync(sessions, os.getenv('DRIVE_ROOT', '/drive'),
                                   host_root=os.getenv('CRITERIA_DRIVE_ROOT'),
                                   auto_prepare=os.getenv('DRIVE_AUTO_PREPARE', 'true').lower() == 'true')
                counts = worker.cycle()
                LOG.info('cycle %s', json.dumps(counts))
                Path('/tmp/drive-sync-heartbeat').write_text(str(time.time()))
                if args.once:
                    return 1 if counts['errors'] else 0
            except Exception as exc:
                LOG.error('cycle failed type=%s', type(exc).__name__)
                if args.once:
                    return 1
            time.sleep(interval)
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
