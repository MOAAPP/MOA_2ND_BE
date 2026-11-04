"""Measured-only evaluation for OCR+rules, raw multimodal, and validated multimodal."""
import argparse
import json
from pathlib import Path
from statistics import mean

METHODS = ("ocr_rules","multimodal","validated")

def evaluate(rows, target=50):
    verified = [x for x in rows if x.get("verified_truth") is True and x.get("truth")]
    output = {"target_images":target,"received":len(rows),"verified":len(verified),
              "remaining":max(target-len(verified),0),"methods":{}}
    for name in METHODS:
        actual = [x for x in verified if x.get("predictions",{}).get(name)]
        stats = {"main":[],"price":[],"total_error":[],"option_error":[],"uncertain":[],
                 "latency":[],"cost":[]}
        for row in actual:
            t,p = row["truth"],row["predictions"][name]
            if t.get("product_name") is not None and p.get("product_name") is not None:
                stats["main"].append(int(t["product_name"] == p["product_name"]))
            if t.get("selling_price") is not None and p.get("selling_price") is not None:
                stats["price"].append(int(t["selling_price"] == p["selling_price"]))
            if t.get("estimated_checkout_total") is not None and p.get("estimated_checkout_total") is not None:
                stats["total_error"].append(abs(t["estimated_checkout_total"]-p["estimated_checkout_total"]))
            if t.get("quantity") is not None and p.get("quantity") is not None:
                stats["option_error"].append(int(t["quantity"]!=p["quantity"] or
                    t.get("selected_options")!=p.get("selected_options")))
            if row.get("uncertain_expected") is not None and p.get("needs_user_confirmation") is not None:
                stats["uncertain"].append(int(bool(row["uncertain_expected"])==bool(p["needs_user_confirmation"])))
            if row.get("latencies_ms",{}).get(name) is not None:stats["latency"].append(row["latencies_ms"][name])
            if row.get("costs_usd",{}).get(name) is not None:stats["cost"].append(row["costs_usd"][name])
        output["methods"][name]={"evaluated_images":len(actual),
          "main_product_accuracy":mean(stats["main"]) if stats["main"] else None,
          "selling_price_accuracy":mean(stats["price"]) if stats["price"] else None,
          "checkout_absolute_error_krw":mean(stats["total_error"]) if stats["total_error"] else None,
          "quantity_option_error_rate":mean(stats["option_error"]) if stats["option_error"] else None,
          "uncertainty_accuracy":mean(stats["uncertain"]) if stats["uncertain"] else None,
          "latency_ms_avg":mean(stats["latency"]) if stats["latency"] else None,
          "api_cost_usd_total":sum(stats["cost"]) if stats["cost"] else None}
    return output

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("file",nargs="?",default="docs/eval/stage2_images.jsonl")
    ap.add_argument("--target",type=int,default=50)
    a=ap.parse_args()
    f=Path(a.file)
    rows=[json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()] if f.exists() else []
    print(json.dumps(evaluate(rows,a.target),ensure_ascii=False,indent=2))
