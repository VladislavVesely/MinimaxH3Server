# Recorded crouch recovery regression

No new inference: exact decoded source frames and previously active RGBA exports.

- `green-native.png`: Pistol CrouchUp native65, job `5eedfc46fd7a44658ef37d6ae439e1d1`; original video SHA256 `f2423b6720e4fd0d77053df9d84077bbc11dda4c76381e3c1ce2e50ca8552eaf`.
- `green-matte.png`: that folder's export003/frame5. Despite a reviewed alpha mask, raw RGB carries severe green outlines. Witnesses `(80,18),(85,44),(104,46),(57,92),(51,111)` must retain opacity but lose the green excess. Five interior fur/cloth witnesses must stay unchanged.
- `warm-native.png`: Pistol CrouchDown native44, job `1769f5b188f94d7cb8e0c79fdddcdfab`; original video SHA256 `c9e2d86e776a6bbdb140e91c7ecd5e593597ac832e67405fae09f8b9c9bd2b41`.
- `warm-matte.png`: that folder's export007/frame6. Nose/shoulder witnesses `(101,57),(78,78)` must not be erased or darkened by a hue-only despill assumption. Alpha remains byte-identical everywhere.

SHA256:
```text
df41dfb8d3a603d2fedbb0366d75cbb2da8361b38d67cb13fba49e71d6d6b72b  green-matte.png
ca35a4c9c5a23c9f6b6d1a9386122fa7c408bac374a74eab942a195a690810c3  green-native.png
679ecac90e7d695f5fd352dac5a8280e62a5dbbe47f39a23a96e1c45c543c922  warm-matte.png
2790a576de9fd6c328040c31e84c60511a579333e0227071295d017c0030b0c4  warm-native.png
```

## Residual whole-stride witnesses

The first repair missed green/cyan phases. `walk-mid-*`, `walk-cyan-*`, `walk-last-*` are actual Pistol CrouchWalk native82/90/95 and then-active export007 frames10/18/23. Job `320e81a5aff04134ba2750ddc373df68`; original MP4 SHA256 `1a6fdce7880e059a9173776e19872a24832fa87b4a68867671731a564ea32436`.

Observed native keys are[23,251,125],[32,249,201],[40,248,245]. The formerly active masks contain205,114,22 pixel witnesses with G−max(R,B)>10. All must retain opacity and lose the forbidden green excess, including the final nearly-cyan key. An unrelated teal palette entry is not permission to leave a green ring on brown fur. Whole-cycle runtime checks cover all72 poses separately.

```text
ec1575f72f0314acb12f6137c01c5c4f1c9b0db7e9839efbb50300ff573788ad  walk-cyan-matte.png
c25ff89b3bb0341c27863005e9af7b93f594c14508ca136bec54f00d73c03a17  walk-cyan-native.png
1706faccb2cf036913058583e8a0560af14422a8c3514962658ff35c707fc4cf  walk-last-matte.png
87dfa05e1b1773165220fc09a87102caa2d3bcdf0bc27444827505fdb7219cd9  walk-last-native.png
ef75c8fcaed72204504cedde679b43591c7d0b0a51f781ae00602209daf61462  walk-mid-matte.png
408a985f53e9938586edabae00012f6611c94d9dcd02e25c1e604d48980d6c7c  walk-mid-native.png
```

The real worker and legacy recovery-export integration are both tested. A raw-RGB bypass fails the green witness by158 levels; a normal fixed-key despill test alone cannot catch that alternative-path regression.
