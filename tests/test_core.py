import json
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.core.timecode import parse_time, format_time
from app.video.captions import parse_srt, shift_srt
from app.ai.schemas import Candidate, Analysis, Metadata
from app.ai.scoring import rank, similar, score
from app.projects.store import ProjectStore
from app.video.engine import render_command, crop_filter
from app.ai import gemini


def candidate(cid=1, start='00:01:00.000', end='00:01:30.000', hook='The hook', topic='Topic'):
    return Candidate(id=cid, start=start, end=end, duration=parse_time(end)-parse_time(start),
                     hook=hook, topic=topic, summary='A complete point', reason='Good payoff',
                     scores={'hook': 9, 'curiosity': 8, 'value': 9, 'pacing': 8, 'payoff': 9, 'completeness': 10})


def test_timecodes_strict():
    assert parse_time('01:02:03.004') == 3723.004
    assert format_time(3723.004, ',') == '01:02:03,004'
    for invalid in ['01:60:01.000', '-01:00:00.000', 'bad', '00:00:01.999x']:
        with pytest.raises(ValueError):
            parse_time(invalid)


def test_srt_clip_boundary_and_overlap():
    cues = parse_srt('1\n00:01:17,000 --> 00:01:22,000\nHello\n\n2\n00:01:23,000 --> 00:01:30,000\nWorld')
    assert shift_srt(cues, 80, 86) == '1\n00:00:00,000 --> 00:00:02,000\nHello\n\n2\n00:00:03,000 --> 00:00:06,000\nWorld\n'
    with pytest.raises(ValueError):
        parse_srt('1\n00:00:00,000 --> 00:00:05,000\na\n\n2\n00:00:04,000 --> 00:00:07,000\nb')


def test_validation_and_scoring():
    strong = candidate()
    duplicate = candidate(2, '00:01:02.000', '00:01:32.000', 'Another hook')
    other = candidate(3, '00:03:00.000', '00:03:35.000', 'A new idea', 'Another topic')
    assert similar(strong, duplicate)
    assert not similar(strong, other)
    assert len(rank([strong, duplicate, other], 15, 60, 35, 5, 300)) == 2
    assert score(strong) > 70
    with pytest.raises(ValidationError):
        Candidate.model_validate({**strong.model_dump(), 'end': '00:02:00.000'})
    with pytest.raises(ValidationError):
        Analysis.model_validate({'candidates': [{**strong.model_dump(), 'scores': {**strong.scores.model_dump(), 'hook': 11}}]})
    with pytest.raises(ValidationError):
        Metadata.model_validate({'titles': []})


def test_project_resume_and_selected_preserved(tmp_path):
    project = ProjectStore(tmp_path / 'project')
    project.create('My Film', '/video.mp4', '/captions.srt', None, {'count': 3})
    project.save_candidates([(candidate(), 90)])
    project.select(1, True)
    project.update_short(1, status='READY', video='/render.mp4')
    job = project.job('Render', 1)
    project.job_status(job, 'RUNNING')
    project.cache('test', {'value': 123})
    project.close()
    reopened = ProjectStore(tmp_path / 'project')
    assert reopened.project()['name'] == 'My Film'
    assert reopened.jobs()[0]['status'] == 'PENDING'
    assert reopened.cached('test') == {'value': 123}
    reopened.save_candidates([(candidate(1, '00:02:00.000', '00:02:30.000'), 80),
                              (candidate(2, '00:03:00.000', '00:03:30.000'), 75)])
    assert reopened.candidates()[0]['selected'] == 1
    assert len(reopened.candidates()) == 2
    assert reopened.shorts()[0]['video'] == '/render.mp4'
    reopened.close()


def test_ffmpeg_arguments_are_safe():
    args = render_command('/some dir/a.mp4', '/out.mp4', 0, 30, '/some dir/a.srt', crop_mode='manual', crop_offset=.8)
    assert isinstance(args, list)
    assert args[args.index('-i')+1] == '/some dir/a.mp4'
    assert 'subtitles=' in args[args.index('-vf')+1]
    assert 'crop=1080:1920' in crop_filter()
    with pytest.raises(ValueError):
        render_command('a', 'b', 5, 1)


def test_analysis_never_sends_source_video(tmp_path, monkeypatch):
    srt = tmp_path / 'captions.srt'
    srt.write_text('1\n00:00:00,000 --> 00:00:10,000\nA thought', encoding='utf-8')
    store = ProjectStore(tmp_path / 'project')
    store.create('Video', '/private/video.mp4', srt, None, {'count': 1, 'min': 5, 'max': 30})
    captured = []
    fake_client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(gemini, 'client_for', lambda purpose: fake_client)
    def fake_generate(client, model, contents, schema):
        captured.extend(contents)
        return Analysis(candidates=[candidate(1, '00:00:00.000', '00:00:10.000')])
    monkeypatch.setattr(gemini, 'generate', fake_generate)
    gemini.analyze(store, {'analysis_model': 'test'}, 30)
    assert '/private/video.mp4' not in repr(captured)
    assert 'A thought' in repr(captured)
    captured.clear()
    gemini.analyze(store, {'analysis_model': 'test'}, 30)
    assert captured == []  # Cached; no new external call.
    store.close()


def test_gemini_structured_retry_without_network():
    class FakeModels:
        calls = 0
        def generate_content(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(text='garbage' if self.calls == 1 else json.dumps({'candidates': [candidate().model_dump()]}))
    fake = SimpleNamespace(models=FakeModels())
    assert gemini.generate(fake, 'model', ['transcript'], Analysis).candidates[0].id == 1
    assert fake.models.calls == 2
