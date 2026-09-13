# dhelmy.stream

The public website serves the approved split-layout study log through Django. The
original Babel graph remains available in `galaxy/index.html` for its later route
integration.

## Local setup

Use Python 3.13 and start the dedicated PostgreSQL 17 service on host port 5433:

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install --python .venv/bin/python -r requirements-dev.txt
docker compose up -d --wait db
python manage.py migrate
npm start
```

Open http://127.0.0.1:8000/. `npm start` calls the virtual environment's Python
directly. To open the preserved Electron app, use `npm run start:desktop`.

Development defaults use the `study_dev` database and credentials. Settings read
only process environment variables and do not load a local `.env` file. Copy
`.env.example` when explicit values are useful. With `DJANGO_DEBUG=false`, the
secret key and every database setting are required.

## Tests and dependency locks

```bash
source .venv/bin/activate
pytest tests/test_pages.py -q
python manage.py check
```

The checked-in requirements files contain exact versions and hashes. Regenerate
them with pip-tools-compatible `uv pip compile`:

```bash
uv pip compile --generate-hashes --output-file requirements.txt requirements.in
uv pip compile --generate-hashes --output-file requirements-dev.txt requirements-dev.in
```
