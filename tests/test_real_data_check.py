"""The check that keeps real data out of the public repository: each kind of finding is caught, a fictional value passes, and a finding never shows what it found."""
from fake_payer.real_data_check import Finding, main, scan_repo, scan_text


def kinds(text):
    return [f.kind for f in scan_text(text)]


def test_a_real_insurers_name_is_found():
    assert kinds("Call Delta Dental about the claim") == ["insurer"]
    assert kinds("sent to metlife") == ["insurer"]


def test_an_ordinary_word_is_not_an_insurer():
    assert kinds("the principal investigator guarded the anthems") == []


def test_a_phone_number_outside_the_fictional_range_is_found():
    for text in ("call (617) 555-0223", "call 617-555-1234", "call 617.867.5309", "dial +16175551234", "dial 1 617 867 5309"):
        assert "phone" in kinds(text), text


def test_fictional_phone_numbers_pass():
    for text in ("call (617) 555-0100", "call 202-555-0199", "dial +1 415 555 0142"):
        assert "phone" not in kinds(text), text


def test_ordinary_numbers_are_not_phone_numbers():
    assert kinds("version 3.11.4, id 20261112, 1234567 and 2026-11-12") == []


def test_an_internal_host_name_is_found():
    assert kinds("deploy to builder-vm") == ["internal-name"]


def test_credential_shaped_values_are_found():
    cases = ["sk-" + "ant-" + "a" * 30, "AKIA" + "A" * 16, "-----BEGIN " + "RSA PRIVATE KEY-----", 'api_key = "' + "a1B2" * 8 + '"']
    for text in cases:
        assert "credential" in kinds(text), text


def test_an_example_configuration_value_is_not_a_credential():
    assert kinds("SONIOX_API_KEY=example-soniox-key") == []


def test_a_finding_reports_where_and_what_kind_but_never_the_text():
    secret = "sk-" + "ant-" + "z" * 30
    [finding] = scan_text("line one\n" + secret + "\n", "notes.md")
    assert (finding.path, finding.line, finding.kind) == ("notes.md", 2, "credential")
    assert secret not in str(finding) and secret not in repr(finding)


def test_the_repository_itself_is_clean(capsys):
    assert scan_repo() == []
    assert main() == 0
