#!/usr/bin/env python3
"""Build a local, commit-bound submission packet using existing project checks."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import html
import io
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

CSV_FILES = ('nodes_roles.csv', 'clusters.csv', 'top_nodes.csv', 'edge_table.csv')
INPUT_FILES = ('nodes.parquet', 'edges.parquet', 'transactions.parquet')
CHECK_NAMES = ('python-tests', 'pipeline-repro', 'pipeline-contract', 'copilot-evaluation',
               'three-demo-cases', 'frontend-install', 'frontend-tests', 'frontend-build')
PAYLOAD_FILES = (*CSV_FILES, 'run_report.md', 'run_report.json', 'acceptance_report.md',
                 'acceptance_report.json', 'demo_cases.md', 'demo_cases.json', 'START_HERE.md',
                 'checks/evaluation.md', 'checks/evaluation.json',
                 *(f'checks/{name}.log' for name in ('python-tests', 'pipeline-repro',
                   'copilot-evaluation', 'frontend-install', 'frontend-tests', 'frontend-build')))
UNVERIFIED = [
    'Docker/browser smoke на этом commit не запускался этим скриптом.',
    'Живой NVIDIA API и работа без сети после установки зависимостей не проверялись.',
    'Пятиминутное выступление и отправка организаторам остаются ручными шагами.',
    'Более поздние коммиты и незамерженные PR не входят в этот отчёт.',
]


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))  # GIDs remain exact strings.


def snapshot(repo: Path, commit: str, destination: Path) -> None:
    archive = subprocess.check_output(['git', 'archive', '--format=tar', commit], cwd=repo)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            target = destination / member.name
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError('Archive path escapes the snapshot')
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open('wb') as out:
                    shutil.copyfileobj(source, out)
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError(f'Unsupported archive entry: {member.name}')


def stage(name: str, command: list[str], cwd: Path, packet: Path, env: dict,
          timeout: int = 600) -> dict:
    log = packet / 'checks' / f'{name}.log'
    record = {'name': name, 'command': shlex.join(command), 'status': 'failed',
              'log': f'checks/{name}.log'}
    started = time.monotonic()
    print(f'[{name}] {shlex.join(command)}', flush=True)
    with log.open('w', encoding='utf-8') as output:
        try:
            proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
                                    stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
            try:
                record['exit_code'] = proc.wait(timeout=timeout)
                record['status'] = 'passed' if record['exit_code'] == 0 else 'failed'
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                record['reason'] = f'Timeout after {timeout}s'
        except OSError as exc:
            record['reason'] = str(exc)
    record['seconds'] = round(time.monotonic() - started, 2)
    print(f'[{name}] {record["status"]} ({record["seconds"]}s)', flush=True)
    return record


def build_cases(out: Path, commit: str) -> list[dict]:
    rows = read_csv(out / 'nodes_roles.csv')
    top = read_csv(out / 'top_nodes.csv')
    by_gid = {r['gid']: (line, r) for line, r in enumerate(rows, 2)}
    ranked = sorted(rows, key=lambda r: (-Decimal(r['priority_score']), int(r['gid'])))
    first = min(top, key=lambda r: int(r['rank']))['gid'] if top else None
    transit = next((r['gid'] for r in ranked if r['role'] == 'transit' and r['gid'] != first), None)
    boundary = next((r['gid'] for r in sorted(rows, key=lambda r: (-Decimal(r['in_kzt']), int(r['gid'])))
                     if int(r['depth']) == 4 and r['gid'] not in {first, transit}), None)
    specs = [
        ('priority', 'Кого проверить первым и почему', first,
         'Почему этот узел в топе?', 'Первый rank из top_nodes.csv.'),
        ('transit', 'Гипотеза транзита и пределы временной метрики', transit,
         'Какие факты поддерживают роль transit?', 'Максимальный priority_score среди оставшихся transit; равенства по gid.'),
        ('depth4', 'Почему отсутствие выхода не доказывает оседание', boundary,
         'Можно ли считать этот узел конечным получателем?', 'Наибольший видимый вход среди оставшихся узлов depth=4; равенства по gid.'),
    ]
    cases = []
    for key, title, gid, question, selection in specs:
        case = dict(id=key, title=title, gid=gid, question=question, selection=selection, commit=commit)
        if gid is None:
            cases.append({**case, 'available': False, 'reason': 'Подходящий отдельный узел отсутствует в этой выгрузке.'})
            continue
        line, row = by_gid[gid]
        fields = ['role', 'role_rule', 'role_score', 'priority_score', 'cluster_id', 'depth', 'is_seed',
                  'in_kzt', 'out_kzt', 'in_tx', 'out_tx', 'n_payers', 'n_receivers',
                  'pass_through_reliable', 'fast_out_share', 'out_observable', 'evidence', 'priority_why']
        facts = {name: row[name] for name in fields if name in row}
        limits = ['Роль — гипотеза для проверки; score задаёт очередь и не является вероятностью виновности.',
                  'Видны только переводы внутри выборки; факты не описывают все операции клиента.']
        if row.get('is_seed', '').lower() == 'true':
            limits.append('Это seed: вход извне выборки не наблюдается.')
        if row.get('external_inflow_suspected', '').lower() == 'true':
            limits.append('Отток превышает видимый вход: вероятен источник вне выборки.')
        if key == 'transit':
            limits.append('fast_out_share сопоставляет даты, а не происхождение средств; порядок внутри дня неизвестен.')
        if int(row['depth']) == 4:
            limits.append('Исходящие depth=4 не выгружались: нулевой out_kzt не доказывает отсутствие оттока или роль terminal.')
        next_step = ('Запросить исходящие переводы за границей обхода.' if key == 'depth4' else
                     'Запросить полную выписку с внешними поступлениями и временем операций; проверить альтернативные объяснения.')
        cases.append({**case, 'available': True, 'source': {'file': 'nodes_roles.csv', 'line': line, 'gid': gid},
                      'facts': facts, 'limits': limits, 'next_step': next_step})
    return cases


def md(value) -> str:
    return html.escape(str(value), quote=False).replace('|', '\\|').replace('\n', ' ')


def render_cases(cases: list[dict], commit: str) -> str:
    lines = ['# Три кейса для защиты', '', f'Проверенная версия: `{commit}`.', '',
             'Сценарий на 5 минут: 30 с ввод → по 60 с на три кейса → 60 с Copilot/источники → 30 с ограничения.',
             'Это план выступления; фактический хронометраж нужно проверить вручную.', '']
    for case in cases:
        lines += [f'## {case["title"]}', '']
        if not case['available']:
            lines += [f'**Пример отсутствует:** {case["reason"]}', '']
            continue
        lines += [f'GID: **`{case["gid"]}`**. Вставьте его в поиск интерфейса.', '',
                  f'Выбор: {case["selection"]}', '', f'Вопрос: {case["question"]}', '',
                  f'Источник: [nodes_roles.csv](nodes_roles.csv), строка {case["source"]["line"]}, точный gid выше.', '',
                  '| Поле CSV | Значение |', '|---|---|']
        lines += [f'| `{name}` | {md(value) if value else "не определено (пустая ячейка CSV)"} |'
                  for name, value in case['facts'].items()]
        lines += ['', 'Оговорки:', ''] + [f'- {limit}' for limit in case['limits']]
        lines += ['', f'Следующий шаг: {case["next_step"]}', '']
    return '\n'.join(lines)


def render_report(report: dict) -> str:
    lines = ['# Отчёт готовности пакета', '', f'**{report["status"].upper()}**', '',
             f'Commit: `{report["commit"]}`. Создан: {report["generated_at"]}.', '',
             'Проверен изолированный снимок commit с отдельной копией входных Parquet.', '',
             '| Проверка | Итог | Секунды | Лог |', '|---|---|---:|---|']
    for check in report['checks']:
        log = f'[{check["log"]}]({check["log"]})' if check.get('log') else md(check.get('reason', ''))
        lines.append(f'| {check["name"]} | {check["status"]} | {check.get("seconds", "—")} | {log} |')
    lines += ['', '## Проверенные команды', '']
    lines += [f'- Из `{check.get("directory", ".")}`: `{check["command"]}`'
              for check in report['checks'] if check.get('command')]
    lines += ['', '## Окружение', '']
    lines += [f'- {name}: {md(value)}' for name, value in report['environment'].items()]
    lines += ['', '## Границы проверки', ''] + [f'- {note}' for note in report['unverified']]
    lines += ['', '## Входной набор (SHA-256)', '']
    lines += [f'- `{name}`: `{sha}`' for name, sha in report['input_sha256'].items()]
    if report.get('error'):
        lines += ['', f'Ошибка подготовки: {md(report["error"])}']
    lines += ['', 'Успешные автоматические проверки не заменяют ручную приёмку PAN-49.', '']
    return '\n'.join(lines)


def package(packet: Path, target: Path, report: dict) -> None:
    if report['status'] != 'passed':
        raise ValueError('Cannot package failed checks')
    files = {name: {'sha256': digest(packet / name), 'bytes': (packet / name).stat().st_size}
             for name in sorted(PAYLOAD_FILES)}
    manifest = {'schema_version': 1, 'commit': report['commit'], 'status': report['status'],
                'input_sha256': report['input_sha256'], 'files': files}
    write_json(packet / 'manifest.json', manifest)
    checksums = {name: record['sha256'] for name, record in files.items()}
    checksums['manifest.json'] = digest(packet / 'manifest.json')
    (packet / 'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name, sha in sorted(checksums.items())), encoding='utf-8')
    temporary = target.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted((*PAYLOAD_FILES, 'manifest.json', 'SHA256SUMS')):
            archive.write(packet / name, name)
    verify_archive(temporary)
    temporary.replace(target)


def verify_archive(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        expected = set(manifest['files']) | {'manifest.json', 'SHA256SUMS'}
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError('Archive file list differs from manifest')
        actual = {}
        for name, record in manifest['files'].items():
            raw = archive.read(name)
            sha = hashlib.sha256(raw).hexdigest()
            if sha != record['sha256'] or len(raw) != record['bytes']:
                raise ValueError(f'Checksum mismatch: {name}')
            actual[name] = sha
        actual['manifest.json'] = hashlib.sha256(archive.read('manifest.json')).hexdigest()
        lines = archive.read('SHA256SUMS').decode().splitlines()
        sums = {name: sha for sha, name in (line.split('  ', 1) for line in lines)}
        if sums != actual or len(lines) != len(actual):
            raise ValueError('SHA256SUMS differs from archive')
        return manifest


def build(args) -> int:
    repo = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
    commit = subprocess.check_output(['git', 'rev-parse', '--verify', '--end-of-options', f'{args.ref}^{{commit}}'],
                                     cwd=repo, text=True).strip()
    dest, data = Path(args.dest).resolve(), Path(args.data).resolve()
    if dest.is_relative_to(repo) or dest == data or dest.is_relative_to(data):
        raise ValueError('--dest must be outside the repository and input directory')
    if not all((data / name).is_file() for name in INPUT_FILES):
        raise ValueError('--data must contain nodes.parquet, edges.parquet, transactions.parquet')
    dest.mkdir(parents=True, exist_ok=False)
    packet = dest / 'packet'
    (packet / 'checks').mkdir(parents=True)
    report = {'schema_version': 1, 'commit': commit, 'generated_at': datetime.now(timezone.utc).isoformat(),
              'status': 'failed', 'checks': [{'name': name, 'status': 'not_run', 'reason': 'Не выполнено'}
                                           for name in CHECK_NAMES],
              'input_sha256': {}, 'unverified': UNVERIFIED,
              'environment': {'python': platform.python_version(), 'platform': platform.platform()},
              'runner_sha256': digest(Path(__file__))}
    env = dict(os.environ, NVIDIA_API_KEY='', NVIDIA_MODEL='', CI='1', NO_COLOR='1', PYTHONDONTWRITEBYTECODE='1')
    env.pop('FORCE_COLOR', None)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    def record_check(record):
        report['checks'][CHECK_NAMES.index(record['name'])] = record
    try:
        with tempfile.TemporaryDirectory(prefix='taymas-acceptance-') as temp:
            work = Path(temp)
            snapshot(repo, commit, work)
            if digest(work / 'scripts/prepare_submission.py') != report['runner_sha256']:
                raise ValueError('Run the committed prepare_submission.py version for the selected ref')
            (work / 'data').mkdir()
            for name in INPUT_FILES:
                shutil.copyfile(data / name, work / 'data' / name)
                report['input_sha256'][name] = digest(work / 'data' / name)
            for program in ('node', 'npm'):
                try:
                    report['environment'][program] = subprocess.check_output(
                        [shutil.which(program) or program, '--version'], env=env, text=True, timeout=10).strip()
                except (OSError, subprocess.SubprocessError):
                    report['environment'][program] = 'недоступно'
            def run(name, command, cwd=work):
                record = stage(name, command, cwd, packet, env)
                record['directory'] = str(cwd.relative_to(work))
                record_check(record)
                return record['status'] == 'passed'
            run('python-tests', [sys.executable, '-m', 'pytest', '-q', 'tests'])
            pipeline_ok = run('pipeline-repro', [sys.executable, '-m', 'money_graph', '--data', 'data', '--out', 'out', '--check-repro'])
            if pipeline_ok:
                out = work / 'out'
                record_check({'name': 'pipeline-contract', 'status': 'failed',
                              'reason': 'Проверка числа узлов, времени, повторяемости и хешей'})
                details = json.loads((out / 'run_report.json').read_text())
                if details['input']['nodes'] != args.expected_nodes:
                    raise ValueError(f'Expected {args.expected_nodes} nodes, got {details["input"]["nodes"]}')
                if not details['reproducibility']['identical'] or details['total_sec'] > details['time_limit_sec']:
                    raise ValueError('Reproducibility/time gate failed')
                for name in CSV_FILES:
                    if digest(out / name) != details['outputs'][name]['sha256']:
                        raise ValueError(f'Pipeline report hash differs: {name}')
                    shutil.copyfile(out / name, packet / name)
                for name in ('run_report.md', 'run_report.json'):
                    shutil.copyfile(out / name, packet / name)
                record_check({'name': 'pipeline-contract', 'status': 'passed',
                              'reason': f'{args.expected_nodes} узлов; время и SHA-256 соответствуют отчёту'})
                run('copilot-evaluation', [sys.executable, '-m', 'agent_tools.evaluation', '--out', 'out',
                    '--answerer', 'agent_orchestrator.orchestrator:answer_case', '--report', str(packet / 'checks/evaluation.md')])
                cases = build_cases(out, commit)
                write_json(packet / 'demo_cases.json', cases)
                (packet / 'demo_cases.md').write_text(render_cases(cases, commit), encoding='utf-8')
                record_check({'name': 'three-demo-cases',
                    'status': 'passed' if all(c['available'] for c in cases) else 'failed',
                    'reason': ' / '.join(f'{c["id"]}: {c["available"]}' for c in cases)})
            npm = shutil.which('npm') or 'npm'
            if run('frontend-install', [npm, 'ci', '--ignore-scripts'], work / 'frontend'):
                run('frontend-tests', [npm, 'test'], work / 'frontend')
                run('frontend-build', [npm, 'run', 'build'], work / 'frontend')
            report['status'] = 'passed' if all(c['status'] == 'passed' for c in report['checks']) else 'failed'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
    write_json(packet / 'acceptance_report.json', report)
    (packet / 'acceptance_report.md').write_text(render_report(report), encoding='utf-8')
    if report['status'] != 'passed':
        print(f'FAILED: see {packet / "acceptance_report.md"}; no submission archive created', flush=True)
        return 1
    (packet / 'START_HERE.md').write_text(
        '# Материалы сдачи\n\n'
        f'Commit: `{commit}`.\n\n'
        '1. Откройте acceptance_report.md: результаты команд и непроверенные пункты.\n'
        '2. demo_cases.md: три примера с точными gid и полями CSV.\n'
        '3. nodes_roles.csv, clusters.csv и top_nodes.csv — обязательные выгрузки; edge_table.csv — связи для UI.\n'
        '4. run_report.md — проверки данных, время, версии библиотек и повторяемость.\n'
        '5. SHA256SUMS и manifest.json — контроль содержимого пакета.\n\n'
        'Исходные Parquet, код, node_modules и ключи в архив не входят. Это локальный пакет; отправка организаторам выполняется отдельно.\n'
        'Для повтора возьмите указанный commit и исходный набор с SHA-256 из manifest.json; команды приведены в отчёте.\n'
        'Новый commit требует нового прогона. Гипотезы о ролях не являются утверждениями о виновности.\n', encoding='utf-8')
    archive = dest / f'submission-{commit[:12]}.zip'
    package(packet, archive, report)
    verify_archive(archive)
    print(f'PASSED: {archive}', flush=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='data', help='Organizer Parquet directory')
    parser.add_argument('--dest', help='New directory outside the repository')
    parser.add_argument('--ref', default='HEAD', help='Committed revision to check')
    parser.add_argument('--expected-nodes', type=int, default=2248)
    parser.add_argument('--verify', type=Path, help='Verify an existing ZIP without rerunning checks')
    args = parser.parse_args(argv)
    try:
        if args.verify:
            result = verify_archive(args.verify)
            print(f'Checksums OK: {result["commit"]}; reported status={result["status"]}')
            return 0
        if not args.dest:
            parser.error('--dest is required when building a packet')
        return build(args)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
