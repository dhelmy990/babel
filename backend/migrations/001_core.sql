CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE creators (
  id uuid PRIMARY KEY,
  slug text NOT NULL UNIQUE,
  display_name text NOT NULL,
  profile_color text NOT NULL CHECK (profile_color ~ '^#[0-9A-Fa-f]{6}$'),
  profile_kind text NOT NULL CHECK (profile_kind IN ('personal', 'generated')),
  selector_order integer NOT NULL UNIQUE CHECK (selector_order >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE babels (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES creators(id) ON DELETE RESTRICT,
  title text NOT NULL CHECK (char_length(title) > 0),
  content_html text NOT NULL,
  color text NOT NULL CHECK (color ~ '^#[0-9A-Fa-f]{6}$'),
  content_revision bigint NOT NULL CHECK (content_revision > 0),
  content_hash text NOT NULL CHECK (char_length(content_hash) > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE babels ADD CONSTRAINT babels_owner_id_id_unique UNIQUE (owner_id, id);

CREATE TABLE babel_sources (
  babel_id uuid PRIMARY KEY,
  owner_id uuid NOT NULL,
  provider text NOT NULL CHECK (provider = 'wikipedia'),
  external_page_id bigint NOT NULL CHECK (external_page_id > 0),
  canonical_url text NOT NULL CHECK (char_length(canonical_url) > 0),
  source_revision_id bigint,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  seed_assignment_id uuid,
  declared_title text NOT NULL CHECK (char_length(declared_title) > 0)
);

ALTER TABLE babel_sources ADD CONSTRAINT babel_sources_babel_owner_fk
  FOREIGN KEY (owner_id, babel_id) REFERENCES babels(owner_id, id) ON DELETE CASCADE;
ALTER TABLE babel_sources ADD CONSTRAINT babel_sources_owner_page_unique
  UNIQUE (owner_id, provider, external_page_id);
CREATE UNIQUE INDEX babel_sources_seed_assignment_unique
  ON babel_sources(seed_assignment_id) WHERE seed_assignment_id IS NOT NULL;

CREATE TABLE edges (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES creators(id) ON DELETE RESTRICT,
  source_babel_id uuid NOT NULL,
  target_babel_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (source_babel_id <> target_babel_id)
);

ALTER TABLE edges ADD CONSTRAINT edges_source_owner_fk
  FOREIGN KEY (owner_id, source_babel_id) REFERENCES babels(owner_id, id) ON DELETE CASCADE;
ALTER TABLE edges ADD CONSTRAINT edges_target_owner_fk
  FOREIGN KEY (owner_id, target_babel_id) REFERENCES babels(owner_id, id) ON DELETE CASCADE;
