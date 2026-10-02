from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from bakeoff import (
    automatic_checks,
    digest,
    draw_image,
    load_fixtures,
    load_results,
    prepare,
    score_card,
    scorecards,
    summarize,
    validate_plan,
    write_json,
)


def test_plan_pairs_all_controls_inputs_and_profiles(tmp_path):
    plan = prepare(tmp_path / "prepared", "bundle/model.gguf")
    assert len(plan["runs"]) == 26
    assert sum(not run["warmup"] for run in plan["runs"]) == 24
    by_case = {}
    for run in plan["runs"]:
        inputs = run["graph"]["2"]["inputs"]
        assert inputs["model"] == "bundle/model.gguf"
        assert inputs["cache_prompt"] is False
        assert inputs["release_after_generation"] is False
        assert inputs["partial_output_policy"] == "raise_error"
        if not run["warmup"]:
            by_case.setdefault(run["case_id"], []).append(run)
        if inputs["system_prompt"]:
            assert run["effective_system_prompt"] == inputs["system_prompt"]
        if run["arm"] == "freeform":
            assert run["effective_prompt"] == inputs["prompt"]
    for runs in by_case.values():
        assert [run["seed"] for run in runs] == [17, 17, 29, 29]
        assert [run["arm"] for run in runs] in [
            ["freeform", "candidate", "candidate", "freeform"],
            ["candidate", "freeform", "freeform", "candidate"],
        ]
        for left, right in (runs[:2], runs[2:]):
            assert left["graph"]["2"] == right["graph"]["2"]
            assert left["pair_sha256"] == right["pair_sha256"]
            assert left["graph"]["1"] != right["graph"]["1"]


def test_plan_rejects_changed_control_even_with_updated_plan_hash(tmp_path):
    plan = prepare(tmp_path / "prepared", "bundle/model.gguf")
    plan["runs"][2]["graph"]["2"]["inputs"]["temperature"] = 1.7
    plan["plan_sha256"] = digest(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    with pytest.raises(AssertionError):
        validate_plan(plan)


def test_plan_rejects_changed_harness_even_with_updated_plan_hash(tmp_path):
    plan = prepare(tmp_path / "prepared", "bundle/model.gguf")
    plan["harness_sha256"] = "0" * 64
    plan["plan_sha256"] = digest(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    )
    with pytest.raises(AssertionError, match="Harness changed"):
        validate_plan(plan)


@pytest.mark.parametrize("scene", ["count-relations", "readable-text", "occlusion-unknown"])
def test_images_are_repeatable_rgb_fixtures(tmp_path, scene):
    from PIL import Image

    first = draw_image(scene, tmp_path / "first.png")
    second = draw_image(scene, tmp_path / "second.png")
    assert first["rgb_sha256"] == second["rgb_sha256"]
    assert first["png_sha256"] == second["png_sha256"]
    with Image.open(tmp_path / "first.png") as image:
        assert image.size == (640, 480)
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (245, 241, 232)
        if scene == "count-relations":
            assert image.getpixel((120, 170)) == (223, 48, 48)
            assert image.getpixel((330, 165)) == image.getpixel((485, 165)) == (36, 104, 219)
        elif scene == "occlusion-unknown":
            assert image.getpixel((240, 150)) == (36, 104, 219)
            assert image.getpixel((240, 240)) == (147, 93, 56)


def test_operator_word_limit_40_passes_41_fails():
    specification, _ = load_fixtures()
    case = next(item for item in specification["cases"] if item["id"] == "text-operator-system")
    assert case["max_words"] == 40
    assert automatic_checks(case, "PROMPT:\n" + " ".join(["word"] * 40)) == {
        "word_limit": True,
        "format": True,
    }
    assert automatic_checks(case, "PROMPT:\n" + " ".join(["word"] * 41)) == {
        "word_limit": False,
        "format": True,
    }
    assert automatic_checks(case, "Wrong heading\nbrief")["format"] is False


def reviewed_card():
    return {
        "status": "complete",
        "reviewed": True,
        "facts": [{"id": "fact", "score": 1, "evidence": "Correct cited fact."}],
        "constraints": [{"id": "word_limit", "passed": True, "evidence": "Within limit."}],
        "automatic_checks": {"word_limit": True},
        "clarity": 2,
        "clarity_evidence": "Directly usable.",
        "unsupported_claims": [],
    }


def test_scoring_weights_penalty_cap_and_review_gates():
    card = reviewed_card()
    assert score_card(card) == 10
    card["facts"][0]["score"] = 0.5
    assert score_card(card) == 8
    card["unsupported_claims"] = [
        "added spoon: absent",
        "added steam: absent",
        "added plate: absent",
    ]
    assert score_card(card) == 4
    card["automatic_checks"]["word_limit"] = False
    with pytest.raises(AssertionError, match="automatic constraint"):
        score_card(card)
    card["status"] = "failed"
    assert score_card(card) == 0
    card["reviewed"] = False
    with pytest.raises(AssertionError):
        score_card(card)


def synthetic_results(directory: Path, plan: dict):
    """Aggregator test data only; these are not measured model-quality results."""
    records = []
    for run in plan["runs"]:
        response = (
            "PROMPT:\nfixture response"
            if run["case_id"] == "text-operator-system"
            else "fixture response"
        )
        records.append(
            {
                "id": run["id"],
                "plan_sha256": plan["plan_sha256"],
                "status": "complete",
                "response": response,
                "submitted_graph": copy.deepcopy(run["graph"]),
                "wall_ms": 110 if run["arm"] == "candidate" else 100,
                "execution_ms": 110 if run["arm"] == "candidate" else 100,
            }
        )
    (directory / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return records


def test_summary_blinds_arms_requires_review_and_calculates_paired_gate(tmp_path):
    directory = tmp_path / "prepared"
    plan = prepare(directory, "bundle/model.gguf")
    synthetic_results(directory, plan)
    scorecards(directory)
    sheet = json.loads((directory / "scorecards.json").read_text())
    key = json.loads((directory / "scoring-key.json").read_text())
    assert len(sheet["cards"]) == 24
    assert all("arm" not in card and "profile" not in card for card in sheet["cards"])
    with pytest.raises(AssertionError):
        summarize(directory)
    for card in sheet["cards"]:
        candidate = key[card["blind_id"]].endswith("-candidate")
        for item in card["facts"]:
            item.update(score=1 if candidate else 0.5, evidence="Synthetic scoring fixture.")
        for item in card["constraints"]:
            item.update(passed=True, evidence="Synthetic scoring fixture.")
        card.update(clarity=2, clarity_evidence="Synthetic scoring fixture.", reviewed=True)
    write_json(directory / "scorecards.json", sheet)
    result = summarize(directory)
    assert result["latency_metric"] == "execution_ms"
    for family in result["families"].values():
        assert family["mean_delta"] == 2
        assert family["median_latency_ratio"] == 1.1
        assert family["decision"] == "eligible_for_followup"
    sheet["cards"][0]["response"] = "edited output"
    write_json(directory / "scorecards.json", sheet)
    with pytest.raises(AssertionError):
        summarize(directory)


def test_results_reject_changed_sampler_or_missing_run(tmp_path):
    directory = tmp_path / "prepared"
    plan = prepare(directory, "bundle/model.gguf")
    records = synthetic_results(directory, plan)
    records[0]["submitted_graph"]["2"]["inputs"]["seed"] = 999
    path = directory / "results.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    with pytest.raises(AssertionError, match="Submitted graph"):
        load_results(directory, plan)
    path.write_text(json.dumps(records[0]) + "\n")
    with pytest.raises(AssertionError, match="Incomplete run set"):
        load_results(directory, plan)
