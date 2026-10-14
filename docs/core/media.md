# media

> Module: `app/core/media.py`
> Last updated: 2026-10-12

## Purpose

Stores integration tool logos and builds the links that display them.
The database keeps only an object key (`integration_tools.logo_path`,
e.g. `integration-tools/logos/surepass/3f1c….png`); every tool listing
returns `logo_url = LOGO_PUBLIC_BASE_URL + "/" + key`.

## Public surface

| Name | What it does |
|------|--------------|
| `decode_logo(text) -> LogoImage` | Decodes base64 or a `data:` URI and checks the image. Raises `InvalidLogoError` |
| `LogoImage` | `content`, `content_type`, `extension` |
| `new_logo_key(prefix, tool_code, extension)` | A fresh key per upload: `<prefix>/<tool_code>/<32 hex>.<ext>` |
| `public_url(key, settings=None)` | The display link, or `None` for no logo |
| `MediaStore` | Protocol: `save(key, image)`, `delete(key)` |
| `S3MediaStore` | `put_object` with `ContentType` and `Cache-Control: public, max-age=31536000, immutable`; `delete_object` |
| `LocalMediaStore` | Files under `LOGO_LOCAL_DIR`, served at `/media` (development) |
| `build_media_store(settings)`, `get_media_store`, `MediaStoreDep` | The configured store |
| `MediaStoreError` | `503 media_store_unavailable` |

## How it works

`decode_logo` strips an optional `data:<mime>;base64,` prefix, decodes
strictly (`validate=True`), and identifies the image by its first bytes,
not by the declared type:

| Type | Signature |
|------|-----------|
| PNG | `89 50 4E 47 0D 0A 1A 0A` |
| JPEG | `FF D8 FF` |
| WebP | `RIFF....WEBP` |

Anything else (SVG, GIF, text), an empty file, or more than 1 MB
(`MAX_LOGO_BYTES`) is rejected. A data URI whose declared type is not
PNG/JPEG/WebP is rejected too. SVG is excluded because it can carry
scripts.

Every upload gets a new key, so the long `Cache-Control` never serves a
stale image; the previous file is deleted after the database change is
committed (see `IntegrationToolService.upsert_tool`). A failed delete
only leaves an orphaned file and is logged.

`LocalMediaStore` refuses keys that resolve outside its folder.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `LOGO_STORAGE_BACKEND` | `local` | `s3` or `local`; must be `s3` in production |
| `LOGO_S3_BUCKET` | — | Required for `s3` |
| `AWS_REGION` | — | Required for `s3` (shared with the secret store) |
| `LOGO_S3_PREFIX` | `integration-tools/logos` | Key prefix |
| `LOGO_PUBLIC_BASE_URL` | `http://127.0.0.1:8001/media` | Public base for links: the bucket URL or a CloudFront domain |
| `LOGO_LOCAL_DIR` | `media` | Folder for `local` |

For `s3`, the prefix must be publicly readable, through a bucket policy
or CloudFront (ACLs are not used):

```json
{
  "Effect": "Allow",
  "Principal": "*",
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::<bucket>/integration-tools/logos/*"
}
```

The service's AWS identity needs `s3:PutObject` and `s3:DeleteObject`
on `arn:aws:s3:::<bucket>/integration-tools/logos/*`.

## Failure modes

- Invalid image → `422` from request validation (the data is not echoed).
- S3 or disk error on save → `503 media_store_unavailable`; the tool is
  not changed.
- Error on delete of a replaced logo → logged warning; the tool keeps the
  new logo.

## Tests

`tests/features/integration_tools/test_tool_logos.py`: upload, keep,
remove, replace, rejected types and sizes, store failure, the link in
every tool listing, and the local store's folder guard.
