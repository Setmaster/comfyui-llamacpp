"""One frozen two-model Visible Evidence evaluation, reusing the original recorder."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import statistics
from contextlib import contextmanager
from pathlib import Path

import bakeoff as base

HERE = Path(__file__).resolve().parent
MODEL_KEYS = ("qwen", "secondary")
GATES = {
    "model_mean_min": 0.0,
    "model_task_mean_min": -0.5,
    "combined_mean_min": 0.5,
    "candidate_constraints_all_pass": True,
    "candidate_unsupported_at_most_freeform": True,
    "model_median_latency_max": 1.25,
}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes() -> dict:
    return {
        "heldout.py": file_hash(Path(__file__)),
        "heldout_cases.json": file_hash(HERE / "heldout_cases.json"),
        "bakeoff.py": file_hash(HERE / "bakeoff.py"),
        "profiles.json": file_hash(HERE / "profiles.json"),
        "generation/profiles.py": file_hash(base.ROOT / "generation/profiles.py"),
    }


def draw_image(scene: str, destination: Path) -> dict:
    """Repo-authored integer geometry; no model, external assets, or font files."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    if scene == "heldout-symbol-grid":
        orange, blue = "#e87916", "#2468db"
        for x, y, filled in ((150, 170, True), (400, 170, True), (150, 420, False)):
            points = ((x, y - 75), (x + 75, y), (x, y + 75), (x - 75, y))
            draw.polygon(points, fill=orange if filled else "white")
            draw.line((*points, points[0]), fill=orange, width=10, joint="curve")
        for x, y, filled in ((650, 170, False), (400, 420, True), (650, 420, False)):
            draw.ellipse(
                (x - 65, y - 65, x + 65, y + 65),
                fill=blue if filled else "white",
                outline=blue,
                width=10,
            )
    elif scene == "heldout-digit-bars":
        segments = {
            "a": (12, 0, 68, 10),
            "b": (68, 10, 78, 60),
            "c": (68, 70, 78, 120),
            "d": (12, 120, 68, 130),
            "e": (2, 70, 12, 120),
            "f": (2, 10, 12, 60),
            "g": (12, 60, 68, 70),
        }
        for center, enabled in ((160, "abgcd"), (400, "abc"), (640, "abged")):
            for name in enabled:
                x0, y0, x1, y1 = segments[name]
                draw.rectangle((center - 40 + x0, 35 + y0, center - 40 + x1, 35 + y1), fill="black")
        for center, top, color in (
            (160, 390, "#8651aa"),
            (400, 230, "#178f88"),
            (640, 310, "#e7af24"),
        ):
            draw.rectangle((center - 65, top, center + 65, 530), fill=color)
    elif scene == "heldout-screened-paths":
        for left, right_y in ((40, 300), (440, 360)):
            draw.rectangle((left, 80, left + 320, 520), outline="#b5b5b5", width=3)
            draw.line((left + 40, 300, left + 130, 300), fill="#e87916", width=14)
            draw.line((left + 210, right_y, left + 280, right_y), fill="#e87916", width=14)
            draw.rectangle((left + 120, 180, left + 220, 420), fill="#8a8a8a")
    else:
        raise ValueError(scene)
    image.save(destination, format="PNG", optimize=False)
    return {
        "file": destination.name,
        "size": list(image.size),
        "rgb_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        "png_sha256": file_hash(destination),
    }


def model_plan(design: dict, model_key: str, images: dict) -> dict:
    specification = design["specification"]
    model = design["models"][model_key]
    runs = []

    def append(case, seed, arm, warmup=False):
        profile = base.TaskProfileSnapshot.from_dict(design["profiles"][arm])
        graph = base.graph_for(case, profile, specification["settings"], model, seed)
        prompt, system = base.apply_task_profile(profile, case["prompt"], case["system_prompt"])
        runs.append(
            {
                "id": ("warmup-" if warmup else "") + f"{case['id']}-{seed}-{arm}",
                "case_id": case["id"],
                "family": "vision",
                "seed": seed,
                "arm": arm,
                "warmup": warmup,
                "graph": graph,
                "pair_sha256": base.digest(
                    {"inputs": graph["2"]["inputs"], "image": images[case["image"]]}
                ),
                "profile_sha256": profile.content_sha256,
                "effective_prompt": prompt,
                "effective_system_prompt": system,
            }
        )

    append(specification["cases"][0], 7, "freeform", warmup=True)
    for case_index, case in enumerate(specification["cases"]):
        for seed_index, seed in enumerate(specification["seeds"]):
            arms = ("freeform", "candidate")
            if (case_index + seed_index + MODEL_KEYS.index(model_key)) % 2:
                arms = tuple(reversed(arms))
            for arm in arms:
                append(case, seed, arm)
    plan = {
        "schema_version": 1,
        "design_sha256": base.digest(design),
        "sources": design["sources"],
        "model_key": model_key,
        "model": model,
        "specification": specification,
        "profiles": design["profiles"],
        "images": images,
        "runs": runs,
    }
    plan["plan_sha256"] = base.digest(plan)
    return plan


def validate_model_plan(plan: dict) -> None:
    assert plan["sources"] == source_hashes(), "Held-out sources changed after freeze"
    assert plan["plan_sha256"] == base.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    cases = plan["specification"]["cases"]
    assert len(cases) == 3 and plan["specification"]["seeds"] == [17, 29]
    design = {
        "specification": plan["specification"],
        "profiles": plan["profiles"],
        "models": {plan["model_key"]: plan["model"]},
        "sources": plan["sources"],
    }
    reconstructed = model_plan(design, plan["model_key"], plan["images"])
    assert plan["runs"] == reconstructed["runs"], "Controls, pairing, order or profile changed"
    assert len(plan["runs"]) == 13 and sum(run["warmup"] for run in plan["runs"]) == 1


def prepare(directory: Path, models: dict, prior: Path) -> dict:
    assert set(models) == set(MODEL_KEYS) and len(set(models.values())) == 2
    prior_plan = base.read_json(prior / "plan.json")
    base.validate_plan(prior_plan)
    prior_summary = base.read_json(prior / "summary.json")
    assert prior_summary["plan_sha256"] == prior_plan["plan_sha256"]
    specification = base.read_json(HERE / "heldout_cases.json")
    specification["settings"] = copy.deepcopy(prior_plan["specification"]["settings"])
    _, profiles = base.load_fixtures()
    candidate = profiles["visible-evidence-v1"]
    assert candidate.as_dict() == prior_plan["profiles"][candidate.id], (
        "Candidate must stay unchanged"
    )
    original_protocol = base.ROOT / "docs/research/task-profile-bakeoff-2026-10-02.md"
    design = {
        "schema_version": 1,
        "models": models,
        "sources": source_hashes(),
        "specification": specification,
        "gates": GATES,
        "context_size": 4096,
        "profiles": {"freeform": base.FREEFORM_PROFILE.as_dict(), "candidate": candidate.as_dict()},
        "prior": {
            "directory": str(prior.resolve()),
            "plan_sha256": prior_plan["plan_sha256"],
            "files": {
                name: file_hash(prior / name)
                for name in ("plan.json", "results.jsonl", "summary.json")
            },
            "protocol_sha256": file_hash(original_protocol),
        },
    }
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "prior-protocol.md").write_bytes(original_protocol.read_bytes())
    plans = {}
    for key in MODEL_KEYS:
        target = directory / key
        target.mkdir()
        images = {
            case["image"]: draw_image(case["image"], target / (case["image"] + ".png"))
            for case in specification["cases"]
        }
        plan = model_plan(design, key, images)
        validate_model_plan(plan)
        base.write_json(target / "plan.json", plan)
        plans[key] = plan["plan_sha256"]
    manifest = {"design": design, "plans": plans}
    manifest["experiment_sha256"] = base.digest(manifest)
    base.write_json(directory / "experiment.json", manifest)
    validate_experiment(directory)
    return manifest


def validate_experiment(directory: Path) -> dict:
    manifest = base.read_json(directory / "experiment.json")
    assert manifest["experiment_sha256"] == base.digest(
        {k: v for k, v in manifest.items() if k != "experiment_sha256"}
    )
    design = manifest["design"]
    assert design["sources"] == source_hashes(), "Held-out sources changed after freeze"
    assert design["gates"] == GATES
    assert set(manifest["plans"]) == set(MODEL_KEYS)
    assert file_hash(directory / "prior-protocol.md") == design["prior"]["protocol_sha256"]
    for key in MODEL_KEYS:
        plan = base.read_json(directory / key / "plan.json")
        validate_model_plan(plan)
        assert plan["design_sha256"] == base.digest(design)
        assert plan["plan_sha256"] == manifest["plans"][key]
        assert plan == model_plan(design, key, plan["images"])
        for metadata in plan["images"].values():
            assert file_hash(directory / key / metadata["file"]) == metadata["png_sha256"]
    return manifest


@contextmanager
def smaller_plan_validator():
    """Delegate transport/card creation unchanged; only the 13-run validator differs."""
    original = base.validate_plan
    base.validate_plan = validate_model_plan
    try:
        yield
    finally:
        base.validate_plan = original


def run_live(directory: Path, key: str, server: str, environment: Path) -> None:
    manifest = validate_experiment(directory)
    assert base.read_json(environment)["context_size"] == manifest["design"]["context_size"], (
        "Both models require the frozen 4096-token context"
    )
    with smaller_plan_validator():
        base.run_live(directory / key, server, environment)


def scorecards(directory: Path) -> None:
    manifest = validate_experiment(directory)
    assert not (directory / "scorecards.json").exists(), "Preserve existing scoring work"
    cards, mapping = [], {}
    for key in MODEL_KEYS:
        with smaller_plan_validator():
            base.scorecards(directory / key)
        cards.extend(base.read_json(directory / key / "scorecards.json")["cards"])
        mapping.update(
            {
                blind: [key, run_id]
                for blind, run_id in base.read_json(directory / key / "scoring-key.json").items()
            }
        )
    assert len(cards) == len(mapping) == 24
    random.Random(9517).shuffle(cards)
    base.write_json(directory / "scoring-key.json", mapping)
    base.write_json(
        directory / "scorecards.json",
        {"experiment_sha256": manifest["experiment_sha256"], "cards": cards},
    )


def decide(models: dict) -> dict:
    combined = statistics.mean(model["mean_delta"] for model in models.values())
    gates = {
        "neither_model_regresses": all(
            model["mean_delta"] >= GATES["model_mean_min"] for model in models.values()
        ),
        "no_model_task_regresses_over_half": all(
            min(model["task_mean_deltas"].values()) >= GATES["model_task_mean_min"]
            for model in models.values()
        ),
        "combined_gain_at_least_half": combined >= GATES["combined_mean_min"],
        "all_candidate_constraints_pass": all(
            model["candidate_constraints_all_pass"] for model in models.values()
        ),
        "unsupported_not_increased_per_model": all(
            model["unsupported_claims"]["candidate"] <= model["unsupported_claims"]["freeform"]
            for model in models.values()
        ),
        "latency_within_bound_per_model": all(
            model["median_latency_ratio"] <= GATES["model_median_latency_max"]
            for model in models.values()
        ),
        "all_measured_complete": all(model["all_measured_complete"] for model in models.values()),
    }
    return {
        "combined_mean_delta": combined,
        "gate": gates,
        "decision": "ship" if all(gates.values()) else "defer",
    }


def summarize(directory: Path) -> dict:
    manifest = validate_experiment(directory)
    sheet = base.read_json(directory / "scorecards.json")
    assert sheet["experiment_sha256"] == manifest["experiment_sha256"]
    cards = {card["blind_id"]: card for card in sheet["cards"]}
    assert len(cards) == len(sheet["cards"]) == 24
    expected_keys, models = {}, {}
    for key in MODEL_KEYS:
        plan = base.read_json(directory / key / "plan.json")
        results = base.load_results(directory / key, plan)
        measured = {run["id"]: run for run in plan["runs"] if not run["warmup"]}
        cases = {case["id"]: case for case in plan["specification"]["cases"]}
        run_cards, scores = {}, {}
        for run_id, run in measured.items():
            blind = base.digest({"plan": plan["plan_sha256"], "run": run_id})[:16]
            expected_keys[blind] = [key, run_id]
            card = cards[blind]
            case = cases[run["case_id"]]
            assert card["response"] == results[run_id].get("response", "")
            assert card["status"] == results[run_id]["status"]
            assert card["automatic_checks"] == base.automatic_checks(case, card["response"])
            for card_key, value in (
                ("case_id", case["id"]),
                ("prompt", case["prompt"]),
                ("operator_system", case["system_prompt"]),
                ("image", case["image"]),
                ("unsupported_examples", case["unsupported_examples"]),
            ):
                assert card[card_key] == value, "Scorecard task changed"
            for group in ("facts", "constraints"):
                assert [
                    {"id": item["id"], "description": item["description"]} for item in card[group]
                ] == case[group], "Scorecard criterion changed"
            run_cards[run_id] = card
            scores[run_id] = base.score_card(card)
        metric = (
            "execution_ms"
            if all(
                isinstance(results[run_id].get("execution_ms"), (int, float))
                and results[run_id]["execution_ms"] > 0
                for run_id in measured
            )
            else "wall_ms"
        )
        assert all(
            isinstance(results[run_id].get(metric), (int, float)) and results[run_id][metric] > 0
            for run_id in measured
        ), "Missing valid latency evidence"
        pairs = []
        for case in cases.values():
            for seed in plan["specification"]["seeds"]:
                freeform, candidate = (
                    f"{case['id']}-{seed}-{arm}" for arm in ("freeform", "candidate")
                )
                pairs.append(
                    {
                        "case_id": case["id"],
                        "seed": seed,
                        "freeform": scores[freeform],
                        "candidate": scores[candidate],
                        "delta": scores[candidate] - scores[freeform],
                        "latency_ratio": results[candidate][metric] / results[freeform][metric],
                    }
                )
        candidate_cards = [
            card for run_id, card in run_cards.items() if measured[run_id]["arm"] == "candidate"
        ]
        models[key] = {
            "model": plan["model"],
            "plan_sha256": plan["plan_sha256"],
            "pairs": pairs,
            "latency_metric": metric,
            "mean_delta": statistics.mean(pair["delta"] for pair in pairs),
            "task_mean_deltas": {
                case_id: statistics.mean(
                    pair["delta"] for pair in pairs if pair["case_id"] == case_id
                )
                for case_id in cases
            },
            "median_latency_ratio": statistics.median(pair["latency_ratio"] for pair in pairs),
            "candidate_constraints_all_pass": all(
                all(item["passed"] for item in card["constraints"]) for card in candidate_cards
            ),
            "unsupported_claims": {
                arm: sum(
                    len(card["unsupported_claims"])
                    for run_id, card in run_cards.items()
                    if measured[run_id]["arm"] == arm
                )
                for arm in ("freeform", "candidate")
            },
            "all_measured_complete": all(
                results[run_id]["status"] == "complete" for run_id in measured
            ),
            "digit_facts": {
                arm: {
                    "fully_correct": sum(
                        item["score"] == 1
                        for run_id, card in run_cards.items()
                        if measured[run_id]["arm"] == arm
                        for item in card["facts"]
                        if item["id"].startswith("digit_")
                    ),
                    "total": 6,
                }
                for arm in ("freeform", "candidate")
            },
        }
    assert base.read_json(directory / "scoring-key.json") == expected_keys, "Scoring key changed"
    summary = {
        "experiment_sha256": manifest["experiment_sha256"],
        "measured_count": 24,
        "models": models,
        **decide(models),
    }
    base.write_json(directory / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("directory", type=Path)
    prepare_parser.add_argument("--qwen-model", required=True)
    prepare_parser.add_argument("--secondary-model", required=True)
    prepare_parser.add_argument("--prior", type=Path, required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("directory", type=Path)
    run_parser.add_argument("--model-key", choices=MODEL_KEYS, required=True)
    run_parser.add_argument("--server", default="http://127.0.0.1:8188")
    run_parser.add_argument("--environment", type=Path, required=True)
    for command in ("scorecards", "summarize"):
        commands.add_parser(command).add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        manifest = prepare(
            args.directory, {"qwen": args.qwen_model, "secondary": args.secondary_model}, args.prior
        )
        print(f"Frozen 24 measured + 2 warmups: {manifest['experiment_sha256']}")
    elif args.command == "run":
        run_live(args.directory, args.model_key, args.server, args.environment)
    elif args.command == "scorecards":
        scorecards(args.directory)
    else:
        print(json.dumps(summarize(args.directory), indent=2))


if __name__ == "__main__":
    main()
