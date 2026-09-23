"""The delivery packet preserves identifiers, traceability and failure status."""
import csv
import sys
import zipfile

import pytest

from scripts.prepare_submission import (
    PAYLOAD_FILES, build_cases, package, render_cases, stage, verify_archive,
)


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def row(gid, role, priority, depth='1', amount='10000'):
    return dict(gid=gid, role=role, priority_score=priority, depth=depth,
                in_kzt=amount, out_kzt='0', evidence='Поступило 10000 KZT', priority_why='Тест 1',
                is_seed='False', out_observable=str(depth != '4'), external_inflow_suspected='False')


def test_cases_use_exact_gid_data_and_stable_selection(tmp_path):
    a, b, c, d = [str(100000000000000001 + n) for n in range(4)]
    rows = [row(a, 'coordinator', '0.9'), row(c, 'transit', '0.5'),
            row(b, 'transit', '0.5'), row(d, 'peripheral', '0.1', '4', '900000')]
    write_csv(tmp_path / 'nodes_roles.csv', rows)
    write_csv(tmp_path / 'top_nodes.csv', [{'gid': a, 'rank': '1'}])
    cases = build_cases(tmp_path, 'abc')
    assert [c['gid'] for c in cases] == [a, b, d]
    assert cases[2]['facts']['in_kzt'] == '900000'
    assert cases[1]['source']['line'] == 4
    assert 'порядок внутри дня неизвестен' in render_cases(cases, 'abc')
    assert 'не доказывает отсутствие оттока' in render_cases(cases, 'abc')
    write_csv(tmp_path / 'nodes_roles.csv', rows[::-1])
    assert [c['gid'] for c in build_cases(tmp_path, 'abc')] == [a, b, d]


def test_missing_examples_are_explicit(tmp_path):
    write_csv(tmp_path / 'nodes_roles.csv', [row('9007199254740993', 'terminal', '0.7')])
    write_csv(tmp_path / 'top_nodes.csv', [{'gid': '9007199254740993', 'rank': '1'}])
    cases = build_cases(tmp_path, 'abc')
    assert [c['available'] for c in cases] == [True, False, False]
    assert render_cases(cases, 'abc').count('Пример отсутствует') == 2


def packet_fixture(tmp_path):
    packet = tmp_path / 'packet'
    for name in PAYLOAD_FILES:
        path = packet / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture\n', encoding='utf-8')
    (packet / '.env').write_text('DO_NOT_PACKAGE=fixture')
    (packet / 'nodes.parquet').write_text('raw input must stay out')
    report = {'commit': 'abc123', 'status': 'passed', 'input_sha256': {'nodes.parquet': 'input-hash'}}
    return packet, report


def test_archive_allowlist_hashes_and_tamper_detection(tmp_path):
    packet, report = packet_fixture(tmp_path)
    target = tmp_path / 'submission.zip'
    package(packet, target, report)
    result = verify_archive(target)
    assert result['commit'] == 'abc123'
    assert set(result['files']) == set(PAYLOAD_FILES)
    with zipfile.ZipFile(target) as source, zipfile.ZipFile(tmp_path / 'tampered.zip', 'w') as corrupt:
        assert '.env' not in source.namelist() and 'nodes.parquet' not in source.namelist()
        for name in source.namelist():
            corrupt.writestr(name, b'changed' if name == 'top_nodes.csv' else source.read(name))
    with pytest.raises(ValueError, match='Checksum mismatch: top_nodes.csv'):
        verify_archive(tmp_path / 'tampered.zip')


def test_failed_checks_cannot_produce_submission_archive(tmp_path):
    packet, report = packet_fixture(tmp_path)
    report['status'] = 'failed'
    with pytest.raises(ValueError, match='Cannot package failed'):
        package(packet, tmp_path / 'submission.zip', report)
    assert not (tmp_path / 'submission.zip').exists()


def test_nonzero_command_is_recorded_as_failure(tmp_path):
    (tmp_path / 'checks').mkdir()
    result = stage('failure', [sys.executable, '-c', 'print("failure evidence"); raise SystemExit(7)'],
                   tmp_path, tmp_path, {})
    assert result['status'] == 'failed' and result['exit_code'] == 7
    assert 'failure evidence' in (tmp_path / result['log']).read_text()
