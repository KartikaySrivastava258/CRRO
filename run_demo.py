import json
import os
from app.main import run_engine, OptimizeRequest

if __name__ == '__main__':
    report = run_engine(OptimizeRequest(cases=20000, contacts=4000, incentive_budget_inr=100000))
    os.makedirs('outputs', exist_ok=True)
    with open('outputs/results.json', 'w') as f:
        json.dump(report, f, indent=2, default=str)
    print(json.dumps({"CRRO": next(x for x in report['policies'] if x['policy'] == 'CRRO'), "budget": report['budget_usage']}, indent=2))
