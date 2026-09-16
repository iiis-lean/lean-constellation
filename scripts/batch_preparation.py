#!/usr/bin/env python3
"""Control preparation across independent LC servers using existing Admin APIs."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import time
from queue import Empty, Queue
from threading import Event, Thread
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

PREP_AGENTS = {
    'RepoFormatDiscoveryAgent', 'SourceCorpusBuilderAgent', 'SourceCorpusReviewerAgent',
    'SourceIndexBuilderAgent', 'SourceIndexReviewerAgent', 'RootInterfacePrepareAgent',
    'RepoResourceDiscoveryAgent', 'RepoLeanProviderDiscoveryAgent', 'RepoMathlibReconAgent',
}
PREP_FLOWS = {
    'native_repo_preparation', 'native_repo_continuation', 'source_index_build',
    'root_interface_preparation', 'repo_resource_discovery', 'repo_lean_provider_discovery',
    'repo_mathlib_recon', 'native_repo_coordinator',
}
DISCOVERY = {'repo_resource_discovery', 'repo_lean_provider_discovery', 'repo_mathlib_recon'}


def request(method, url, body=None, timeout=20):
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request(url, data=data, method=method,
                         headers={'Content-Type': 'application/json'}), timeout=timeout) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError(str(result.get('issues', 'Admin request failed')))
    return result.get('value', result)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def flow_records(tree):
    found = {}
    def visit(value):
        if isinstance(value, dict):
            if value.get('flow_id') and value.get('flow_type'):
                found[value['flow_id']] = value
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(tree)
    return list(found.values())


class Client:
    def __init__(self, project, transport=request, phase="preparation", reviewed_resource_lease=None):
        self.project = project
        self.phase = phase
        self.reviewed_resource_lease = reviewed_resource_lease
        self.http = transport
        self.base = project['admin_base_url'].rstrip('/')
        self.repo = self.base + '/admin/repos/' + quote(project['repo_key'], safe='')

    def snapshot(self):
        out = {'key': self.project['key'], 'server': self.base, 'repo_key': self.project['repo_key']}
        try:
            health = self.http('GET', self.base + '/health')
            out['instance'] = health['process_instance_id']
            # Do not implicitly load a dormant repo through a repo-scoped GET.
            loaded = health['repo_runtimes']['repos']
            if not any(r['repo_key'] == self.project['repo_key'] and r.get('loaded') for r in loaded):
                return dict(out, disposition='not_loaded')
            status = self.http('GET', self.repo + '/runtime/status')
            out['runtime'] = status
            lease_id = (status.get('run_control') or {}).get('lease_id')
            if lease_id:
                monitor = self.http('GET', self.repo + '/runtime/leases/' + lease_id)
                out['lease'] = monitor['lease']
                out['lease_review_required'] = monitor.get('requires_review', False)
                out['batch_bookmark'] = monitor.get('content_batch_bookmark')
            tree = self.http('GET', self.repo + '/flows/tree')
            out['flows'] = flow_records(tree)
            ids = status.get('created_step_ids', []) + status.get('running_step_ids', [])
            out['steps'] = [self.http('GET', self.repo + '/steps/' + sid) for sid in dict.fromkeys(ids)]
            if self.phase == 'execution':
                out['run_status'] = self.http('GET', self.repo + '/run/status')
                if status['paused'] and not status['running_step_ids'] and not status['active_flow_advances']:
                    coordinators = [f for f in out['flows'] if f['flow_type'] == 'native_repo_coordinator'
                                    and f['status'] not in {'completed', 'failed'}]
                    if len(coordinators) == 1:
                        out['coordinator_state'] = read_coordinator_state(self.project, coordinators[0])
                out['disposition'], out['action'] = decide_execution(out, self.reviewed_resource_lease)
            else:
                out['disposition'], out['action'] = decide(out)
            return out
        except Exception as exc:
            return dict(out, disposition='unavailable', error=str(exc))

    def advance(self, state_dir):
        identity = hashlib.sha256((self.base + '/' + self.project['repo_key']).encode()).hexdigest()[:20]
        receipt = state_dir / (identity + '.advance.json')
        with locked(state_dir / (identity + '.lock')):
            before = self.snapshot()
            if receipt.exists() and json.loads(receipt.read_text()).get('status') in {'posting', 'uncertain'}:
                return dict(before, advance='held_uncertain', receipt=str(receipt))
            if before.get('disposition') != 'advance':
                return dict(before, advance='not_admitted')
            # A second live inspection catches changes during planning. Exclusive external writer required.
            current = self.snapshot()
            if current.get('instance') != before.get('instance') or current.get('action') != before['action'] or current.get('disposition') != 'advance' or current.get('coordinator_state') != before.get('coordinator_state') or current.get('lease') != before.get('lease'):
                return dict(current, advance='frontier_changed')
            intent = {'status': 'posting', 'project': self.project, 'instance': before['instance'],
                      'body': before['action'], 'recorded_at': time.time(),
                      'reviewed_resource_lease': self.reviewed_resource_lease}
            write_json(receipt, intent)
            try:
                result = self.http('POST', self.repo + '/runtime/semantic-advance', before['action'])
            except Exception as exc:
                intent.update(status='uncertain', error=str(exc), observed=self.snapshot())
                write_json(receipt, intent)
                return {'key': self.project['key'], 'advance': 'uncertain', 'receipt': str(receipt),
                        'observed': intent['observed']}
            intent.update(status='accepted', result=result)
            write_json(receipt, intent)
            return {'key': self.project['key'], 'advance': 'accepted', 'result': result, 'receipt': str(receipt)}


def decide(snapshot):
    runtime, flows, steps = snapshot['runtime'], snapshot['flows'], snapshot['steps']
    if not runtime['paused'] or runtime['running_step_ids'] or runtime['active_flow_advances']:
        return 'active', None
    if snapshot.get('lease', {}).get('status') == 'active':
        return 'active_lease_paused', None
    if not flows:
        return 'not_started', None
    if any(f['flow_type'] not in PREP_FLOWS for f in flows):
        return 'outside_preparation', None
    if any(s.get('agent_type') == 'CoordinatorAgent' and s.get('started_at')
           for f in flows for s in f.get('steps', [])):
        return 'coordinator_already_started', None
    active = [f for f in flows if f['status'] != 'completed']
    if any(f['status'] in {'failed', 'suspended'} or f.get('manual_pause_active') for f in active):
        return 'needs_review', None
    for f in active:
        current = next((s for s in f.get('steps', []) if s['step_id'] == f.get('current_step_id')), None)
        if current and current['status'] in {'failed', 'suspended'}:
            return 'needs_review', None
    coordinators = [s for s in steps if s.get('agent_type') == 'CoordinatorAgent']
    if coordinators:
        prepared = {f['flow_type'] for f in flows if f['status'] == 'completed'}
        gate = DISCOVERY | {'source_index_build', 'root_interface_preparation'}
        complete = (len(coordinators) == 1 and len(steps) == 1
                    and coordinators[0]['status'] == 'created' and not coordinators[0].get('started_at')
                    and gate <= prepared
                    and all(f['flow_type'] == 'native_repo_coordinator' for f in active))
        return ('prepared' if complete else 'coordinator_gate_incomplete'), None
    if any(s.get('agent_type') not in PREP_AGENTS for s in steps if s.get('agent_type')):
        return 'unknown_agent', None
    if any('agent' in s.get('step_type', '') and not s.get('agent_type') for s in steps):
        return 'unknown_agent', None
    agents = [s for s in steps if s.get('agent_type') in PREP_AGENTS and s['status'] == 'created']
    if agents:
        return 'advance', {'granularity': 'step', 'action': 'agent', 'step_id': agents[0]['step_id']}
    # Only a current, nonterminal, recognized Flow can supply a logic scope.
    runnable = [f for f in active if f['status'] in {'created', 'running'}]
    for flow in active:
        if (flow['flow_type'] == 'native_repo_coordinator'
                and flow['status'] == 'waiting'
                and flow.get('phase') == 'waiting_repo_exploration'
                and not flow.get('current_step_id')):
            children = [f for f in flows if f.get('parent_flow_id') == flow['flow_id']]
            if (len(children) == 3
                    and {f['flow_type'] for f in children} == DISCOVERY
                    and all(f['status'] == 'completed' for f in children)):
                runnable.append(flow)
    if not runnable:
        return 'needs_review', None
    return 'advance', {'granularity': 'step', 'action': 'logic', 'scope_id': runnable[-1]['scope_id']}


def read_coordinator_state(project, flow):
    """Read local persisted dispatch identity, then let Admin validate admission."""
    root = Path(project['repo_root']).resolve()
    matches = list((root / '.agent_runtime/scopes').glob('*/flows/' + flow['flow_id'] + '/flow.json'))
    if len(matches) != 1:
        raise ValueError('Coordinator persisted truth is missing or ambiguous')
    record = json.loads(matches[0].read_text())
    for key in ('flow_id', 'flow_type', 'scope_id', 'status', 'current_step_id'):
        if record.get(key) != flow.get(key):
            raise ValueError('Coordinator Admin/persisted frontier changed')
    if Path(record['input']['repo_root']).resolve() != root:
        raise ValueError('Coordinator repo root mismatch')
    state = record['state']
    if state['position']['phase'] != flow['phase']:
        raise ValueError('Coordinator phase changed')
    if state.get('pending_dispatch_kind') == 'resource_request':
        source_id = state.get('pending_dispatch_source_step_id')
        source = json.loads((matches[0].parent / 'steps' / source_id / 'step.json').read_text())
        submission = source.get('submission') or {}
        if submission.get('submission_id') != state.get('pending_dispatch_source_submission_id'):
            raise ValueError('Resource source submission changed')
        state = dict(state, resource_requested_use=submission.get('requested_use'))
    return state


def decide_execution(snapshot, reviewed_resource_lease=None):
    runtime, flows, steps = snapshot['runtime'], snapshot['flows'], snapshot['steps']
    if not runtime['paused'] or runtime['running_step_ids'] or runtime['active_flow_advances']:
        return 'active', None
    if snapshot.get('lease', {}).get('status') == 'active':
        return 'active_lease_paused', None
    manual_review = snapshot.get('lease_review_required', False)
    if manual_review:
        lease = snapshot.get('lease', {})
        if (not reviewed_resource_lease or lease.get('lease_id') != reviewed_resource_lease
                or lease.get('status') != 'terminal' or lease.get('terminal_reason') != 'manual_pause'):
            return 'needs_review', None
    coordinators = [f for f in flows if f['flow_type'] == 'native_repo_coordinator'
                    and f['status'] not in {'completed', 'failed'}]
    if not coordinators:
        if (snapshot.get('run_status', {}).get('publication_status') in {'ready', 'stable'}
                and flows and all(f['status'] == 'completed' for f in flows) and not steps):
            return 'ready', None
        return 'needs_review', None
    if len(coordinators) != 1:
        return 'needs_review', None
    coordinator = coordinators[0]
    if coordinator.get('manual_pause_active') or coordinator['status'] not in {'created', 'running', 'waiting'}:
        return 'needs_review', None
    state = snapshot.get('coordinator_state', {})
    phase = coordinator.get('phase')
    if state.get('position', {}).get('phase') != phase:
        return 'needs_review', None
    if manual_review:
        children = [f for f in flows if f.get('parent_flow_id') == coordinator['flow_id']
                    and f.get('parent_dispatch_step_id') == state.get('waiting_dispatch_step_id')]
        if (phase != 'waiting_resource_request' or not state.get('waiting_dispatch_step_id')
                or steps or len(children) != 1 or children[0]['status'] != 'completed'
                or children[0]['flow_type'] != 'resource_curation'
                or children[0].get('manual_pause_active')):
            return 'needs_review', None
    current = next((s for s in coordinator.get('steps', [])
                    if s['step_id'] == coordinator.get('current_step_id')), None)
    if current and current['status'] in {'failed', 'suspended'}:
        return 'needs_review', None
    agents = [s for s in steps if s.get('agent_type') == 'CoordinatorAgent']
    if agents:
        step = agents[0]
        if (len(steps) != 1 or step.get('flow_id') != coordinator['flow_id']
                or step['status'] != 'created' or step.get('started_at')
                or step['step_id'] != coordinator.get('current_step_id')
                or phase not in {'coordinator_agent', 'coordinator_callback'}):
            return 'needs_review', None
        return 'advance', {'granularity': 'step', 'action': 'agent', 'step_id': step['step_id']}
    if phase in {'before_content_task_dispatch_snapshot', 'dispatch_content_tasks',
                 'waiting_content_tasks', 'after_content_task_batch_snapshot'}:
        if (state.get('pending_dispatch_kind') != 'content_tasks'
                or not state.get('pending_dispatch_source_submission_id')
                or not state.get('pending_dispatch_source_step_id')
                or not state.get('pending_content_node_paths')):
            return 'needs_review', None
        dispatch = state.get('waiting_dispatch_step_id')
        if phase in {'waiting_content_tasks', 'after_content_task_batch_snapshot'} and not dispatch:
            return 'needs_review', None
        body = {'granularity': 'content_batch', 'repo_key': snapshot['repo_key'],
                'coordinator_flow_id': coordinator['flow_id'],
                'expected_source_submission_id': state['pending_dispatch_source_submission_id']}
        if dispatch:
            body['expected_dispatch_step_id'] = dispatch
        return 'advance', body
    if phase in {'before_resource_request_dispatch_snapshot', 'dispatch_resource_request',
                 'waiting_resource_request', 'after_resource_request_terminal_snapshot'}:
        if (state.get('pending_dispatch_kind') != 'resource_request'
                or state.get('resource_requested_use') != 'supporting_material'):
            return 'needs_review', None
        children = [f for f in flows if f.get('parent_flow_id') == coordinator['flow_id']
                    and f.get('parent_dispatch_step_id') == state.get('waiting_dispatch_step_id')]
        if phase == 'waiting_resource_request':
            if len(children) != 1 or children[0]['flow_type'] != 'resource_curation':
                return 'needs_review', None
            child = children[0]
            if child['status'] in {'failed', 'suspended'} or child.get('manual_pause_active'):
                return 'needs_review', None
            curators = [s for s in steps if s.get('agent_type') == 'ResourceCuratorAgent'
                        and s.get('flow_id') == child['flow_id']]
            if curators:
                step = curators[0]
                if len(steps) != 1 or step['status'] != 'created' or step.get('started_at'):
                    return 'needs_review', None
                return 'advance', {'granularity': 'step', 'action': 'agent', 'step_id': step['step_id']}
            if child['status'] not in {'created', 'running', 'completed'}:
                return 'needs_review', None
        if any(s.get('agent_type') for s in steps):
            return 'needs_review', None
        return 'advance', {'granularity': 'step', 'action': 'logic', 'scope_id': coordinator['scope_id']}
    # Content AgentSteps are never individually started by the execution driver.
    if any(s.get('agent_type') for s in steps):
        return 'needs_review', None
    if phase in {'coordinator_agent', 'coordinator_callback', 'mark_repo_ready'}:
        return 'advance', {'granularity': 'step', 'action': 'logic', 'scope_id': coordinator['scope_id']}
    return 'needs_review', None


def parallel(clients, fn):
    def isolated(client):
        try:
            return fn(client)
        except Exception as exc:
            project = getattr(client, 'project', {})
            return {'key': project.get('key'), 'disposition': 'client_error', 'error': str(exc)}
    with ThreadPoolExecutor(max_workers=max(1, len(clients))) as pool:
        return list(pool.map(isolated, clients))


def fingerprint(snapshot):
    return [snapshot.get('instance'), snapshot['disposition'], snapshot.get('action'),
            snapshot.get('lease', {}).get('lease_id'), snapshot.get('error')]


def wait_batch(clients, state_path, timeout=3600, wait_s=20):
    """Wake on any actionable project, without joining slow endpoint requests."""
    with locked(state_path.with_suffix('.watch.lock')):
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        deadline = time.monotonic() + timeout
        stopped, updates = Event(), Queue()

        def observe(index, client):
            while not stopped.is_set():
                try:
                    snap = client.snapshot()
                except Exception as exc:
                    snap = {'key': getattr(client, 'project', {}).get('key'),
                            'disposition': 'client_error', 'error': str(exc)}
                if stopped.is_set():
                    return
                updates.put((index, snap))
                lease = snap.get('lease', {})
                duration = min(wait_s, max(.1, deadline - time.monotonic()))
                if snap['disposition'] == 'active' and lease.get('status') == 'active':
                    query = urlencode({'after_version': lease['version'], 'timeout_s': duration})
                    try:
                        client.http('GET', client.repo + '/runtime/leases/' + lease['lease_id'] + '/wait?' + query,
                                    timeout=duration + 10)
                    except Exception:
                        stopped.wait(.1)
                else:
                    stopped.wait(min(duration, 2))

        # Read-only daemon observers cannot delay process exit on a slow HTTP call.
        # Only this thread persists cursor state; late observations are discarded.
        for index, client in enumerate(clients):
            Thread(target=observe, args=(index, client), daemon=True).start()
        snapshots, events = {}, []
        return_at = deadline
        try:
            while time.monotonic() < return_at:
                try:
                    index, snap = updates.get(timeout=max(.001, return_at - time.monotonic()))
                except Empty:
                    break
                snapshots[index] = snap
                key = snap.get('server', '') + '/' + snap.get('repo_key', str(snap.get('key')))
                old = state.get(key, {})
                mark = fingerprint(snap)
                if old.get('instance') and old['instance'] != snap.get('instance') and snap.get('instance'):
                    events.append({'key': snap['key'], 'event': 'server_restarted'})
                elif snap['disposition'] != 'active' and old.get('fingerprint') != mark:
                    events.append({'key': snap['key'], 'event': snap['disposition']})
                state[key] = {'fingerprint': mark, 'instance': snap.get('instance') or old.get('instance'),
                              'lease_version': snap.get('lease', {}).get('version')}
                if events:
                    # Collect near-simultaneous events, never await every endpoint.
                    return_at = min(return_at, time.monotonic() + .05)
            write_json(state_path, state)
            return {'events': events, 'snapshots': [snapshots[i] for i in sorted(snapshots)],
                    'pending_projects': [getattr(c, 'project', {}).get('key', str(i))
                                         for i, c in enumerate(clients) if i not in snapshots],
                    'timed_out': not events}
        finally:
            stopped.set()


def compact(value):
    if isinstance(value, list):
        return [compact(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {k: compact(v) for k, v in value.items() if k != 'flows'}
    if 'flows' in value:
        counts = {}
        for flow in value['flows']:
            label = flow['flow_type'] + ':' + flow['status']
            counts[label] = counts.get(label, 0) + 1
        result['flow_counts'] = counts
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'wait', 'advance-preparation', 'advance-execution'])
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--project', action='append', help='Project key; repeat or omit for all')
    parser.add_argument('--timeout', type=float, default=3600)
    parser.add_argument('--phase', choices=['preparation', 'execution'], default='preparation',
                        help='Observation mode for status/wait; advance commands choose their own mode')
    parser.add_argument('--reviewed-resource-lease', help='Acknowledge one exact manual-pause lease after its resource child completed')
    args = parser.parse_args()
    rows = json.loads(args.catalog.read_text())
    if isinstance(rows, dict):
        rows = rows['projects']
    selected = [p for p in rows if not args.project or p['key'] in args.project]
    if not selected or (args.project and set(args.project) - {p['key'] for p in selected}):
        parser.error('No projects selected or unknown project key')
    endpoints = [(p['admin_base_url'], p['repo_key']) for p in selected]
    if len(set(endpoints)) != len(endpoints):
        parser.error('Duplicate server/repo target')
    phase = args.command.removeprefix('advance-') if args.command.startswith('advance-') else args.phase
    if phase == 'execution' and any(not p.get('repo_root') for p in selected):
        parser.error('Execution mode requires local repo_root in the catalog')
    if args.reviewed_resource_lease and (args.command != 'advance-execution' or len(selected) != 1):
        parser.error('Resource review requires advance-execution and exactly one project')
    clients = [Client(p, phase=phase, reviewed_resource_lease=args.reviewed_resource_lease) for p in selected]
    if args.timeout <= 0:
        parser.error('timeout must be positive')
    if args.command == 'wait':
        result = wait_batch(clients, args.state_dir / ('wait.json' if phase == 'preparation' else 'wait-execution.json'), timeout=args.timeout)
    elif args.command == 'status':
        result = parallel(clients, lambda c: c.snapshot())
    else:
        result = parallel(clients, lambda c: c.advance(args.state_dir))
    print(json.dumps(compact(result), indent=2))


if __name__ == '__main__':
    main()
