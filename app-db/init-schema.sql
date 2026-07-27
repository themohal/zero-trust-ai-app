-- Phase 4: data layer with row-level security enforced from the first table.
-- Run this after the extension is enabled (see Step 2 in chat).

CREATE TABLE IF NOT EXISTS memories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id text NOT NULL,          -- Alice's JWT `sub` claim
  content text NOT NULL,
  embedding vector(1536),
  source_turn_id text,             -- provenance: which conversation turn produced this
  created_at timestamptz DEFAULT now()
);
ALTER TABLE memories ENABLE ROW LEVEL SECURITY;
CREATE POLICY owner_isolation ON memories
  USING (owner_id = current_setting('app.user_id', true));

CREATE TABLE IF NOT EXISTS rag_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id text NOT NULL,
  owner_id text,                   -- null if department-shared, not user-private
  department text,
  content text NOT NULL,
  embedding vector(1536),
  created_at timestamptz DEFAULT now()
);
ALTER TABLE rag_chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY rag_access ON rag_chunks
  USING (
    owner_id = current_setting('app.user_id', true)
    OR department = current_setting('app.user_department', true)
  );

CREATE TABLE IF NOT EXISTS conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id text NOT NULL,
  title text,
  created_at timestamptz DEFAULT now()
);
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
CREATE POLICY conversations_isolation ON conversations
  USING (owner_id = current_setting('app.user_id', true));

-- Index for fast vector similarity search (cosine distance, adjust to your embedding model)
CREATE INDEX IF NOT EXISTS memories_embedding_idx ON memories
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx ON rag_chunks
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
