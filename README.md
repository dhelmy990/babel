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

## Publishing study notes

The verified publisher can switch to Admin mode, open **New article** from the
galaxy or timeline, upload a UTF-8 Markdown file, assign logical paths to image
uploads, preview the exact rendered article, and publish it. Existing articles
use the same form at their Edit article link. A title, Babel color, stable slug,
and original publication date remain part of the article; editing changes only
the current revision. The owner can archive an article from its page, which
returns it to the timeline and removes it from public reading.

The application serves uploaded images from private storage and does not need a
live Google account or OAuth configuration to run tests. Browser tests create
ordinary Django test sessions and use disposable test media.
