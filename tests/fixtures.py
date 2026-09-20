"""Synthetic software fixtures, not diagnostic criteria or clinical evidence."""
def source():
    return {"id": "fixture-v1", "source_id": "fixture", "title": "SYNTHETIC SOFTWARE TEST", "organization": "TEST", "version": "1", "official_url": "https://example.org/test", "sha256": "a" * 64, "pdf_pages": 2, "license_note": "synthetic test", "archive_reference": "test-fixture-only", "retention": "retained_source"}


def card():
    return {
        "topic_id": "synthetic", "name_vi": "Đo thử nghiệm", "name_en": "Synthetic measurement",
        "aliases": ["TEST"], "type": "measurement", "guideline_family": "SYNTHETIC",
        "guideline_module": "software-test", "guideline_version": "1", "modality": "TEST", "origin": "manual",
        "applicability": {"population": "software fixtures only", "clinical_context": "not clinical",
            "intended_use": "software testing", "prerequisites": [], "exclusions": ["all patients"],
            "required_inputs": ["synthetic value"], "discordance_policy": "manual review", "limitations": ["not medical data"]},
        "measurement": {"modality": "TEST", "protocol": "synthetic", "plane": "synthetic", "landmarks": "synthetic",
            "caliper_or_roi": "synthetic", "quantity": "length", "unit": "mm", "quality_requirements": "synthetic", "pitfalls": ["test only"]},
        "claims": [{"id": "c1", "text_vi": "Dữ liệu giả lập để kiểm thử phần mềm.", "evidence_ids": ["e1"],
            "threshold": {"parameter": "synthetic_length", "operator": ">=", "value": "5.0", "unit": "mm"}}],
        "logic": {"kind": "reference_only", "claim_ids": ["c1"], "description": "not a calculator"},
        "evidence": [{"id": "e1", "document_version_id": "fixture-v1", "document_sha256": "a" * 64,
            "pdf_page": 1, "section": "test", "quote": "SYNTHETIC length >= 5.0 mm", "parser_version": "manual-test"}]
    }
