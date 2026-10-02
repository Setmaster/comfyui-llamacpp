# Author task profiles

Use **Edit Profile Library** on **llama.cpp Task Profile** to inspect and author
profiles without finding a JSON file on disk. Profiles contain only text:
a system prompt, prompt prefix, and prompt suffix, plus an ID, name, and
description. They do not change the model, samplers, seed, images, constraints,
release policy, or partial-output policy. Freeform remains the only built-in
profile.

## Create or edit a profile

1. Open **Edit Profile Library**.
2. Choose **New Profile**, or choose an existing local profile. **Duplicate
   Profile** makes an editable copy. Freeform is read only.
3. Enter an ID, name, and the text fields you need. IDs use lowercase letters,
   digits, dots, underscores, or hyphens and must start with a letter or digit.
4. Choose **Save Library**. The editor reports whether the save succeeded.
5. Close the editor. Choose the local profile on the node, then choose
   **Update Saved Snapshot** when you want this workflow to use it.

**Save Library and Update Saved Snapshot are separate actions.** A library edit
never silently changes a saved workflow. The update action participates in
Comfy's graph undo: undo restores the previous saved snapshot. Execution uses
that snapshot even when the local library later changes or the profile is removed.

Text is preserved without trimming. Prefix and suffix wrap the user prompt
before it reaches the model; they do not add fixed text to the model's response.
A profile's system prompt fills only an exactly empty system-prompt input. A
space in that input counts as an explicit value.

The editor's **Inspect saved workflow snapshot** section shows the content
currently stored in the workflow, including profiles no longer in your library.
**Copy snapshot to new profile** makes an editable local draft with a new ID.
Saving it still does not update the workflow automatically.

**Remove Profile** removes an entry from the editor's draft. Save Library makes
that removal persistent. Changing an existing profile ID likewise leaves old
workflow snapshots referring to their original ID and content. **Discard edits
and reload** reloads the current library and discards unsaved editor changes.
Closing with unsaved changes asks whether to discard them.

## Import and export

**Export JSON** downloads the editor's current draft, including valid unsaved
edits, as `llamacpp-task-profiles.json`. Freeform is excluded. Keep this file as
a backup or share your own instructions with another user.

**Import JSON** validates a UTF-8 JSON file before changing the draft. It shows
matching profile IDs and offers two explicit choices:

- **Import new IDs only** keeps existing entries when IDs match.
- **Replace matching IDs and import** replaces matching entries with the imported
  content and adds new IDs.

Neither choice writes the library. Review the draft and choose **Save Library**
when ready. Invalid imports leave your existing draft and library unchanged.

The portable format is versioned and contains user profiles only:

```json
{
  "schema_version": 1,
  "profiles": [
    {
      "schema_version": 1,
      "id": "my-format",
      "name": "My format",
      "description": "My own reusable text instructions.",
      "system_prompt": "",
      "prompt_prefix": "",
      "prompt_suffix": "\nUse the format I describe above."
    }
  ]
}
```

All seven profile fields are required. Unknown fields, duplicate JSON keys,
duplicate IDs, a user entry named by the reserved ID `freeform`, malformed UTF-8,
and byte-order marks are rejected. The library permits up to 128 user profiles
and a 1 MiB document. Each profile has a 256 KiB encoded snapshot limit, IDs up to
64 characters, names up to 128 characters, descriptions up to 2,048 characters,
and each prompt text field up to 65,536 characters. These are the existing
profile-format limits; the editor does not expand them.

## Conflicting edits and storage

The library belongs to the current Comfy user. Comfy resolves its storage path
under that user's configured data directory at
`comfyui-llamacpp/profiles.json`. Profiles and export files contain no connection
credentials or inference settings.

Each save includes the content revision read when the library was loaded. If
another editor or an external file edit changed the file, Save Library reports a
conflict and keeps your unsaved draft. Export your draft if you need a backup,
then reload the library and import or reapply the desired changes. A conflict
never silently overwrites the other editor's work.

A successful save uses a validated temporary file in the same directory and an
atomic replacement. Edits through this Comfy process are serialized. An external
program writing during the final compare/replace interval cannot participate in
that lock; avoid editing the file directly while saving from the editor. An
invalid or unreadable existing library is reported rather than overwritten.

The editor is opened by a node action and uses a keyboard-accessible modal dialog.
Its buttons, drafts, import previews, and status are not serialized into the
workflow. App Mode can use the editor where the host exposes the Task Profile
node's action controls; profile execution does not depend on the editor being
available.
