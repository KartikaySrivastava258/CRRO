install:
	python -m pip install -r requirements.txt
run:
	uvicorn app.main:app --reload
benchmark:
	python run_demo.py
test:
	python -m pytest -q
