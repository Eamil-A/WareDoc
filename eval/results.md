# WareDoc Evaluation Results

> Run on 12 questions **(subset — see note)**

## Overall Accuracy

**11/12 = 91.7%**

## Accuracy by Category

| Category | Passed | Total | Accuracy |
| --- | --- | --- | --- |
| calculation | 1 | 1 | 100.0% |
| catalog_price | 0 | 1 | 0.0% |
| catalog_scan | 1 | 1 | 100.0% |
| chart | 1 | 1 | 100.0% |
| multi_doc | 1 | 1 | 100.0% |
| other | 1 | 1 | 100.0% |
| policy | 3 | 3 | 100.0% |
| sales_data | 2 | 2 | 100.0% |
| unanswerable | 1 | 1 | 100.0% |

## Failures

### ❌ [catalog_price] What is the box price of Savon brut (SKU1000) in Catalog A?
- **Expected:** Rs 1,075
- **Predicted:** Not found in documents.
- **Score:** 0.00


## Note on Subset Run

Due to time constraints, only a subset of ~12 questions was run, covering every category.
To run the full 25-question eval: `python eval/run_eval.py --full`