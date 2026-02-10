# Post-Migration Verification & Org Cleanup

## Post-Migration Verification

After migration, verify data integrity by comparing SHA256 hashes across platforms. This is the gold standard before deleting the source repo.

**How it works:**
- HuggingFace: `hf_api.dataset_info(repo_id, files_metadata=True)` -> each sibling's `.lfs.sha256`
- ModelScope: `api.get_dataset_files(repo_id, recursive=True)` -> each file's `Sha256` field (paginate with `page_number`/`page_size`)
- Compare per-file. Skip platform-generated files (`.gitattributes`, `README.md`) as these differ between platforms.

**Gotchas:**
- Re-packed tars with different filenames will have different SHA256 even if the underlying images are identical. SHA256 verification only works for byte-identical files.
- ModelScope `get_dataset_files` returns max 100 per page -- always paginate for large repos.
- HuggingFace `files_metadata=True` may undercount files in repos with deeply nested directories.

## Org Cleanup Workflow

For cleaning up an org with many repos (migrating to ModelScope, then deleting from HF), use the Socratic approach:

1. **Inventory**: List all repos with size, type, visibility, creation date, last update
2. **Check backup status**: For each repo, check if it exists on ModelScope via `repo_exists()`
3. **Verify before delete**: Run SHA256 cross-platform verification on backed-up repos
4. **Present one-by-one**: Show the user each repo with full context and let them decide migrate/delete/keep
5. **Execute**: Migrate in background (parallel for large repos), delete after verification

**Key patterns from real cleanup sessions:**
- Repos named "poc", "test", "v0", or "half_done" are usually safe to delete
- Datasets that are subsets of larger datasets (e.g., 70k subset of 2.6M) are redundant once the parent is backed up
- Re-packed datasets under different orgs (same images, different tar names) are duplicates even though SHA256 won't match -- compare directory structure and file counts instead
- Public model repos (like released checkpoints) should typically stay on HF for community access
- Private training datasets are the best candidates for migrate-then-delete
