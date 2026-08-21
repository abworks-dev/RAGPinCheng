# Unified Upload Dropzone Design

## Goal

Unify file and folder selection inside the upload dialog's dashed dropzone so users can see that both can be dragged or selected from the same area.

## Interaction

- Keep the existing dialog title, description, accepted formats, upload confirmation, conflict handling, and folder confirmation flows.
- Change the dropzone prompt to `拖动文件或文件夹到这里`.
- Show two explicit actions inside the dropzone: `选择文件` opens the existing multi-file picker and `选择文件夹` opens the existing directory picker.
- Remove the standalone full-width folder-upload button below the dropzone.
- While scanning or uploading, keep both selection actions disabled and retain the existing `正在读取文件夹…` status.

## Structure And Accessibility

Use a non-label dropzone container because it owns two separate interactive controls. Each control remains a real button with an accessible name and triggers its corresponding hidden file input. Drag events stay on the dropzone container, so files and folders continue through the existing `inspectDroppedUpload` path.

## Scope

Modify only the upload dialog component and its unit and Playwright coverage. Do not change API contracts, accepted formats, folder traversal, confirmation dialogs, conflict resolution, dependencies, or backend behavior.

## Verification

- Unit test that the prompt mentions files and folders, both selection actions are inside the dropzone, and there is no separate folder button outside it.
- Preserve tests for file selection, folder selection, dropped files, and dropped folders.
- Run the focused unit test, frontend build, and the focused Playwright upload workflow at desktop and mobile viewports.

