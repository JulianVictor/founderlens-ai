-- Storage bucket for uploaded research documents.
--
-- Object keys are `<workspace_id>/<document_id>.<ext>`. Every segment is a UUID
-- or a server-chosen extension, so nothing user-supplied reaches the key --
-- which is what makes path traversal and key collisions impossible rather than
-- merely unlikely.
--
-- Access model
-- ------------
-- The bucket is private, and **no policy grants `authenticated` any access to
-- it**. A browser holding a user's anon-key session cannot read, write, list or
-- delete an object here at all.
--
-- That is deliberate. Every upload has to be inspected server-side before it is
-- accepted -- the content type is determined from the bytes, not from what the
-- client claims -- and a direct-to-storage upload would bypass exactly that
-- check. So all access is mediated by the API, which uses the service-role key
-- *after* verifying workspace membership. `service_role` bypasses RLS, so it
-- needs no policy.
--
-- The limits below are a second line of defence. The API enforces its own size
-- and type limits before it ever calls Storage; these stop a mistake there from
-- becoming an unbounded write.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'research-documents',
    'research-documents',
    false,
    26214400,  -- 25 MiB, matching MAX_DOCUMENT_BYTES in the API settings
    array['application/pdf', 'text/plain', 'text/markdown']
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

-- Deleting a document row does not delete its object: Postgres has no reach
-- into Storage. Object removal is done by the API as part of deleting a
-- document, before the row is dropped -- see DocumentService.delete_document.
