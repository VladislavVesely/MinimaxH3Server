# Recorded despill regression

`magenta-scarf-source.png` is the first native 512×512 RGB frame decoded from the project's preserved `Sprite_Dachshund_Pistol_Idle/original.mp4` (H3 job `dbbe7bd3c4ea4eccb05b1e1a7384b056`). No new inference was performed.

- Source MP4 SHA256: `77d081ac0b24a2f76469b3a768c93b3c59161256bdcacbc5d6268616c41862d4`
- Fixture SHA256: `444ca0b915904e2959815ca421c36f57f93e5bd5135603cf1a8c6143b162abee`
- Decode: `ffmpeg -v error -i original.mp4 -frames:v 1 magenta-scarf-source.png`
- Key: `#ff00ff`.
- Failure: a fixed six-native-pixel reconstruction radius failed to reach clean foreground behind the scarf. At 128×128 output, visible pink pixels survived at `(43,44)`, `(37,46)`, `(38,46)`, `(38,47)`.
- Test verifies removal of this spill, unchanged alpha and unrelated solid colors. This is evidence for this source, not proof that all key-colored subjects can be reconstructed.
