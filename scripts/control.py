#!/usr/bin/env python3
"""Thin client + private Unix-socket worker. Only the worker imports CV/UI libraries."""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MAX_REQUEST = 1_048_576


def paths(name):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}', name):
        raise ValueError('session name: 1..32 letters, numbers, _ or -')
    directory = Path('/tmp') / f'markforge-{os.getuid()}'
    if directory.is_symlink():
        raise ValueError('unsafe runtime directory')
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.getuid():
        raise ValueError('runtime directory has another owner')
    directory.chmod(0o700)
    return directory / f'{name}.sock', directory / f'{name}.log'


def rpc(name, request, timeout=20):
    address, _ = paths(name)
    payload = json.dumps(request, ensure_ascii=False).encode()+b'\n'
    if len(payload) > MAX_REQUEST:
        raise ValueError('request too large')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(address))
        client.sendall(payload)
        with client.makefile('rb') as reader:
            response = reader.readline(MAX_REQUEST*4)
        if not response:
            raise RuntimeError('worker closed the connection without a result')
        return json.loads(response)


def serve(args):
    from markforge.backends import MacBackend, BrowserBackend
    from markforge.session import Session
    address, _ = paths(args.session)
    # Parent checked for a live worker; binding never replaces an existing socket.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        backend = None
        bound = False
        try:
            backend = (MacBackend(args.app, window_id=args.window_id) if args.backend == 'mac'
                       else BrowserBackend(args.url, headless=args.headless))
            session = Session(backend)
            server.bind(str(address))
            bound = True
            os.chmod(address, 0o600)
            server.listen(4)
            idle_since = time.monotonic()
            server.settimeout(30)
            while time.monotonic()-idle_since < 600:
                try:
                    client, _ = server.accept()
                except socket.timeout:
                    continue
                idle_since = time.monotonic()
                stop = False
                with client:
                    client.settimeout(5)
                    try:
                        with client.makefile('rb') as reader:
                            line = reader.readline(MAX_REQUEST+1)
                        if len(line) > MAX_REQUEST or not line.endswith(b'\n'):
                            raise ValueError('invalid or oversized JSON-line request')
                        req = json.loads(line)
                        operation = req.pop('op')
                        if operation == 'status':
                            result = {'status': 'OK', 'session': args.session, 'pid': os.getpid(),
                                      'context': backend.context(), 'observation_count': session.observations,
                                      'frame_id': session.frame['id'] if session.frame else None}
                        elif operation == 'observe':
                            result = session.observe(**req)
                        elif operation == 'act':
                            result = session.act(**req)
                        elif operation == 'stop':
                            result, stop = {'status': 'STOPPED', 'session': args.session}, True
                        else:
                            raise ValueError('supported operations: status, observe, act, stop')
                    except Exception as exc:
                        result = {'status': 'ERROR', 'reason': str(exc)}
                    try:
                        client.sendall(json.dumps(result, ensure_ascii=False).encode()+b'\n')
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # A disconnected client never causes replay or worker loss.
                if stop:
                    break
        finally:
            try:
                if backend:
                    backend.close()
            finally:
                if bound:
                    address.unlink(missing_ok=True)


def start(args):
    address, log_path = paths(args.session)
    if address.exists():
        try:
            existing = rpc(args.session, {'op': 'status'}, timeout=2)
        except (ConnectionRefusedError, FileNotFoundError):
            address.unlink(missing_ok=True)
        else:
            # Do not silently reuse another app/browser under this session name.
            raise ValueError(f'session already exists; use observe/act or stop it first: {existing}')
    command = [sys.executable, str(Path(__file__).resolve()), '_serve', '--session', args.session,
               '--backend', args.backend]
    if args.backend == 'mac':
        if not args.app:
            raise ValueError('mac backend requires --app <bundle-id>')
        command += ['--app', args.app]
        if args.window_id is not None:
            command += ['--window-id', str(args.window_id)]
    else:
        if not args.url:
            raise ValueError('browser backend requires --url')
        command += ['--url', args.url]
        if args.headless:
            command += ['--headless']
    log_fd = os.open(log_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(log_fd, 'wb') as log:
        worker = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                  cwd=ROOT, start_new_session=True)
    deadline = time.monotonic()+25
    while time.monotonic() < deadline:
        if worker.poll() is not None:
            lines = log_path.read_text(errors='replace').strip().splitlines()
            reason = lines[-1] if lines else 'worker exited before initialization'
            raise RuntimeError(f'{reason} (details: {log_path})')
        try:
            return rpc(args.session, {'op': 'status'}, timeout=0.3)
        except (OSError, json.JSONDecodeError, RuntimeError):
            time.sleep(0.05)
    # Stop only the exact worker we just created, never kill by process name.
    worker.terminate()
    worker.wait(timeout=5)
    raise RuntimeError(f'worker did not become ready; inspect {log_path}')


def parser():
    root = argparse.ArgumentParser(description='MarkForge persistent observe / act controller')
    sub = root.add_subparsers(dest='command', required=True)
    for name in ('start', '_serve', 'status', 'observe', 'act', 'stop'):
        p = sub.add_parser(name)
        p.add_argument('--session', default='desktop')
        if name in ('start', '_serve'):
            p.add_argument('--backend', choices=['mac', 'browser'], default='mac')
            p.add_argument('--app')
            p.add_argument('--window-id', type=int)
            p.add_argument('--url')
            p.add_argument('--headless', action='store_true')
        if name == 'observe':
            p.add_argument('--out')
            p.add_argument('--detect', action='store_true')
            p.add_argument('--ocr', action='store_true')
            p.add_argument('--marked-out')
        if name == 'act':
            p.add_argument('--frame-id', required=True)
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument('--actions', help='JSON action array')
            group.add_argument('--actions-file', help='file containing a JSON action array')
            p.add_argument('--stable-layout', action='store_true')
            p.add_argument('--expect-text')
            p.add_argument('--timeout-ms', type=int, default=1500)
            p.add_argument('--out')
            p.add_argument('--dry-run', action='store_true')
    return root


def main():
    args = parser().parse_args()
    if args.command == '_serve':
        serve(args)
        return 0
    try:
        if args.command == 'start':
            result = start(args)
        else:
            req = {'op': args.command}
            if args.command == 'observe':
                req.update({key: getattr(args, key) for key in ('out', 'detect', 'ocr', 'marked_out')})
            elif args.command == 'act':
                actions = args.actions if args.actions is not None else Path(args.actions_file).read_text()
                req.update({key: getattr(args, key) for key in
                           ('frame_id', 'stable_layout', 'expect_text', 'timeout_ms', 'out', 'dry_run')})
                req['actions'] = json.loads(actions)
            result = rpc(args.session, req)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in ('OK', 'STOPPED', 'DRY_RUN') else 3
    except Exception as exc:
        print(json.dumps({'status': 'ERROR', 'reason': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
