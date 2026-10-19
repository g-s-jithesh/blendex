ALTER TABLE assets ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1));

CREATE TABLE asset_tags (
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  PRIMARY KEY (asset_id, tag)
) STRICT;

CREATE INDEX asset_tags_tag ON asset_tags(tag);

PRAGMA user_version = 2;
