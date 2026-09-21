import hashlib
import json
from pathlib import Path
import pytest
from app.database import Base, connect
from app.drive_sync import DriveSync, contained, immutable_write
from app.schemas import Card, SourceVersion
from app.service import KnowledgeService
from tests.fixtures import card, source
from tests.test_verification import result


@pytest.fixture
def setup(tmp_path):
    engine, sessions = connect(f"sqlite:///{tmp_path / 'drive.db'}")
    Base.metadata.create_all(engine)
    root = tmp_path / 'drive'
    for name in ('source_pdf', 'audit_packages', 'audit_results'):
        (root / name).mkdir(parents=True)
    pdf = b'%PDF-1.7\nSYNTHETIC SOFTWARE FIXTURE ONLY\n%%EOF'
    (root / 'source_pdf' / 'fixture.pdf').write_bytes(pdf)
    sha = hashlib.sha256(pdf).hexdigest()
    s = source()
    s.update(sha256=sha, archive_reference='source_pdf/fixture.pdf')
    c = card()
    c['origin'] = 'ai_extracted'
    c['evidence'][0]['document_sha256'] = sha
    service = KnowledgeService(sessions)
    service.register_source(SourceVersion.model_validate(s))
    service.add_revision('test', 0, Card.model_validate(c), 'fixture')
    yield DriveSync(sessions, root), service, root
    engine.dispose()


def exported(root):
    files = list((root / 'audit_packages').glob('test/revision_1/*/audit_package.json'))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding='utf-8'))


def save_result(root, pkg):
    payload = result(pkg)
    target = root / 'audit_results' / (pkg['audit_package_id'] + '.json')
    target.write_text(json.dumps(payload), encoding='utf-8')
    return target


def test_round_trip_auto_prepare_hash_checked_idempotent(setup):
    worker, service, root = setup
    counts = worker.cycle()
    assert counts == {'prepared': 1, 'exported': 1, 'imported': 0, 'duplicates': 0, 'errors': 0}
    pkg = exported(root)
    assert list((root / 'audit_packages').rglob('READY.json'))
    save_result(root, pkg)
    assert worker.cycle()['imported'] == 1
    assert worker.cycle()['duplicates'] == 1
    assert worker.cycle()['prepared'] == 0
    with service.sessions() as db:
        assert service.verification(db, 'test', 1)['chatgpt'] == 'GPT_VERIFIED'


def test_result_waits_for_matching_pdf(setup):
    worker, service, root = setup
    worker.cycle()
    save_result(root, exported(root))
    path = root / 'source_pdf' / 'fixture.pdf'
    original = path.read_bytes()
    path.write_bytes(b'%PDF-wrong source')
    assert worker.cycle()['errors'] == 1
    with service.sessions() as db:
        assert service.verification(db, 'test', 1)['chatgpt'] == 'GPT_UNVERIFIED'
    path.write_bytes(original)
    assert worker.cycle()['imported'] == 1


def test_malformed_partial_and_wrong_filename_retry(setup):
    worker, service, root = setup
    worker.cycle()
    pkg = exported(root)
    path = save_result(root, pkg)
    complete = path.read_bytes()
    path.write_bytes(b'{"audit_package_id":')
    assert worker.cycle()['errors'] == 1
    path.write_bytes(complete)
    other = path.with_name('wrong-name.json')
    path.rename(other)
    assert worker.cycle()['errors'] == 1
    other.rename(path)
    assert worker.cycle()['imported'] == 1


def test_modified_export_not_overwritten(setup):
    worker, service, root = setup
    worker.cycle()
    path = next((root / 'audit_packages').rglob('audit_package.json'))
    path.write_text('tampered')
    assert worker.cycle()['errors'] == 1
    assert path.read_text() == 'tampered'


def test_missing_mount_and_path_traversal(setup, tmp_path):
    worker, service, root = setup
    with pytest.raises(ValueError):
        DriveSync(service.sessions, tmp_path / 'missing')
    with pytest.raises(ValueError):
        worker.source_file('source_pdf/../../secret.pdf')
    with pytest.raises(ValueError):
        worker.source_file('/etc/passwd')
    with pytest.raises(ValueError):
        immutable_write(root, root / '../escape.json', {})


def test_host_archive_reference_mapping(setup):
    worker, service, root = setup
    worker.host_root = '/home/ubuntu/rclone/papers/criteria_sources'
    assert worker.source_file(worker.host_root + '/source_pdf/fixture.pdf') == root / 'source_pdf/fixture.pdf'


def test_auto_prepare_off_and_historical_result(setup):
    worker, service, root = setup
    worker.auto_prepare = False
    assert worker.cycle()['prepared'] == 0
    pkg = service.prepare_audit('test', 1, 'fixture')
    service.add_revision('test', 1, Card.model_validate(pkg['card']), 'fixture')
    worker.cycle()
    save_result(root, pkg)
    assert worker.cycle()['imported'] == 1
    with service.sessions() as db:
        assert service.verification(db, 'test', 1)['chatgpt'] == 'GPT_VERIFIED'
        assert service.verification(db, 'test', 2)['chatgpt'] == 'GPT_UNVERIFIED'


def test_oversize_result_rejected(setup):
    worker, service, root = setup
    (root / 'audit_results' / 'huge.json').write_bytes(b' ' * 512001)
    assert worker.cycle()['errors'] == 1
