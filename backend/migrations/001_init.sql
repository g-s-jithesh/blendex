CREATE TABLE libraries (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL
) STRICT;

CREATE TABLE assets (
  id INTEGER PRIMARY KEY,
  library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  path TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  ext TEXT NOT NULL,
  -- Points at the newest versions row; NULL only between inserting the asset and its first revision.
  current_version_id INTEGER REFERENCES versions(id) ON DELETE SET NULL
) STRICT;

CREATE INDEX assets_library_id ON assets(library_id);

CREATE TABLE versions (
  id INTEGER PRIMARY KEY,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  size INTEGER NOT NULL,
  mtime INTEGER NOT NULL,
  dcc_version TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'converting', 'ready', 'failed', 'unsupported')),
  poly_count INTEGER,
  proxy_poly_count INTEGER,
  error TEXT,
  has_thumbnail INTEGER NOT NULL DEFAULT 0 CHECK (has_thumbnail IN (0, 1)),
  has_proxy INTEGER NOT NULL DEFAULT 0 CHECK (has_proxy IN (0, 1)),
  created_at INTEGER NOT NULL
) STRICT;

CREATE INDEX versions_asset_id ON versions(asset_id);

CREATE TABLE dcc_apps (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  version TEXT,
  executable TEXT NOT NULL UNIQUE,
  exts TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(exts))
) STRICT;

PRAGMA user_version = 1;
