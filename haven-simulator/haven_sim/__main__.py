"""Haven CLI: offline simulator, Haven API contract evaluation, paid OpenAI planning and execution."""
import argparse
import json
import os
from pathlib import Path
from .evaluation import run_contract_checks
from .feed_api import HavenFeedClient, HavenAPIError
from .models import SimulationConfig
from .simulator import simulate
from .paid_run import PaidConfig, preview_plan, run_paid
from .training_export import prepare_sequences


def build_parser():
    p = argparse.ArgumentParser(description="Haven v0.3: safety-first synthetic feed evaluation")
    p.add_argument("command", choices=["simulate", "evaluate", "plan-openai", "openai-run", "prepare-training"])
    p.add_argument("--users", type=int, default=100)
    p.add_argument("--posts", type=int, default=100)
    p.add_argument("--sessions-per-user", type=int, default=3)
    p.add_argument("--feed-size", type=int, default=8)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--feed-source", choices=["offline", "api"], default="offline")
    p.add_argument("--api-url", default="http://localhost:4000")
    p.add_argument("--engine", choices=["heuristic"], default="heuristic",
                   help="Paid generation uses the separate `openai-run --execute` command")
    p.add_argument("--model", default=os.getenv("OPENAI_MODEL", ""),
                   help="Your actual API model ID, not a marketing name")
    p.add_argument("--price-input", default=os.getenv("OPENAI_INPUT_USD_PER_MILLION", ""),
                   help="Verified standard input USD per one million tokens")
    p.add_argument("--price-output", default=os.getenv("OPENAI_OUTPUT_USD_PER_MILLION", ""),
                   help="Verified output USD per one million tokens")
    p.add_argument("--price-cached-input", default=os.getenv("OPENAI_CACHED_USD_PER_MILLION", ""),
                   help="Optional eligible cached-input price; otherwise no discount assumed")
    p.add_argument("--budget-usd", default="100")
    p.add_argument("--reserve-usd", default="5",
                   help="Unspent local safety buffer for billing variance")
    p.add_argument("--max-calls", type=int, default=50000,
                   help="Max OpenAI requests across ALL resumes of this output directory")
    p.add_argument("--max-output-tokens", type=int, default=600)
    p.add_argument("--progress-every", type=int, default=25,
                   help="Print approximate API spend every N completed sessions; 0 disables")
    p.add_argument("--execute", action="store_true",
                   help="Required for ALL OpenAI calls; no request in plan-openai")
    p.add_argument("--skip-failed", action="store_true",
                   help="On resume, mark ambiguous/failed LLM sessions as heuristic recovery; no duplicate API request")
    p.add_argument("--output", default="output/haven_run")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "prepare-training":
            print(json.dumps(prepare_sequences(Path(args.output)), indent=2))
        elif args.command == "evaluate":
            client = HavenFeedClient(args.api_url)
            health = client.health()
            if not health.get("ok"):
                raise HavenAPIError("Backend /api/health did not return ok=true")
            report = run_contract_checks(client, Path(args.output))
            print(json.dumps({"checks_passed": report["passed"], "checks_total": report["total"],
                              "report": str(Path(args.output) / "contract_checks.json")}, indent=2))
            if not report["all_passed"]:
                parser.exit(1, "Haven API contract evaluation failed.\n")
        elif args.command == "simulate":
            cfg = SimulationConfig(users=args.users, posts=args.posts,
                                   sessions_per_user=args.sessions_per_user,
                                   feed_size=args.feed_size, seed=args.seed,
                                   engine="heuristic", feed_source=args.feed_source,
                                   api_url=args.api_url)
            report = simulate(cfg, Path(args.output))
            print(json.dumps({"output": str(Path(args.output).resolve()),
                              "counts": report["counts"], "safety_audit": report["safety_audit"]}, indent=2))
            if not report["safety_audit"]["passed"]:
                parser.exit(1, "Haven feed safety-property audit failed.\n")
        else:
            cfg = PaidConfig(users=args.users, posts=args.posts,
                             sessions_per_user=args.sessions_per_user,
                             feed_size=args.feed_size, seed=args.seed,
                             feed_source=args.feed_source, api_url=args.api_url,
                             model=args.model, input_rate=args.price_input,
                             output_rate=args.price_output,
                             cached_input_rate=args.price_cached_input,
                             budget_usd=args.budget_usd, reserve_usd=args.reserve_usd,
                             max_calls=args.max_calls, max_output_tokens=args.max_output_tokens)
            if args.command == "plan-openai":
                if args.execute:
                    raise ValueError("plan-openai never executes calls. Use openai-run --execute.")
                print(json.dumps(preview_plan(cfg), indent=2))
            elif args.command == "openai-run":
                if not args.execute:
                    raise ValueError("OpenAI calls are DISABLED until you pass --execute; use plan-openai first")
                report = run_paid(cfg, Path(args.output), skip_failed=args.skip_failed,
                                  execute=True, progress_every=args.progress_every)
                print(json.dumps({"status": report["status"], "stop_reason": report["stop_reason"],
                                  "output": str(Path(args.output).resolve()),
                                  "counts": report["counts"], "openai": report["openai"],
                                  "safety_audit": report["safety_audit"]}, indent=2))
                if not report["safety_audit"]["passed"]:
                    parser.exit(1, "Haven paid run mechanical safety audit failed.\n")
    except (HavenAPIError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Haven error: {exc}\n")


if __name__ == "__main__":
    main()
