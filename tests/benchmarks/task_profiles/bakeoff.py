"""Fixed paired task-profile evaluation through an already-running Comfy runtime."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import re
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generation.profiles import (  # noqa: E402
    FREEFORM_PROFILE,
    TaskProfileSnapshot,
    apply_task_profile,
    parse_profiles_document,
)


def digest(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_fixtures():
    cases = read_json(FIXTURES / "cases.json")
    profiles = {
        profile.id: profile
        for profile in parse_profiles_document((FIXTURES / "profiles.json").read_bytes())
    }
    assert cases["schema_version"] == 1 and cases["seeds"] == [17, 29]
    assert len(cases["cases"]) == 6
    assert len({case["id"] for case in cases["cases"]}) == 6
    for case in cases["cases"]:
        assert case["candidate"] in profiles
        assert case["family"] in {"text", "vision"}
        assert bool(case["image"]) == (case["family"] == "vision")
        for criteria in (case["facts"], case["constraints"]):
            assert criteria and len({item["id"] for item in criteria}) == len(criteria)
    return cases, profiles


def draw_image(scene: str, destination: Path) -> dict:
    """Use integer geometry and fixed bitmap glyphs; no fonts, downloads, or AI."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 480), "#f5f1e8")
    draw = ImageDraw.Draw(image)
    if scene == "count-relations":
        draw.ellipse((60, 110, 180, 230), fill="#df3030")
        draw.rectangle((275, 110, 385, 220), fill="#2468db")
        draw.rectangle((430, 110, 540, 220), fill="#2468db")
        draw.polygon(((180, 390), (245, 270), (310, 390)), fill="#20934d")
    elif scene == "readable-text":
        draw.rectangle((95, 35, 545, 155), fill="white")
        glyphs = {
            "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
            "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
            "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
            "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
            "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
            "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
        }
        for index, character in enumerate("OPEN 24"):
            for y, row in enumerate(glyphs.get(character, ())):
                for x, pixel in enumerate(row):
                    if pixel == "1":
                        left, top = 156 + index * 48 + x * 8, 67 + y * 8
                        draw.rectangle((left, top, left + 7, top + 7), fill="black")
        draw.ellipse((95, 260, 225, 390), fill="#ee9224")
        draw.rectangle((390, 260, 520, 390), fill="#2468db")
    elif scene == "occlusion-unknown":
        draw.ellipse((100, 70, 380, 350), fill="#2468db")
        draw.rectangle((210, 190, 560, 410), fill="#935d38")
    else:
        raise ValueError(f"Unknown scene: {scene}")
    image.save(destination, format="PNG", optimize=False)
    return {
        "file": destination.name,
        "size": list(image.size),
        "rgb_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
        "png_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def graph_for(case: dict, profile: TaskProfileSnapshot, settings: dict, model: str, seed: int):
    inputs = {
        **copy.deepcopy(settings),
        "prompt": case["prompt"],
        "system_prompt": case["system_prompt"],
        "model": model,
        "seed": seed,
        "server_url": "",
        "profile": ["1", 0],
        "image_amount": int(bool(case["image"])),
    }
    graph = {
        "1": {
            "class_type": "LlamaCppTaskProfile",
            "inputs": {
                "profile_snapshot": profile.to_json(),
            },
        },
        "2": {"class_type": "LlamaCppGenerate", "inputs": inputs},
    }
    if case["image"]:
        graph["3"] = {"class_type": "LoadImage", "inputs": {"image": case["image"] + ".png"}}
        inputs["image_1"] = ["3", 0]
    return graph


def prepare(output: Path, model: str) -> dict:
    specification, profiles = load_fixtures()
    output.mkdir(parents=True, exist_ok=False)
    images = {
        case["image"]: draw_image(case["image"], output / (case["image"] + ".png"))
        for case in specification["cases"]
        if case["image"]
    }
    runs = []

    def append(case, seed, arm, warmup=False):
        profile = profiles[case["candidate"]] if arm == "candidate" else FREEFORM_PROFILE
        graph = graph_for(case, profile, specification["settings"], model, seed)
        effective_prompt, effective_system = apply_task_profile(
            profile, case["prompt"], case["system_prompt"]
        )
        paired = {"inputs": graph["2"]["inputs"], "image": images.get(case["image"])}
        runs.append(
            {
                "id": ("warmup-" if warmup else "") + f"{case['id']}-{seed}-{arm}",
                "case_id": case["id"],
                "family": case["family"],
                "seed": seed,
                "arm": arm,
                "warmup": warmup,
                "graph": graph,
                "pair_sha256": digest(paired),
                "profile_sha256": profile.content_sha256,
                "effective_prompt": effective_prompt,
                "effective_system_prompt": effective_system,
            }
        )

    for family in ("text", "vision"):
        append(
            next(case for case in specification["cases"] if case["family"] == family),
            7,
            "freeform",
            warmup=True,
        )
    for case_index, case in enumerate(specification["cases"]):
        for seed_index, seed in enumerate(specification["seeds"]):
            arms = (
                ("freeform", "candidate")
                if (case_index + seed_index) % 2 == 0
                else ("candidate", "freeform")
            )
            for arm in arms:
                append(case, seed, arm)
    revision = (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False
        ).stdout.strip()
        or "unavailable"
    )
    plan = {
        "schema_version": 1,
        "repo_revision": revision,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model": model,
        "specification": specification,
        "profiles": {key: item.as_dict() for key, item in profiles.items()},
        "images": images,
        "runs": runs,
    }
    plan["plan_sha256"] = digest(plan)
    validate_plan(plan)
    write_json(output / "plan.json", plan)
    return plan


def validate_plan(plan: dict) -> None:
    assert plan["harness_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), (
        "Harness changed; prepare a new experiment directory"
    )
    assert plan["plan_sha256"] == digest(
        {key: value for key, value in plan.items() if key != "plan_sha256"}
    ), "Plan content changed"
    cases = {case["id"]: case for case in plan["specification"]["cases"]}
    pairs = {}
    for run in plan["runs"]:
        case = cases[run["case_id"]]
        snapshot = TaskProfileSnapshot.from_json(run["graph"]["1"]["inputs"]["profile_snapshot"])
        expected_id = case["candidate"] if run["arm"] == "candidate" else "freeform"
        assert snapshot == TaskProfileSnapshot.from_dict(plan["profiles"][expected_id])
        assert snapshot.content_sha256 == run["profile_sha256"]
        assert run["graph"] == graph_for(
            case, snapshot, plan["specification"]["settings"], plan["model"], run["seed"]
        )
        transformed = apply_task_profile(snapshot, case["prompt"], case["system_prompt"])
        assert transformed == (run["effective_prompt"], run["effective_system_prompt"])
        if case["system_prompt"]:
            assert run["effective_system_prompt"] == case["system_prompt"]
        paired = {"inputs": run["graph"]["2"]["inputs"], "image": plan["images"].get(case["image"])}
        assert run["pair_sha256"] == digest(paired)
        if not run["warmup"]:
            pairs.setdefault((run["case_id"], run["seed"]), []).append(run)
    assert len(pairs) == 12 and sum(run["warmup"] for run in plan["runs"]) == 2
    for pair in pairs.values():
        assert {run["arm"] for run in pair} == {"freeform", "candidate"} and len(pair) == 2
        assert pair[0]["pair_sha256"] == pair[1]["pair_sha256"], "Unequal paired controls/input"


def runtime_identity(snapshot: dict, model: str):
    assert snapshot["mode"] == "direct" and snapshot["owned"], "An owned direct runtime is required"
    assert len(snapshot["models"]) == 1
    current = snapshot["models"][0]
    assert model in [current["model_id"], *current["aliases"]], "Running model does not match plan"
    return {
        "runtime_epoch": snapshot["runtime_epoch"],
        "model_id": current["model_id"],
        "projector": current["projector"],
    }


def execution_ms(history: dict):
    stamps = {
        name: details.get("timestamp")
        for name, details in history.get("status", {}).get("messages", [])
        if isinstance(details, dict)
    }
    start, end = stamps.get("execution_start"), stamps.get("execution_success")
    return (
        end - start if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None
    )


def run_live(directory: Path, server: str, environment_path: Path) -> None:
    import requests

    plan = read_json(directory / "plan.json")
    validate_plan(plan)
    environment = read_json(environment_path)
    for key in (
        "repo_revision",
        "model_sha256",
        "projector_sha256",
        "llama_build",
        "comfy_revision",
        "frontend_version",
        "gpu",
        "context_size",
    ):
        assert environment.get(key), f"Missing environment evidence: {key}"
    for key in ("model_sha256", "projector_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", environment[key]), f"Invalid {key}"
    records_path = directory / "results.jsonl"
    assert not records_path.exists(), "Preserve prior results; prepare a new directory to rerun"
    session = requests.Session()
    session.trust_env = False

    def request(method, route, **kwargs):
        response = session.request(method, server.rstrip("/") + route, timeout=(5, 15), **kwargs)
        response.raise_for_status()
        return response.json()

    snapshot = request("GET", "/llamacpp/runtime/discovery")
    identity = runtime_identity(snapshot, plan["model"])
    write_json(
        directory / "environment.json",
        {
            **environment,
            "initial_discovery": snapshot,
            "runtime_identity": identity,
            "plan_sha256": plan["plan_sha256"],
        },
    )
    uploaded = {}
    try:
        for scene, metadata in plan["images"].items():
            image = directory / metadata["file"]
            assert hashlib.sha256(image.read_bytes()).hexdigest() == metadata["png_sha256"]
            with image.open("rb") as stream:
                result = request(
                    "POST",
                    "/upload/image",
                    files={"image": (image.name, stream, "image/png")},
                    data={
                        "type": "input",
                        "subfolder": "profile-bakeoff-" + plan["plan_sha256"][:12],
                    },
                )
            uploaded[scene] = "/".join(
                part for part in (result.get("subfolder"), result["name"]) if part
            )
        for run in plan["runs"]:
            queue = request("GET", "/queue")
            assert not queue["queue_running"] and not queue["queue_pending"], (
                "Comfy queue must be idle"
            )
            before = request("GET", "/llamacpp/runtime/discovery")
            assert runtime_identity(before, plan["model"]) == identity, (
                "Runtime changed during bakeoff"
            )
            graph = copy.deepcopy(run["graph"])
            case = next(
                item for item in plan["specification"]["cases"] if item["id"] == run["case_id"]
            )
            if case["image"]:
                graph["3"]["inputs"]["image"] = uploaded[case["image"]]
            record = {
                "id": run["id"],
                "plan_sha256": plan["plan_sha256"],
                "status": "failed",
                "submitted_graph": graph,
                "before": before,
            }
            started = time.perf_counter()
            try:
                queued = request(
                    "POST", "/prompt", json={"prompt": graph, "client_id": str(uuid.uuid4())}
                )
                record["prompt_id"] = queued["prompt_id"]
                deadline = (
                    time.monotonic() + plan["specification"]["settings"]["request_timeout"] + 30
                )
                while time.monotonic() < deadline:
                    histories = request("GET", "/history/" + record["prompt_id"])
                    if record["prompt_id"] in histories:
                        break
                    time.sleep(0.1)
                else:
                    raise TimeoutError(
                        "Comfy history deadline exceeded; inspect the recorded prompt ID"
                    )
                history = histories[record["prompt_id"]]
                record.update(
                    history=history,
                    wall_ms=(time.perf_counter() - started) * 1000,
                    execution_ms=execution_ms(history),
                )
                assert history["status"]["status_str"] == "success", "Generation did not succeed"
                texts = history["outputs"]["2"]["text"]
                assert len(texts) == 1 and isinstance(texts[0], str)
                record["response"] = texts[0]
                record["after"] = request("GET", "/llamacpp/runtime/discovery")
                assert runtime_identity(record["after"], plan["model"]) == identity, (
                    "Runtime changed"
                )
                record["status"] = "complete"
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                with records_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{run['id']}: {record['wall_ms']:.0f} ms", flush=True)
    finally:
        session.close()


def load_results(directory: Path, plan: dict):
    records = [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    indexed = {record["id"]: record for record in records}
    assert len(indexed) == len(records), "Duplicate results"
    assert set(indexed) == {run["id"] for run in plan["runs"]}, "Incomplete run set"
    assert all(record["plan_sha256"] == plan["plan_sha256"] for record in records)
    for run in plan["runs"]:
        graph = indexed[run["id"]]["submitted_graph"]
        expected = copy.deepcopy(run["graph"])
        if "3" in expected:
            # Comfy uploads can choose a different filename; only that path may vary.
            expected["3"]["inputs"]["image"] = graph["3"]["inputs"]["image"]
        assert graph == expected, "Submitted graph differs from paired plan controls"
    return indexed


def automatic_checks(case: dict, response: str):
    lines = response.strip().splitlines()
    measured_text = (
        lines[1] if case["format"] == "operator_two_lines" and len(lines) == 2 else response
    )
    return {
        "word_limit": len(measured_text.split()) <= case["max_words"],
        "format": (len(lines) == 2 and lines[0] == "PROMPT:")
        if case["format"] == "operator_two_lines"
        else len(lines) == 1,
    }


def scorecards(directory: Path) -> None:
    plan = read_json(directory / "plan.json")
    validate_plan(plan)
    results = load_results(directory, plan)
    cases = {case["id"]: case for case in plan["specification"]["cases"]}
    cards, key = [], {}
    for run in plan["runs"]:
        if run["warmup"]:
            continue
        blind_id = digest({"plan": plan["plan_sha256"], "run": run["id"]})[:16]
        key[blind_id] = run["id"]
        case, result = cases[run["case_id"]], results[run["id"]]
        cards.append(
            {
                "blind_id": blind_id,
                "case_id": case["id"],
                "prompt": case["prompt"],
                "operator_system": case["system_prompt"],
                "image": case["image"],
                "response": result.get("response", ""),
                "status": result["status"],
                "automatic_checks": automatic_checks(case, result.get("response", "")),
                "facts": [{**item, "score": None, "evidence": ""} for item in case["facts"]],
                "constraints": [
                    {**item, "passed": None, "evidence": ""} for item in case["constraints"]
                ],
                "unsupported_examples": case["unsupported_examples"],
                "clarity": None,
                "clarity_evidence": "",
                "unsupported_claims": [],
                "reviewed": False,
            }
        )
    random.Random(1729).shuffle(cards)
    write_json(directory / "scoring-key.json", key)
    write_json(directory / "scorecards.json", {"plan_sha256": plan["plan_sha256"], "cards": cards})


def score_card(card: dict) -> float:
    assert card["reviewed"] is True
    if card["status"] != "complete":
        return 0.0
    assert all(item["score"] in (0, 0.5, 1) and item["evidence"].strip() for item in card["facts"])
    assert all(
        type(item["passed"]) is bool and item["evidence"].strip() for item in card["constraints"]
    )
    assert card["clarity"] in (0, 1, 2) and card["clarity_evidence"].strip()
    assert isinstance(card["unsupported_claims"], list)
    assert all(isinstance(claim, str) and claim.strip() for claim in card["unsupported_claims"])
    for item in card["constraints"]:
        if card["automatic_checks"].get(item["id"]) is False:
            assert item["passed"] is False, (
                "An automatic constraint failure cannot be scored as passing"
            )
    total = (
        4 * statistics.mean(item["score"] for item in card["facts"])
        + 4 * statistics.mean(item["passed"] for item in card["constraints"])
        + card["clarity"]
        - min(4, 2 * len(card["unsupported_claims"]))
    )
    return round(max(0, total), 4)


def summarize(directory: Path) -> dict:
    plan = read_json(directory / "plan.json")
    validate_plan(plan)
    results = load_results(directory, plan)
    sheet = read_json(directory / "scorecards.json")
    key = read_json(directory / "scoring-key.json")
    assert sheet["plan_sha256"] == plan["plan_sha256"]
    measured = {run["id"]: run for run in plan["runs"] if not run["warmup"]}
    assert key == {
        digest({"plan": plan["plan_sha256"], "run": run_id})[:16]: run_id for run_id in measured
    }, "Scoring key differs from the frozen plan"
    cards = {key[card["blind_id"]]: card for card in sheet["cards"]}
    assert len(cards) == len(sheet["cards"]) and set(cards) == set(measured)
    cases = {case["id"]: case for case in plan["specification"]["cases"]}
    for run_id, card in cards.items():
        case = cases[measured[run_id]["case_id"]]
        assert card["response"] == results[run_id].get("response", "")
        assert card["status"] == results[run_id]["status"]
        assert card["automatic_checks"] == automatic_checks(case, card["response"])
        for group in ("facts", "constraints"):
            assert [item["id"] for item in card[group]] == [item["id"] for item in case[group]]
    scores = {run_id: score_card(card) for run_id, card in cards.items()}
    metric = (
        "execution_ms"
        if all(
            isinstance(results[key].get("execution_ms"), (int, float))
            and results[key]["execution_ms"] > 0
            for key in measured
        )
        else "wall_ms"
    )
    summary = {"plan_sha256": plan["plan_sha256"], "latency_metric": metric, "families": {}}
    for family in ("text", "vision"):
        pairs = []
        for case in cases.values():
            if case["family"] != family:
                continue
            for seed in plan["specification"]["seeds"]:
                base, candidate = (
                    f"{case['id']}-{seed}-{arm}" for arm in ("freeform", "candidate")
                )
                pairs.append(
                    {
                        "case_id": case["id"],
                        "seed": seed,
                        "freeform": scores[base],
                        "candidate": scores[candidate],
                        "delta": scores[candidate] - scores[base],
                        "latency_ratio": results[candidate][metric] / results[base][metric],
                    }
                )
        candidate_ids = [
            key
            for key, run in measured.items()
            if run["family"] == family and run["arm"] == "candidate"
        ]
        per_case = {
            case_id: statistics.mean(pair["delta"] for pair in pairs if pair["case_id"] == case_id)
            for case_id in {pair["case_id"] for pair in pairs}
        }
        gate = {
            "mean_gain_at_least_one": statistics.mean(pair["delta"] for pair in pairs) >= 1,
            "no_case_mean_regression": min(per_case.values()) >= 0,
            "at_least_two_cases_improve": sum(value > 0 for value in per_case.values()) >= 2,
            "candidate_constraints_all_pass": all(
                all(item["passed"] for item in cards[key]["constraints"]) for key in candidate_ids
            ),
            "candidate_no_unsupported_claims": all(
                not cards[key]["unsupported_claims"] for key in candidate_ids
            ),
            "candidate_all_complete": all(
                results[key]["status"] == "complete" for key in candidate_ids
            ),
            "median_latency_ratio_at_most_1_25": statistics.median(
                pair["latency_ratio"] for pair in pairs
            )
            <= 1.25,
        }
        summary["families"][family] = {
            "pairs": pairs,
            "mean_delta": statistics.mean(pair["delta"] for pair in pairs),
            "median_latency_ratio": statistics.median(pair["latency_ratio"] for pair in pairs),
            "gate": gate,
            "decision": "eligible_for_followup" if all(gate.values()) else "defer",
        }
    write_json(directory / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("directory", type=Path)
    prepare_parser.add_argument("--model", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("directory", type=Path)
    run_parser.add_argument("--server", default="http://127.0.0.1:8188")
    run_parser.add_argument("--environment", type=Path, required=True)
    for command in ("scorecards", "summarize"):
        commands.add_parser(command).add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        plan = prepare(args.directory, args.model)
        print(f"Prepared {len(plan['runs'])} runs (24 measured, 2 warmups): {plan['plan_sha256']}")
    elif args.command == "run":
        run_live(args.directory, args.server, args.environment)
    elif args.command == "scorecards":
        scorecards(args.directory)
    else:
        print(json.dumps(summarize(args.directory), indent=2))


if __name__ == "__main__":
    main()
