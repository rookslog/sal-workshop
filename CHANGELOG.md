# Changelog

## Unreleased

- Project skeleton: the `sal` command, the check gate.
- Views: `audio_basic` (waveform, loudness) and `av_transition` (filmstrip, cuts, black and frozen
  stretches, waveform, loudness), each with a `view.json` that maps time to pixels.
- Rounds: `sal round new`, `sal round build`, and the review page (synchronised playback, switching,
  level matching, looping).
- The leak check refuses media files, large files and private strings in files and commit messages.
- Comments: one file per comment, never edited; `sal round serve` (loopback only, byte ranges),
  saving from the page, `sal comment add`, `sal comment answer`, `sal inbox`.
- The page, second pass: one take is the clock and the others follow; a large picture that stays
  in the window; a play button, a time readout, arrow keys, `C` to comment, `Ctrl`/`Cmd`+`Enter` to
  save. What is heard is tested as sound, not as the state of a control.
- The server answers only its own page (Host, Origin and Content-Type are checked).
- A round keeps its own copy of each take, with paths relative to the round. **`round.json` files
  written before this must be made again with `sal round new`.**
- Charts are never written through an existing link; audio that starts late is drawn where it
  starts; a silent take no longer sets the level for the others; black stretches shorter than the
  minimum are not reported; a span at the very end of a clip can be drawn.
- The leak check reads what Git holds (the commit, the index and every earlier commit), not the
  working copies.
- Comments are listed on the page with their answers; takes of different lengths play to their own
  ends; a comment the server has stored is shown as saved even if the list cannot be read back.
- `sal round serve` takes a free port unless `--port` names one, and says so plainly when the named
  port is in use (the old fixed default failed wherever another program held it).
