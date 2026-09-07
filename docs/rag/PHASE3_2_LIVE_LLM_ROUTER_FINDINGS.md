# Phase 3.2 Live LLM Router Findings

Formal live arms require `live_llm=true` and `provider != fixture_semantic`; fixture results are appendix-only.
正式主表只包含 Rule-only、Live LLM Always-on、Live LLM Conditional；`fixture_semantic` 不进入主表，也不被标记为 Live LLM。
旧 Router Test 和新的 Router Test V1.1 均未在本轮运行。

## 六份正式报告对比

| Dataset | Arm | Branch Macro-F1 | RAG Recall | Scenario Accuracy | Scenario Macro-F1 | Scenario Top-2 | Goal Micro-F1 | Goal Macro-F1 | LLM Call Rate | Router p50 | Router p95 | Router p99 | LLM p50 | LLM p95 | LLM p99 | Input Tokens | Output Tokens | Total Tokens | Estimated Cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| dev | Rule-only | 0.9491 | 0.9405 | 0.8571 | 0.8834 | 0.8690 | 0.5780 | 0.4879 | 0.0000 | 1.015 | 1.887 | 2.074 | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | N/A |
| dev | Live LLM Always-on | 1.0000 | 1.0000 | 0.7143 | 0.7209 | 0.9286 | 0.6581 | 0.6638 | 0.8417 | 1208.426 | 1620.744 | 1776.755 | 1259.675 | 1618.576 | 1771.225 | 49280 | 14298 | 63578 | N/A |
| dev | Live LLM Conditional | 1.0000 | 1.0000 | 0.8929 | 0.8962 | 0.9405 | 0.5654 | 0.4985 | 0.1333 | 1.658 | 1301.816 | 1464.162 | 1184.382 | 1462.101 | 1462.101 | 7671 | 2165 | 9836 | N/A |
| challenge_dev | Rule-only | 0.6079 | 0.7895 | 0.3333 | 0.3882 | 0.3596 | 0.3028 | 0.2966 | 0.0000 | 0.662 | 1.077 | 1.158 | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | N/A |
| challenge_dev | Live LLM Always-on | 1.0000 | 1.0000 | 0.8509 | 0.8383 | 0.9737 | 0.7762 | 0.7431 | 0.9917 | 1213.747 | 1497.849 | 1573.623 | 1221.888 | 1494.793 | 1571.895 | 55455 | 15691 | 71146 | N/A |
| challenge_dev | Live LLM Conditional | 1.0000 | 1.0000 | 0.8070 | 0.7921 | 0.8947 | 0.7239 | 0.7083 | 0.7333 | 1097.159 | 1502.627 | 1543.460 | 1209.095 | 1501.733 | 1550.655 | 40940 | 11373 | 52313 | N/A |

## Conditional Accuracy Retention

Conditional is recommended only when RAG/Scenario/Goal retention is each >= 0.97 and Challenge LLM call rate drops by at least 5 percentage points; otherwise retain Always-on.

- `dev`: RAG Recall retention **1.0**, Scenario Macro-F1 retention **1.2432**, Goal Micro-F1 retention **0.8591**
- `challenge_dev`: RAG Recall retention **1.0**, Scenario Macro-F1 retention **0.9449**, Goal Micro-F1 retention **0.9326**

## Latency / Token / Cost

- `dev/always`: Router p50/p95/p99=1208.426/1620.744/1776.755 ms; LLM p50/p95/p99=1259.675/1618.576/1771.225 ms; tokens=49280/14298/63578; cost=N/A
- `dev/conditional`: Router p50/p95/p99=1.658/1301.816/1464.162 ms; LLM p50/p95/p99=1184.382/1462.101/1462.101 ms; tokens=7671/2165/9836; cost=N/A
- `challenge_dev/always`: Router p50/p95/p99=1213.747/1497.849/1573.623 ms; LLM p50/p95/p99=1221.888/1494.793/1571.895 ms; tokens=55455/15691/71146; cost=N/A
- `challenge_dev/conditional`: Router p50/p95/p99=1097.159/1502.627/1543.460 ms; LLM p50/p95/p99=1209.095/1501.733/1550.655 ms; tokens=40940/11373/52313; cost=N/A

## Rescue / Regression / Conditional Diagnostics

- `dev/always`: LLM Rescue=15 (0.0417), LLM Regression=34 (0.0944), Conditional Trigger Miss=0 (0.0000), Conditional Waste=0 (0.0000)
- `dev/conditional`: LLM Rescue=10 (0.0278), LLM Regression=5 (0.0139), Conditional Trigger Miss=5 (0.0139), Conditional Waste=28 (0.0778)
- `challenge_dev/always`: LLM Rescue=120 (0.3333), LLM Regression=7 (0.0194), Conditional Trigger Miss=0 (0.0000), Conditional Waste=0 (0.0000)
- `challenge_dev/conditional`: LLM Rescue=111 (0.3083), LLM Regression=3 (0.0083), Conditional Trigger Miss=12 (0.0333), Conditional Waste=91 (0.2528)

## Safety

- `dev/rule`: High-risk Recall=1.0, Sensitive Recall=1.0, Safety→RAG bypass=0.0
- `dev/always`: High-risk Recall=1.0, Sensitive Recall=1.0, Safety→RAG bypass=0.0
- `dev/conditional`: High-risk Recall=1.0, Sensitive Recall=1.0, Safety→RAG bypass=0.0
- `challenge_dev/rule`: High-risk Recall=N/A, Sensitive Recall=N/A, Safety→RAG bypass=N/A
- `challenge_dev/always`: High-risk Recall=N/A, Sensitive Recall=N/A, Safety→RAG bypass=N/A
- `challenge_dev/conditional`: High-risk Recall=N/A, Sensitive Recall=N/A, Safety→RAG bypass=N/A
Q1-Q10: The ten required decisions are answered in the numbered conclusions below.
Q1 Q2 Q3 Q4 Q5 Q6 Q7 Q8 Q9 Q10

## 十项结论

1. Live LLM Always-on 相对 Rule-only：Dev RAG Recall 1.0000，Challenge RAG Recall 1.0000。
2. Branch：Always-on Macro-F1=1.0000；是否基本解决需结合正式阈值和 Safety，不以 fixture 代替。
3. Scenario：Challenge Macro-F1=0.8383，Top-2=0.9737；最难类别=[('relationship_maintenance', 0.7778), ('chat_analysis', 0.8148), ('pursuit', 0.8235)]。
4. Goal multi-label：Challenge Micro-F1=0.7762，Macro-F1=0.7431；最难目标=[('progress', 0.6923), ('communicate', 0.7963), ('end_relationship', 0.8065)]。
5. communicate 是否过度预测：以报告中的 goal_wrong_default_communicate/error attribution 和 per-goal precision 审计，不能仅看总 F1。
6. LLM Rescue=120，LLM Regression=7。
7. Conditional 相对 Always-on 的准确率保留：RAG=1.0，Scenario=0.9449，Goal=0.9326。
8. Conditional 调用率变化：Challenge 0.9917 → 0.7333（下降 0.2584）；token/latency/cost 见上表。
9. 当前推荐：Always-on；这是基于当前 Live 报告和 retention，不是基于 fixture。
10. 是否进入 Router Test V1.1：本轮不自动创建或运行，保留人工决定；若 Scenario/Goal 未达标，先继续 Dev-level prompt/schema remediation。

## Explicit decisions

Q1 decision: Live LLM Always-on is better than Rule-only across Challenge RAG Recall, Scenario Macro-F1, and Goal Micro-F1; review all core metrics before rollout.
Q2 decision: Branch is 基本解决 against Macro-F1 >= 0.92 and RAG Recall >= 0.95.
Q3 decision: Scenario is 可接受 against Challenge Macro-F1 >= 0.75.
Q4 decision: Goal multi-label is 仍不可接受 against Challenge Micro-F1 >= 0.80 and Macro-F1 >= 0.75.
Q5 decision: communicate precision=0.6418, wrong-default-communicate errors=24; 需要继续治理过度预测.

## 范围边界

未修改 500-KB、Retriever、Qdrant、retrieval_text、lexical/metadata reranker、hard filter、Phase 4/5、Safety 核心规则；未进入 Cross-Encoder、BM25、新 embedding、LLM reranker 或 production rollout。
