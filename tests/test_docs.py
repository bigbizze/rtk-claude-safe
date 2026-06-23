from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ROOT / "README.md",
    ROOT / "SAFE_RTK_COMMANDS.md",
    ROOT / "TEST_RESULTS.md",
]


def _docs_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in DOCS)


def test_public_docs_describe_current_rtk_baseline() -> None:
    text = _docs_text()

    assert "0.42.4" in text
    assert "0.37.1" not in text
    assert "rtk 0.37.2" not in text


def test_public_docs_do_not_recommend_legacy_hook_install_flow() -> None:
    text = _docs_text()

    stale_phrases = [
        "runs rtk's official hook installer",
        "Runs `rtk init -g --hook-only`",
        '"command": "rtk hook claude"',
        "delegating to `rtk hook claude`",
    ]
    for phrase in stale_phrases:
        assert phrase not in text
