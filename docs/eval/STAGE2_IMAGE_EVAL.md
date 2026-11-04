# 이미지 인식 검증

정답 검증된 평가 이미지 50장을 목표로 합니다. 현재 평가 자료가 포함되지 않아 실제 측정은 0건입니다. 없는 성능을 통과로 표시하지 않습니다.

평가 데이터는 로컬 JSONL: image_id, verified_truth (사람이 검증했는지), truth, predictions (ocr_rules, multimodal, validated), latencies_ms, costs_usd, uncertain_expected. truth에는 product_name, selling_price, quantity, selected_options, estimated_checkout_total을 넣습니다. predictions에도 동일한 필드를 두고, 확인 필요한 경우 needs_user_confirmation을 표시합니다.

실제 이미지와 정답 확보 후 다음 실행:

    python scripts/eval_stage2.py /path/to/verified_results.jsonl --target 50

분모가 0인 정확도·실측 비용은 null로 남습니다. 세 방법은 같은 이미지에서 비교해야 합니다. 수동 검증 없이 verified_truth를 true로 만들지 마세요. 원본 사진은 개인정보 제거 후 로컬에 보관하고 공개 저장소에 올리지 마세요.
