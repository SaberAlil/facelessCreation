from __future__ import annotations

import pytest
import yaml
from conftest import REPO

from faceless_studio.config import Settings, load_settings
from faceless_studio.schemas import JobState
from faceless_studio.util import FacelessError, GateError


def test_default_yaml_matches_code_defaults():
    shipped = yaml.safe_load((REPO / "config" / "default.yaml").read_text())
    code = Settings().model_dump(mode="json", exclude={"project_root"})
    assert shipped == code, "config/default.yaml drifted from config.py defaults"


def test_env_overrides(tmp_path):
    s = load_settings(root=REPO, environ={
        "FACELESS__RENDER__ENCODER": "libx264", "FACELESS__TTS__SPEED": "1.1",
        "FACELESS__PATHS__DATA_DIR": str(tmp_path)})
    assert s.render.encoder == "libx264"
    assert s.tts.speed == 1.1
    assert s.data_dir == tmp_path


def test_unknown_config_key_rejected(tmp_path):
    with pytest.raises(Exception, match="extra"):
        load_settings(root=REPO, environ={"FACELESS__RENDER__NOPE": "1"})


def test_create_job_and_duplicate_day(studio):
    j = studio.create_job("Why prices end in 99", day=1, date="2027-01-01")
    assert j.id.startswith("d001-")
    assert studio.paths(j.id).job_json.is_file()
    with pytest.raises(FacelessError, match="already has job"):
        studio.create_job("Another", day=1)


def test_invalid_job_id_rejected(studio):
    with pytest.raises(FacelessError):
        studio.paths("../etc")


def test_state_flow_and_invalidation(pipeline, short_script, research_file, small_limits):
    pl = small_limits(pipeline)
    j = pl.studio.create_job("Test topic").id
    assert pl.refresh(j) == JobState.DRAFT
    pl.draft(j, script_file=short_script, research_file=research_file)
    assert pl.refresh(j) == JobState.RESEARCH_REVIEW
    with pytest.raises(GateError):
        pl.voice(j)  # gate: not approved
    pl.approve_script(j, "tester")
    assert pl.refresh(j) == JobState.SCRIPT_APPROVED
    # Editing the approved script invalidates the approval
    p = pl.paths(j).script
    p.write_text(p.read_text().replace("Test Video Title", "Edited Title"))
    assert pl.refresh(j) == JobState.RESEARCH_REVIEW
    assert not pl.approval_valid(j, "script")
    # Editing research also invalidates it
    pl.approve_script(j, "tester")
    r = pl.paths(j).research
    r.write_text(r.read_text().replace('"A"', '"B"'))
    assert pl.refresh(j) == JobState.RESEARCH_REVIEW
    transitions = [t["to_state"] for t in pl.studio.transitions(j)]
    assert transitions[:3] == ["DRAFT", "RESEARCH_REVIEW", "SCRIPT_APPROVED"]


def test_redraft_keeps_history(pipeline, short_script, small_limits):
    pl = small_limits(pipeline)
    j = pl.studio.create_job("Test topic").id
    pl.draft(j, script_file=short_script)
    pl.draft(j, script_file=short_script)
    assert len(list(pl.paths(j).drafts.glob("script-*.json"))) == 1
