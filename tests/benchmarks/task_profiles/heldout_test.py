from __future__ import annotations

import copy
import json

import bakeoff as base
import heldout
import pytest


@pytest.fixture
def prepared(tmp_path):
    prior = tmp_path / "prior"
    prior_plan = base.prepare(prior, "synthetic-prior.gguf")
    base.write_json(prior / "summary.json", {"plan_sha256": prior_plan["plan_sha256"]})
    (prior / "results.jsonl").write_text('{"synthetic_test_only": true}\n')
    directory = tmp_path / "heldout"
    manifest = heldout.prepare(directory, {"qwen": "qwen.gguf", "secondary": "minicpm.gguf"}, prior)
    return directory, manifest


def test_freezes_two_models_24_measured_unchanged_profile_and_controls(prepared):
    directory, manifest = prepared
    plans = [base.read_json(directory / key / "plan.json") for key in heldout.MODEL_KEYS]
    assert sum(len(plan["runs"]) for plan in plans) == 26
    assert sum(not run["warmup"] for plan in plans for run in plan["runs"]) == 24
    prior = base.read_json(directory.parent / "prior" / "plan.json")
    assert manifest["design"]["profiles"]["candidate"] == prior["profiles"]["visible-evidence-v1"]
    assert manifest["design"]["specification"]["settings"] == prior["specification"]["settings"]
    assert manifest["design"]["prior"]["files"]["results.jsonl"] == heldout.file_hash(
        directory.parent / "prior" / "results.jsonl"
    )
    assert plans[0]["images"] == plans[1]["images"]
    for plan in plans:
        runs = plan["runs"][1:]
        for index in range(0, 12, 2):
            left, right = runs[index : index + 2]
            assert left["pair_sha256"] == right["pair_sha256"]
            assert left["graph"]["2"] == right["graph"]["2"]
            assert left["graph"]["1"] != right["graph"]["1"]
        for run in runs:
            if run["graph"]["2"]["inputs"]["system_prompt"]:
                assert (
                    run["effective_system_prompt"] == run["graph"]["2"]["inputs"]["system_prompt"]
                )
    assert plans[0]["runs"][1]["arm"] != plans[1]["runs"][1]["arm"]
    heldout.validate_experiment(directory)


@pytest.mark.parametrize(
    "scene", ["heldout-symbol-grid", "heldout-digit-bars", "heldout-screened-paths"]
)
def test_new_images_repeatable_and_visible_landmarks(tmp_path, scene):
    from PIL import Image

    first = heldout.draw_image(scene, tmp_path / "first.png")
    second = heldout.draw_image(scene, tmp_path / "second.png")
    assert first["png_sha256"] == second["png_sha256"]
    assert first["rgb_sha256"] == second["rgb_sha256"]
    with Image.open(tmp_path / "first.png") as image:
        assert image.size == (800, 600) and image.mode == "RGB"
        if scene == "heldout-symbol-grid":
            assert image.getpixel((150, 170)) == (232, 121, 22)
            assert image.getpixel((150, 420)) == (255, 255, 255)
            assert image.getpixel((400, 420)) == (36, 104, 219)
        elif scene == "heldout-digit-bars":
            assert image.getpixel((400, 250)) == (23, 143, 136)
            assert image.getpixel((160, 250)) == (255, 255, 255)
            assert image.getpixel((640, 350)) == (231, 175, 36)
        else:
            assert image.getpixel((200, 300)) == (138, 138, 138)
            assert image.getpixel((700, 360)) == (232, 121, 22)
            assert image.getpixel((700, 300)) == (255, 255, 255)


def test_rejects_changed_source_control_and_image(prepared, monkeypatch):
    directory, _ = prepared
    plan = base.read_json(directory / "qwen" / "plan.json")
    plan["runs"][1]["graph"]["2"]["inputs"]["temperature"] = 2
    plan["plan_sha256"] = base.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(AssertionError, match="Controls"):
        heldout.validate_model_plan(plan)
    sources = heldout.source_hashes()
    monkeypatch.setattr(heldout, "source_hashes", lambda: {**sources, "heldout.py": "changed"})
    with pytest.raises(AssertionError, match="sources changed"):
        heldout.validate_experiment(directory)
    monkeypatch.undo()
    (directory / "qwen" / "heldout-symbol-grid.png").write_bytes(b"damaged")
    with pytest.raises(AssertionError):
        heldout.validate_experiment(directory)


def passing_models():
    return {
        key: {
            "mean_delta": 0.5,
            "task_mean_deltas": {"one": -0.5, "two": 1, "three": 1},
            "candidate_constraints_all_pass": True,
            "unsupported_claims": {"candidate": 1, "freeform": 1},
            "median_latency_ratio": 1.25,
            "all_measured_complete": True,
        }
        for key in heldout.MODEL_KEYS
    }


def test_decision_boundaries_and_every_disqualifier():
    models = passing_models()
    assert heldout.decide(models)["decision"] == "ship"
    changes = [
        ("mean_delta", -0.001),
        ("task_mean_deltas", {"one": -0.5001}),
        ("candidate_constraints_all_pass", False),
        ("unsupported_claims", {"candidate": 2, "freeform": 1}),
        ("median_latency_ratio", 1.2501),
        ("all_measured_complete", False),
    ]
    for key, value in changes:
        changed = copy.deepcopy(models)
        changed["secondary"][key] = value
        assert heldout.decide(changed)["decision"] == "defer", key
    models["qwen"]["mean_delta"] = 0.4999
    assert heldout.decide(models)["decision"] == "defer"


def synthetic_results(directory):
    """Scoring tests only. No model quality or measured latency is implied."""
    for key in heldout.MODEL_KEYS:
        plan = base.read_json(directory / key / "plan.json")
        records = [
            {
                "id": run["id"],
                "plan_sha256": plan["plan_sha256"],
                "status": "complete",
                "response": "Synthetic test response.",
                "submitted_graph": copy.deepcopy(run["graph"]),
                "execution_ms": 110 if run["arm"] == "candidate" else 100,
                "wall_ms": 120 if run["arm"] == "candidate" else 110,
            }
            for run in plan["runs"]
        ]
        (directory / key / "results.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records)
        )


def test_blinded_combined_scoring_and_frozen_rubric(prepared):
    directory, _ = prepared
    synthetic_results(directory)
    heldout.scorecards(directory)
    sheet = base.read_json(directory / "scorecards.json")
    key = base.read_json(directory / "scoring-key.json")
    assert len(sheet["cards"]) == 24
    assert all(
        not {"arm", "model", "model_key", "profile"}.intersection(card) for card in sheet["cards"]
    )
    with pytest.raises(AssertionError):
        heldout.summarize(directory)
    for card in sheet["cards"]:
        candidate = key[card["blind_id"]][1].endswith("-candidate")
        for item in card["facts"]:
            item.update(score=1 if candidate else 0.5, evidence="Synthetic fixture.")
        for item in card["constraints"]:
            item.update(passed=True, evidence="Synthetic fixture.")
        card.update(clarity=2, clarity_evidence="Synthetic fixture.", reviewed=True)
    base.write_json(directory / "scorecards.json", sheet)
    summary = heldout.summarize(directory)
    assert summary["measured_count"] == 24
    assert summary["combined_mean_delta"] == 2
    assert summary["decision"] == "ship"
    assert all(
        model["digit_facts"]["candidate"]["fully_correct"] == 6
        for model in summary["models"].values()
    )
    sheet["cards"][0]["facts"][0]["description"] = "Easier revised criterion."
    base.write_json(directory / "scorecards.json", sheet)
    with pytest.raises(AssertionError, match="criterion changed"):
        heldout.summarize(directory)


def test_transport_adapter_keeps_validation_and_restores_original(prepared, tmp_path, monkeypatch):
    directory, _ = prepared
    environment = tmp_path / "environment.json"
    base.write_json(environment, {"context_size": 4096})
    original = base.validate_plan
    observed = []

    def fake_transport(target, server, supplied_environment):
        assert base.validate_plan is heldout.validate_model_plan
        base.validate_plan(base.read_json(target / "plan.json"))
        observed.append((target.name, server, supplied_environment))
        raise RuntimeError("inert transport stopped")

    monkeypatch.setattr(base, "run_live", fake_transport)
    with pytest.raises(RuntimeError, match="inert"):
        heldout.run_live(directory, "qwen", "http://inert.invalid", environment)
    assert len(observed) == 1
    assert base.validate_plan is original
    base.write_json(environment, {"context_size": 8192})
    with pytest.raises(AssertionError, match="4096"):
        heldout.run_live(directory, "secondary", "http://inert.invalid", environment)
    assert len(observed) == 1
