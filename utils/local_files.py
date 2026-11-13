"""로컬 폴더 선택과 스캔.

브라우저 File System Access API는 폴더를 고른 뒤 권한 창이 숨거나
민감한 경로에서 Promise가 끝나지 않는 경우가 있다. 이 앱은 로컬에서
동작하므로 폴더 대화상자와 파일 목록은 백엔드에서 처리한다.
"""
import os
import subprocess
import sys

from utils.osc_processor import get_pair_key, is_decay_filename, is_rise_filename

SKIP_DIRECTORIES = {
    '.git',
    '.cursor',
    '.github',
    'node_modules',
    'dist',
    '__pycache__',
    '.venv',
    'venv',
    'AppData',
    'Windows',
    'Program Files',
    'Program Files (x86)',
    '$Recycle.Bin',
    'System Volume Information',
}
MAX_SCAN_ENTRIES = 20000

_ALLOWED_ROOTS = set()


def _should_skip_directory(name):
    return name.startswith('.') or name in SKIP_DIRECTORIES


def remember_root(path):
    absolute = os.path.abspath(path)
    if not os.path.isdir(absolute):
        raise ValueError('폴더를 찾을 수 없습니다.')
    _ALLOWED_ROOTS.add(os.path.normcase(absolute))
    return absolute


def assert_allowed_directory(path):
    if not path or not isinstance(path, str):
        raise ValueError('폴더 경로가 없습니다.')
    absolute = os.path.abspath(path)
    if not os.path.isdir(absolute):
        raise ValueError('폴더를 찾을 수 없습니다.')
    if os.path.normcase(absolute) not in _ALLOWED_ROOTS:
        raise ValueError('이 세션에서 선택한 폴더만 사용할 수 있습니다. 폴더를 다시 선택해주세요.')
    return absolute


def safe_child(root, rel_path):
    rel = (rel_path or '').replace('\\', '/').strip('/')
    if not rel or any(part in ('', '.', '..') for part in rel.split('/')):
        raise ValueError('잘못된 파일 경로입니다.')
    absolute = os.path.abspath(os.path.join(root, *rel.split('/')))
    root_key = os.path.normcase(os.path.abspath(root))
    child_key = os.path.normcase(absolute)
    if child_key != root_key and not child_key.startswith(root_key + os.sep):
        raise ValueError('선택한 폴더 밖의 파일입니다.')
    if not os.path.isfile(absolute):
        raise ValueError(f'파일을 찾을 수 없습니다: {rel}')
    return absolute


def pick_folder():
    """네이티브 폴더 선택 창을 띄우고 절대 경로를 반환한다. 취소면 빈 문자열."""
    script = r'''
import sys
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    root.update()
except Exception:
    pass
path = filedialog.askdirectory(title="처리할 폴더를 선택하세요", mustexist=True) or ""
sys.stdout.buffer.write(path.encode("utf-8"))
root.destroy()
'''
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    completed = subprocess.run(
        [sys.executable, '-c', script],
        capture_output=True,
        creationflags=creationflags,
    )
    if completed.returncode != 0:
        err = completed.stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError(err or '폴더 선택 창을 열지 못했습니다.')
    return completed.stdout.decode('utf-8', errors='replace').strip()


def _walk_files(root):
    seen_dirs = set()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        real_dir = os.path.normcase(os.path.realpath(dirpath))
        if real_dir in seen_dirs:
            dirnames[:] = []
            continue
        seen_dirs.add(real_dir)
        dirnames[:] = [name for name in dirnames if not _should_skip_directory(name)]
        for name in filenames:
            count += 1
            if count > MAX_SCAN_ENTRIES:
                raise ValueError('폴더가 너무 커서 스캔을 중단했습니다. 실험 데이터가 있는 하위 폴더를 선택해주세요.')
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace('\\', '/')
            yield name, rel


def _build_osc_pairs(osc_files):
    rise_by_key = {}
    decay_by_key = {}
    unpaired = []

    for entry in osc_files:
        key = get_pair_key(entry['name'])
        if not key:
            unpaired.append({**entry, 'reason': 'rise/decay 접미사 없음'})
            continue
        if is_rise_filename(entry['name']):
            if key in rise_by_key:
                unpaired.append({**entry, 'reason': f'중복 rise 페어: {key}'})
            else:
                rise_by_key[key] = entry
            continue
        if is_decay_filename(entry['name']):
            if key in decay_by_key:
                unpaired.append({**entry, 'reason': f'중복 decay 페어: {key}'})
            else:
                decay_by_key[key] = entry
            continue
        unpaired.append({**entry, 'reason': 'rise/decay 접미사 없음'})

    pairs = []
    for key, rise in rise_by_key.items():
        decay = decay_by_key.pop(key, None)
        if decay:
            pairs.append({'key': key, 'rise': rise, 'decay': decay})
        else:
            unpaired.append({**rise, 'reason': f'decay 페어 없음: {key}'})
    for key, decay in decay_by_key.items():
        unpaired.append({**decay, 'reason': f'rise 페어 없음: {key}'})

    pairs.sort(key=lambda item: item['key'])
    return pairs, unpaired


def scan_batch(root):
    vil_files = []
    osc_files = []
    existing_trel = []

    for name, rel in _walk_files(root):
        if name.startswith('._') or not name.lower().endswith('.csv'):
            continue
        entry = {'name': name, 'relPath': rel}
        upper_name = name.upper()
        if 'VIL' in upper_name:
            vil_files.append(entry)
            continue
        if name.endswith('_TrEL.csv'):
            existing_trel.append(entry)
        if 'Hz' in name:
            osc_files.append(entry)

    osc_pairs, unpaired = _build_osc_pairs(osc_files)
    return {
        'vilFiles': vil_files,
        'oscPairs': osc_pairs,
        'unpairedOsc': unpaired,
        'existingTrel': existing_trel,
    }


def scan_analysis(root):
    analysis_files = []
    analysis_vil_files = []

    for name, rel in _walk_files(root):
        if name.startswith('._'):
            continue
        lower_name = name.lower()
        upper_name = name.upper()
        is_csv = lower_name.endswith('.csv')
        is_xlsx = lower_name.endswith('.xlsx')
        entry = {'name': name, 'relPath': rel}
        if is_csv and ('TrEL' in name):
            analysis_files.append(entry)
            continue
        if (is_csv or is_xlsx) and 'VIL' in upper_name and '_processed' in name:
            analysis_vil_files.append(entry)

    return {
        'analysisFiles': analysis_files,
        'analysisVilFiles': analysis_vil_files,
    }


def read_text(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as handle:
        return handle.read()


def read_bytes(path):
    with open(path, 'rb') as handle:
        return handle.read()


def output_file(root, rel_path, output_name):
    rel_dir = os.path.dirname((rel_path or '').replace('\\', '/'))
    parts = [part for part in rel_dir.split('/') if part]
    target_dir = os.path.join(root, 'TrEL_processed', *parts)
    os.makedirs(target_dir, exist_ok=True)
    return os.path.join(target_dir, os.path.basename(output_name))
