from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import threading

import pytest

spec = importlib.util.spec_from_file_location('batch_preparation', Path(__file__).parents[3] / 'scripts/batch_preparation.py')
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def frontier(agent='SourceIndexBuilderAgent'):
    step = {'step_id': 's1', 'status': 'created', 'agent_type': agent, 'started_at': None}
    flow = {'flow_id': 'f1', 'flow_type': 'source_index_build', 'status': 'running',
            'scope_id': 'repo:test', 'current_step_id': 's1', 'steps': [step]}
    return {'key': 'one', 'server': 'http://one', 'repo_key': 'Test', 'instance': 'p1',
            'runtime': {'paused': True, 'running_step_ids': [], 'active_flow_advances': [], 'created_step_ids': ['s1']},
            'flows': [flow], 'steps': [step]}


def prepared():
    snap = frontier('CoordinatorAgent')
    snap['flows'][0]['flow_type'] = 'native_repo_coordinator'
    for kind in batch.DISCOVERY | {'source_index_build', 'root_interface_preparation', 'native_repo_preparation'}:
        snap['flows'].append({'flow_id': kind, 'flow_type': kind, 'status': 'completed', 'steps': []})
    return snap


def test_coordinator_stop_gate_and_missing_discovery():
    snap = prepared()
    assert batch.decide(snap) == ('prepared', None)
    snap['flows'].pop()
    # Remove an actual discovery result, not just the parent preparation.
    snap['flows'] = [f for f in snap['flows'] if f['flow_type'] != 'repo_mathlib_recon']
    assert batch.decide(snap)[0] == 'coordinator_gate_incomplete'
    snap['steps'][0]['started_at'] = 'yesterday'
    assert batch.decide(snap)[0] == 'coordinator_already_started'


def test_unknown_agent_and_content_flow_never_admitted():
    assert batch.decide(frontier('UnexpectedAgent'))[0] == 'unknown_agent'
    snap = frontier()
    snap['flows'][0]['flow_type'] = 'content_node_task'
    assert batch.decide(snap)[0] == 'outside_preparation'


def test_active_and_suspended_never_admitted():
    snap = frontier()
    snap['runtime']['paused'] = False
    assert batch.decide(snap)[0] == 'active'
    snap['runtime']['paused'] = True
    snap['steps'][0]['status'] = 'suspended'
    assert batch.decide(snap)[0] == 'needs_review'


def test_logic_uses_server_defaults():
    snap = frontier()
    snap['steps'] = []
    result, body = batch.decide(snap)
    assert result == 'advance'
    assert body == {'granularity': 'step', 'action': 'logic', 'scope_id': 'repo:test'}


class FakeServer:
    def __init__(self):
        self.snap = frontier()
        self.posts = 0
        self.fail_after_post = False

    def __call__(self, method, url, body=None, timeout=20):
        if method == 'POST':
            self.posts += 1
            self.snap['runtime']['paused'] = False
            if self.fail_after_post:
                raise TimeoutError('response lost')
            return {'lease_id': 'lease-new'}
        if url.endswith('/health'):
            return {'process_instance_id': self.snap['instance'], 'repo_runtimes': {'repos': [{'repo_key': 'Test', 'loaded': True}]}}
        if url.endswith('/runtime/status'):
            return copy.deepcopy(self.snap['runtime'])
        if url.endswith('/flows/tree'):
            return {'roots': [{'flow': copy.deepcopy(f), 'children': []} for f in self.snap['flows']]}
        if '/steps/' in url:
            return copy.deepcopy(self.snap['steps'][0])
        raise AssertionError(url)


def client(server):
    return batch.Client({'key': 'one', 'admin_base_url': 'http://one', 'repo_key': 'Test'}, server)


def test_repeated_advance_posts_once(tmp_path):
    server = FakeServer()
    c = client(server)
    assert c.advance(tmp_path)['advance'] == 'accepted'
    assert c.advance(tmp_path)['advance'] == 'not_admitted'
    assert server.posts == 1


def test_uncertain_post_is_inspected_and_never_retried(tmp_path):
    server = FakeServer()
    server.fail_after_post = True
    c = client(server)
    result = c.advance(tmp_path)
    assert result['advance'] == 'uncertain'
    assert result['observed']['disposition'] == 'active'
    server.snap['runtime']['paused'] = True
    assert c.advance(tmp_path)['advance'] == 'held_uncertain'
    assert server.posts == 1


def test_coordinator_never_posts(tmp_path):
    server = FakeServer()
    server.snap = prepared()
    assert client(server).advance(tmp_path)['disposition'] == 'prepared'
    assert server.posts == 0


def test_process_lock_blocks_duplicate_writer(tmp_path):
    server = FakeServer()
    c = client(server)
    identity = batch.hashlib.sha256(b'http://one/Test').hexdigest()[:20]
    with batch.locked(tmp_path / (identity + '.lock')):
        with pytest.raises(BlockingIOError):
            c.advance(tmp_path)
    assert server.posts == 0


def test_wait_coalesces_projects_and_deduplicates_events(tmp_path):
    barrier = threading.Barrier(2)
    class Observer:
        def __init__(self, key): self.key = key
        def snapshot(self):
            barrier.wait(timeout=2)
            return {'key': self.key, 'server': 'http://' + self.key, 'repo_key': 'Test',
                    'instance': 'p1', 'disposition': 'prepared'}
    clients = [Observer('a'), Observer('b')]
    first = batch.wait_batch(clients, tmp_path / 'wait.json', timeout=.02)
    assert len(first['events']) == 2
    second = batch.wait_batch(clients, tmp_path / 'wait.json', timeout=.02)
    assert second['events'] == [] and second['timed_out']


def test_unloaded_repo_not_implicitly_loaded():
    calls = []
    def http(method, url, **kwargs):
        calls.append(url)
        return {'process_instance_id': 'p1', 'repo_runtimes': {'repos': []}}
    assert client(http).snapshot()['disposition'] == 'not_loaded'
    assert len(calls) == 1


def test_wait_reports_restarted_server(tmp_path):
    server = FakeServer()
    c = client(server)
    batch.wait_batch([c], tmp_path / 'wait.json', timeout=.01)
    server.snap['instance'] = 'p2'
    result = batch.wait_batch([c], tmp_path / 'wait.json', timeout=.01)
    assert result['events'] == [{'key': 'one', 'event': 'server_restarted'}]


def test_missing_agent_identity_is_not_treated_as_logic():
    snap = frontier()
    snap['steps'][0]['agent_type'] = None
    snap['steps'][0]['step_type'] = 'source_index_builder_agent_step'
    assert batch.decide(snap)[0] == 'unknown_agent'


def test_one_client_failure_does_not_hide_other_result():
    def operation(value):
        if value == 1:
            raise RuntimeError('offline')
        return {'ok': True}
    result = batch.parallel([1, 2], operation)
    assert result[0]['disposition'] == 'client_error'
    assert result[1] == {'ok': True}


def test_first_event_does_not_wait_for_slow_endpoint(tmp_path):
    import time
    release = threading.Event()
    entered = threading.Event()

    class Slow:
        project = {'key': 'slow'}
        def snapshot(self):
            entered.set()
            release.wait(timeout=3)
            return {'key': 'slow', 'disposition': 'prepared'}

    class Fast:
        project = {'key': 'fast'}
        def snapshot(self):
            assert entered.wait(timeout=1)
            return {'key': 'fast', 'disposition': 'prepared'}

    start = time.monotonic()
    try:
        result = batch.wait_batch([Slow(), Fast()], tmp_path / 'wait.json', timeout=2)
        assert time.monotonic() - start < 1
        assert result['events'] == [{'key': 'fast', 'event': 'prepared'}]
        assert result['pending_projects'] == ['slow']
        again = batch.wait_batch([Fast()], tmp_path / 'wait.json', timeout=.05)
        assert again['events'] == []
    finally:
        release.set()


def test_waiting_discovery_parent_can_resume_logic_only_for_own_completed_children():
    snap = frontier()
    snap['steps'] = []
    parent = snap['flows'][0]
    parent.update(flow_type='native_repo_coordinator', status='waiting',
                  phase='waiting_repo_exploration', current_step_id=None, steps=[])
    for kind in batch.DISCOVERY:
        snap['flows'].append({'flow_id': kind, 'flow_type': kind, 'status': 'completed',
                              'parent_flow_id': 'f1', 'steps': []})
    assert batch.decide(snap) == ('advance', {
        'granularity': 'step', 'action': 'logic', 'scope_id': 'repo:test'})
    snap['flows'][-1]['parent_flow_id'] = 'unrelated'
    assert batch.decide(snap)[0] == 'needs_review'
    snap['flows'][-1]['parent_flow_id'] = 'f1'
    snap['flows'][-1]['status'] = 'failed'
    assert batch.decide(snap)[0] == 'needs_review'
    snap['flows'][-1]['status'] = 'completed'
    parent['phase'] = 'waiting_content_tasks'
    assert batch.decide(snap)[0] == 'needs_review'


def execution_frontier(phase='coordinator_callback'):
    snap = frontier('CoordinatorAgent')
    snap['repo_key'] = 'Test'
    snap['steps'][0]['flow_id'] = 'f1'
    snap['flows'][0].update(flow_type='native_repo_coordinator', phase=phase)
    snap['coordinator_state'] = {'position': {'phase': phase}}
    return snap


def test_execution_coordinator_is_explicit_and_preparation_still_stops():
    snap = execution_frontier()
    assert batch.decide_execution(snap) == ('advance', {
        'granularity': 'step', 'action': 'agent', 'step_id': 's1'})
    assert batch.decide(snap)[1] is None
    snap['steps'][0]['started_at'] = 'yesterday'
    assert batch.decide_execution(snap)[0] == 'needs_review'


@pytest.mark.parametrize('phase', ['before_content_task_dispatch_snapshot', 'waiting_content_tasks'])
def test_execution_batch_identity_and_default_safety(phase):
    from lean_constellation.app.semantic_scheduler import RuntimeSemanticAdvanceInput
    snap = execution_frontier(phase)
    snap['steps'] = []
    snap['flows'][0]['steps'] = []
    snap['flows'][0]['current_step_id'] = None
    snap['coordinator_state'].update(pending_dispatch_kind='content_tasks',
        pending_dispatch_source_submission_id='sub1', pending_dispatch_source_step_id='source1',
        pending_content_node_paths=['Main.A', 'Main.B'])
    if phase == 'waiting_content_tasks':
        snap['coordinator_state']['waiting_dispatch_step_id'] = 'dispatch1'
    disposition, body = batch.decide_execution(snap)
    assert disposition == 'advance' and body['granularity'] == 'content_batch'
    assert body['expected_source_submission_id'] == 'sub1'
    assert body.get('expected_dispatch_step_id') == ('dispatch1' if phase == 'waiting_content_tasks' else None)
    assert 'safety' not in body
    assert RuntimeSemanticAdvanceInput.model_validate(body).safety.max_step_starts == 500
    snap['coordinator_state']['pending_dispatch_source_submission_id'] = None
    assert batch.decide_execution(snap)[0] == 'needs_review'


def test_execution_review_active_and_resource_boundaries_hold():
    snap = execution_frontier()
    snap['lease_review_required'] = True
    assert batch.decide_execution(snap)[0] == 'needs_review'
    snap['runtime']['paused'] = False
    assert batch.decide_execution(snap)[0] == 'active'
    snap = execution_frontier('waiting_requirement')
    assert batch.decide_execution(snap)[0] == 'needs_review'
    snap = execution_frontier()
    snap['steps'][0]['agent_type'] = 'ContentPlanAgent'
    assert batch.decide_execution(snap)[0] == 'needs_review'


@pytest.mark.parametrize('publication_status', ['ready', 'stable'])
def test_execution_logic_and_ready_require_actual_completion(publication_status):
    snap = execution_frontier('mark_repo_ready')
    snap['steps'] = []
    assert batch.decide_execution(snap) == ('advance', {
        'granularity': 'step', 'action': 'logic', 'scope_id': 'repo:test'})
    snap['flows'][0]['status'] = 'completed'
    assert batch.decide_execution(snap)[0] == 'needs_review'
    snap['run_status'] = {'publication_status': publication_status}
    assert batch.decide_execution(snap) == ('ready', None)


@pytest.mark.parametrize('blocker', ['failed_flow', 'pending_step', 'active_runtime', 'review'])
def test_stable_publication_does_not_bypass_completion_guards(blocker):
    snap = execution_frontier('mark_repo_ready')
    snap['flows'][0]['status'] = 'completed'
    snap['steps'] = []
    snap['run_status'] = {'publication_status': 'stable'}
    if blocker == 'failed_flow':
        snap['flows'][0]['status'] = 'failed'
    elif blocker == 'pending_step':
        snap['steps'] = [{'step_id': 'pending', 'status': 'created'}]
    elif blocker == 'active_runtime':
        snap['runtime']['paused'] = False
    else:
        snap['lease_review_required'] = True
    assert batch.decide_execution(snap)[0] != 'ready'


def test_execution_reads_only_matching_persisted_coordinator(tmp_path):
    import json
    snap = execution_frontier()
    flow = snap['flows'][0]
    path = tmp_path / '.agent_runtime/scopes/repo/flows/f1/flow.json'
    path.parent.mkdir(parents=True)
    value = {**flow, 'input': {'repo_root': str(tmp_path)}, 'state': snap['coordinator_state']}
    path.write_text(json.dumps(value))
    assert batch.read_coordinator_state({'repo_root': str(tmp_path)}, flow) == value['state']
    value['status'] = 'waiting'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='frontier changed'):
        batch.read_coordinator_state({'repo_root': str(tmp_path)}, flow)


def test_resource_supporting_branch_and_exact_curator():
    snap = execution_frontier('before_resource_request_dispatch_snapshot')
    snap['steps'] = []
    snap['coordinator_state'].update(pending_dispatch_kind='resource_request',
                                    resource_requested_use='supporting_material')
    assert batch.decide_execution(snap)[1]['action'] == 'logic'
    snap['coordinator_state']['resource_requested_use'] = 'provider'
    assert batch.decide_execution(snap)[0] == 'needs_review'
    snap['coordinator_state'].update(resource_requested_use='supporting_material',
                                    waiting_dispatch_step_id='d1', position={'phase': 'waiting_resource_request'})
    snap['flows'][0]['phase'] = 'waiting_resource_request'
    child = {'flow_id': 'r1', 'flow_type': 'resource_curation', 'parent_flow_id': 'f1',
             'parent_dispatch_step_id': 'd1', 'status': 'running'}
    snap['flows'].append(child)
    snap['steps'] = [{'step_id': 'curator', 'flow_id': 'r1', 'agent_type': 'ResourceCuratorAgent',
                      'status': 'created', 'started_at': None}]
    assert batch.decide_execution(snap)[1] == {'granularity': 'step', 'action': 'agent', 'step_id': 'curator'}
    child['parent_dispatch_step_id'] = 'old'
    assert batch.decide_execution(snap)[0] == 'needs_review'
    child.update(parent_dispatch_step_id='d1', status='failed')
    assert batch.decide_execution(snap)[0] == 'needs_review'
    child['status'] = 'completed'
    snap['steps'] = []
    assert batch.decide_execution(snap)[1]['action'] == 'logic'


def test_manual_pause_resource_review_is_exact_and_completed_only():
    snap = execution_frontier('waiting_resource_request')
    snap['steps'] = []
    snap['coordinator_state'].update(pending_dispatch_kind='resource_request',
                                    resource_requested_use='supporting_material', waiting_dispatch_step_id='d1')
    child = {'flow_id': 'r1', 'flow_type': 'resource_curation', 'parent_flow_id': 'f1',
             'parent_dispatch_step_id': 'd1', 'status': 'completed'}
    snap['flows'].append(child)
    snap['lease_review_required'] = True
    snap['lease'] = {'lease_id': 'old', 'status': 'terminal', 'terminal_reason': 'manual_pause'}
    assert batch.decide_execution(snap)[0] == 'needs_review'
    assert batch.decide_execution(snap, 'wrong')[0] == 'needs_review'
    assert batch.decide_execution(snap, 'old')[1]['action'] == 'logic'
    for reason in ['content_batch_recovery_required:x', 'semantic_safety_cap_exhausted']:
        snap['lease']['terminal_reason'] = reason
        assert batch.decide_execution(snap, 'old')[0] == 'needs_review'
    snap['lease']['terminal_reason'] = 'manual_pause'
    child['status'] = 'running'
    assert batch.decide_execution(snap, 'old')[0] == 'needs_review'
    child['status'] = 'completed'
    snap['coordinator_state']['resource_requested_use'] = 'provider'
    assert batch.decide_execution(snap, 'old')[0] == 'needs_review'
