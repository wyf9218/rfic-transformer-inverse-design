"""Only the optional CLI output transport; no real cohort or projection rerun."""
import hashlib
import json

import pytest

from research.broadband56_nn import eucap15_received_landing_increment as subject


def test_output_cli_writes_complete_json_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    payload = dict(rows=[dict(request_id=str(i), actual_response=[1.0, 1.1, 13.0, .4],
                              original_proposal={"units": "µm", "metadata": "x" * 1024})
                        for i in range(32)], converted_received={"rows": []})
    monkeypatch.setattr(subject, "run", lambda *args, **kwargs: payload)
    path = tmp_path / "complete.json"
    args = ["--baseline", "unused", "--received", "unused", "--output", str(path)]
    subject.main(args)
    pin = json.loads(capsys.readouterr().out)
    raw = path.read_bytes()
    assert json.loads(raw) == payload
    assert pin == dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                       bytes=len(raw), status="WRITTEN")
    with pytest.raises(FileExistsError):
        subject.main(args)
    assert capsys.readouterr().out == ""
    assert path.read_bytes() == raw
    # The default remains the existing complete stdout representation.
    subject.main(["--baseline", "unused", "--received", "unused"])
    assert json.loads(capsys.readouterr().out) == payload
    # A computation failure must not create an output or print a success pin.
    def fail(*args, **kwargs):
        raise ValueError("synthetic computation failure")
    monkeypatch.setattr(subject, "run", fail)
    failed_path = tmp_path / "never-created.json"
    with pytest.raises(ValueError, match="synthetic computation failure"):
        subject.main(args[:-1] + [str(failed_path)])
    assert not failed_path.exists()
    assert capsys.readouterr().out == ""
