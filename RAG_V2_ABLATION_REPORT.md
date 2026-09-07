# LoveApp RAG V2 Dev Ablation Report

The sweep uses raw standalone queries and the Dev split only.

Frozen config: `{"min_score": 0.6, "candidate_limit": 30, "top_k": 5, "reranker_mode": "full", "lexical_weight": 1.5, "metadata_weight": 1.0, "retrieval_text_mode": "question_variants", "hard_filter": true}`

## rag_min_score

| Config | Constraints | Composite | Hit@3 | MRR | nDCG@5 | NoAnswer F1 | FRR | Coverage | HN leak@3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `{"min_score":0.3,"candidate_limit":15,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.8696 | 0.9798 | 0.9777 | 0.9555 | 0.0000 | 1.0000 | 1.0000 | 0.0526 |
| `{"min_score":0.35,"candidate_limit":15,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.8696 | 0.9798 | 0.9777 | 0.9555 | 0.0000 | 1.0000 | 1.0000 | 0.0526 |
| `{"min_score":0.4,"candidate_limit":15,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.8696 | 0.9798 | 0.9777 | 0.9555 | 0.0000 | 1.0000 | 1.0000 | 0.0526 |
| `{"min_score":0.45,"candidate_limit":15,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.8696 | 0.9798 | 0.9777 | 0.9555 | 0.0000 | 1.0000 | 1.0000 | 0.0526 |
| `{"min_score":0.5,"candidate_limit":15,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.8696 | 0.9798 | 0.9777 | 0.9555 | 0.0000 | 1.0000 | 1.0000 | 0.0526 |
| `{"min_score":0.55,"candidate_limit":15,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.8696 | 0.9798 | 0.9777 | 0.9555 | 0.0000 | 1.0000 | 1.0000 | 0.0526 |
| `{"min_score":0.6,"candidate_limit":15,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9196 | 0.9798 | 0.9777 | 0.9555 | 0.5000 | 0.6667 | 1.0000 | 0.0526 |

## candidate_limit

| Config | Constraints | Composite | Hit@3 | MRR | nDCG@5 | NoAnswer F1 | FRR | Coverage | HN leak@3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `{"min_score":0.6,"candidate_limit":15,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9196 | 0.9798 | 0.9777 | 0.9555 | 0.5000 | 0.6667 | 1.0000 | 0.0526 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9318 | 0.9919 | 0.9879 | 0.9676 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |
| `{"min_score":0.6,"candidate_limit":50,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9318 | 0.9919 | 0.9879 | 0.9676 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |

## top_k

| Config | Constraints | Composite | Hit@3 | MRR | nDCG@5 | NoAnswer F1 | FRR | Coverage | HN leak@3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `{"min_score":0.6,"candidate_limit":30,"top_k":3,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9304 | 0.9919 | 0.9879 | 0.9651 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9318 | 0.9919 | 0.9879 | 0.9676 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":7,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9318 | 0.9919 | 0.9879 | 0.9676 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |

## reranker

| Config | Constraints | Composite | Hit@3 | MRR | nDCG@5 | NoAnswer F1 | FRR | Coverage | HN leak@3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"vector_only","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.8945 | 0.9595 | 0.9424 | 0.9118 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"vector_lexical","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9199 | 0.9798 | 0.9718 | 0.9481 | 0.5000 | 0.6667 | 1.0000 | 0.0395 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"vector_metadata","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9269 | 0.9879 | 0.9848 | 0.9599 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9318 | 0.9919 | 0.9879 | 0.9676 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |

## retrieval_text

| Config | Constraints | Composite | Hit@3 | MRR | nDCG@5 | NoAnswer F1 | FRR | Coverage | HN leak@3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"question","hard_filter":false}` | False | 0.9220 | 0.9919 | 0.9899 | 0.9662 | 0.4000 | 0.7500 | 1.0000 | 0.0526 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9586 | 0.9960 | 0.9912 | 0.9749 | 0.7027 | 0.4583 | 1.0000 | 0.0263 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"question_variants_answer","hard_filter":false}` | False | 0.9585 | 0.9960 | 0.9939 | 0.9751 | 0.7027 | 0.4583 | 1.0000 | 0.0395 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"full","hard_filter":false}` | False | 0.9318 | 0.9919 | 0.9879 | 0.9676 | 0.5000 | 0.6667 | 1.0000 | 0.0263 |

## rerank_weights

| Config | Constraints | Composite | Hit@3 | MRR | nDCG@5 | NoAnswer F1 | FRR | Coverage | HN leak@3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.0,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9586 | 0.9960 | 0.9912 | 0.9749 | 0.7027 | 0.4583 | 1.0000 | 0.0263 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":0.5,"metadata_weight":0.5,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9575 | 0.9960 | 0.9885 | 0.9721 | 0.7027 | 0.4583 | 1.0000 | 0.0263 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":0.5,"metadata_weight":1.0,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9568 | 0.9960 | 0.9885 | 0.9720 | 0.7027 | 0.4583 | 1.0000 | 0.0132 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":0.5,"metadata_weight":1.5,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9560 | 0.9960 | 0.9906 | 0.9716 | 0.7027 | 0.4583 | 1.0000 | 0.0000 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":0.5,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9594 | 0.9960 | 0.9912 | 0.9760 | 0.7027 | 0.4583 | 1.0000 | 0.0132 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.0,"metadata_weight":1.5,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9578 | 0.9960 | 0.9933 | 0.9752 | 0.7027 | 0.4583 | 1.0000 | 0.0000 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.5,"metadata_weight":0.5,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9600 | 0.9960 | 0.9919 | 0.9770 | 0.7027 | 0.4583 | 1.0000 | 0.0132 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.5,"metadata_weight":1.0,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9603 | 0.9960 | 0.9939 | 0.9791 | 0.7027 | 0.4583 | 1.0000 | 0.0132 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.5,"metadata_weight":1.5,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9600 | 0.9960 | 0.9939 | 0.9795 | 0.7027 | 0.4583 | 1.0000 | 0.0132 |

## metadata_filter

| Config | Constraints | Composite | Hit@3 | MRR | nDCG@5 | NoAnswer F1 | FRR | Coverage | HN leak@3 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.5,"metadata_weight":1.0,"retrieval_text_mode":"question_variants","hard_filter":false}` | False | 0.9603 | 0.9960 | 0.9939 | 0.9791 | 0.7027 | 0.4583 | 1.0000 | 0.0132 |
| `{"min_score":0.6,"candidate_limit":30,"top_k":5,"reranker_mode":"full","lexical_weight":1.5,"metadata_weight":1.0,"retrieval_text_mode":"question_variants","hard_filter":true}` | True | 0.9611 | 0.9960 | 0.9939 | 0.9440 | 0.9333 | 0.1250 | 1.0000 | 0.0132 |
