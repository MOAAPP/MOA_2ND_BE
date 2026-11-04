"""Reproducible Stage 5 synthetic 20-persona evaluation versus fixed six worlds.

Run from backend root: python scripts/eval_stage5.py --output /tmp/stage5_eval.json
No paid LLM credential is used. Costs and live tool-call rate remain unverified.
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import date

from app.services.dataset import load_dataset
from app.services.stage3 import compare_worlds
from app.services.agent_paths import GoalConditions, SearchRequest, search_paths
from app.stage3_models import WorldCompareRequest


def evaluate():
    ds = load_dataset()
    day = date.fromisoformat(ds.meta["snapshot_date"])
    rows = []
    for user in sorted(ds.profiles):
        # The SAME user, amount, clock date, stress and protected bills.
        amount = 7000
        baseline = compare_worlds(ds, WorldCompareRequest(
            user_id=user, as_of=day, horizon_days=14,
            purchase_amount=amount, minimum_required_balance=0,
            savings_amount=amount, investment_amount=amount,
        ))
        base_valid = [
            p for p in baseline.paths if p.status == "calculated"
            and p.minimum_balance >= 0 and p.essential_payments_covered
        ]
        optimized = search_paths(ds, SearchRequest(
            conditions=GoalConditions(user_id=user, as_of=day, purchase_amount=amount,
                                      purchase_required=False, allowed_payment_methods=["debit"],
                                      minimum_balance=0),
            horizon_days=14, max_evaluations=12, max_results=4,
        ))
        violations = [r.id for r in optimized.routes if not r.hard_constraints_met
                      or not all(r.checks.values()) or r.minimum_balance < 0]
        # Record unique dates/method/allocation rather than pretending all new routes are better.
        fixed_signatures = {
            (p.scenario_id, p.purchase_delay_days, p.virtual_savings, p.investment_quantity)
            for p in base_valid
        }
        new = [
            r for r in optimized.routes
            if (r.kind, r.purchase_delay_days, r.savings_amount,
                r.scenario.investment_quantity) not in fixed_signatures
        ]
        rows.append({
            "user_id": user, "fixed_valid_paths": len(base_valid),
            "agent_valid_discovered": optimized.valid_count,
            "recommended": len(optimized.routes),
            "new_vs_fixed_signature_count": len(new),
            "hard_constraint_violations": len(violations),
            "engine_evaluations": optimized.evaluated_count,
            "search_elapsed_ms": optimized.elapsed_ms,
            "agent_status": optimized.status,
        })
    total = sum(x["recommended"] for x in rows)
    return {
        "dataset_id": ds.meta["dataset_id"], "is_synthetic": True,
        "comparison_design": "same_user_date_purchase_amount_and_horizon; LLM parsing excluded",
        "users_evaluated": len(rows),
        "search_valid_route_user_rate": sum(x["recommended"] > 0 for x in rows)/len(rows),
        "hard_violation_rate": sum(x["hard_constraint_violations"] for x in rows)/max(total, 1),
        "total_fixed_six_valid": sum(x["fixed_valid_paths"] for x in rows),
        "total_agent_valid_evaluated": sum(x["agent_valid_discovered"] for x in rows),
        "total_agent_recommended": total,
        "new_recommended_signatures_vs_fixed": sum(x["new_vs_fixed_signature_count"] for x in rows),
        "median_search_ms": round(statistics.median(x["search_elapsed_ms"] for x in rows), 2),
        "llm_tool_call_success_rate": None,
        "llm_cost_usd": None,
        "limitation": "Synthetic evaluation only; signature diversity is not a performance lift. Live LLM API not called.",
        "users": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = evaluate()
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(content + "\n", encoding="utf-8")
    print(content)
