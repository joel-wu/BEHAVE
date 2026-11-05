"""New source carriers must work through residual search and native scoring."""
import pytest

from .test_behavior_collection_membership import check_public_search_loop


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('optional', [False, True])
@pytest.mark.parametrize('wrong', [False, True])
def test_product_goal_to_new_input_to_native_score(tmp_path, optional, wrong):
    body = '''        self.cells = [None, None]
        if a == 37: self.cells[1] = b
        value = self.cells[1]
        if value is not None: return {'y': 7 + (b & 1)}
        return {'y': 0}
''' if optional else '''        if a == 37:
            best = float('inf')
            for i in range(3):
                distance = (b - i) * (b - i)
                if distance < best: best = distance
            return {'y': 7 + (b & 1)}
        return {'y': 0}
'''
    source = ('''class M:
    def reset(self): self.cells = [None, None]
    def process(self, a, b):
''' + body + '''    def process_slot(self, slot):
        if set(slot['inputs']) != {'left', 'right'}: return {}
        return {'result': self.process(slot['inputs']['left']['a'], slot['inputs']['right']['b'])}
''').encode()
    candidate = source.replace(b'7 + (b & 1)', b'8 + (b & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('worklist', [False, True])
@pytest.mark.parametrize('wrong', [False, True])
def test_finite_progress_search_replays_its_witness(tmp_path, worklist, wrong):
    loop = '''            pending = 1
            visited = 0
            while pending:
                pending -= 1
                for j in range(3):
                    target = (b >> j) & 3
                    if not ((visited >> target) & 1):
                        visited |= 1 << target
                        pending += 1
            result = visited
''' if worklist else '''            values = [b & 3, (b >> 2) & 3, 1]
            result = values[0]
            changed = 1
            while changed:
                changed = 0
                for j in range(3):
                    if values[j] < result:
                        result = values[j]
                        changed = 1
'''
    source = ('''class M:
    def reset(self): pass
    def process(self, a, b):
        if a == 37:
''' + loop + '''            return {'y': 7 + (result & 1)}
        return {'y': 0}
    def process_slot(self, slot):
        if set(slot['inputs']) != {'left', 'right'}: return {}
        return {'result': self.process(slot['inputs']['left']['a'], slot['inputs']['right']['b'])}
''').encode()
    candidate = source.replace(b'7 + (result & 1)', b'8 + (result & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_helper_state_cells_use_residual_goals_and_native_replay(tmp_path, wrong):
    source = b'''class M:
    def reset(self): self.counts = [0, 0]
    def take(self, which): self.counts[which] -= 1
    def process(self, a, b):
        self.counts[0] = 0
        self.counts[1] = b & 3
        while self.counts[1] > 0:
            self.take(1)
            self.counts[0] += 1
        if a == 37: return {'y': 7 + (self.counts[0] & 1)}
        return {'y': 0}
    def process_slot(self, slot):
        if set(slot['inputs']) != {'left', 'right'}: return {}
        return {'result': self.process(slot['inputs']['left']['a'], slot['inputs']['right']['b'])}
'''
    candidate = source.replace(b'7 + (self.counts[0] & 1)',
                               b'8 + (self.counts[0] & 1)') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)
