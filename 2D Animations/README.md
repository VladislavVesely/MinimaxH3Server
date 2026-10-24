# 2D animation workspace

Start Pi in **this directory**, run `/reload` if it is already open, and use:

```text
/sprite <describe any animation, view, timing and whether it should loop>
/sprite help
```

Edit **`2DSpriteAnimation.style.md`** to establish the consistent style for your assets. The existing GPT Image and H3 tools must be loaded. The extension uses the existing private H3 configuration; it does not ask you to paste keys into prompts.

Optional reference:

```text
/sprite {"name":"MyAsset","request":"Describe the complete animation here","reference":"/absolute/path/reference.png","referenceRole":"identity"}
```

Reference roles: `inspiration`, `identity`, or `exact`. Exact uses supplied artwork without GPT Image repainting; supply transparency, or explicitly identify its existing flat background with `referenceBackground`. H3 itself cannot guarantee exact pixels.

Results are placed in a new `Sprite_<Name>/` directory. Open the latest **`export_###/preview.gif`** to review the processed animation. Transparent frames are in **`export_###/frames/`**; the sprite sheet, timing/anchor metadata, contact sheet and quality report are alongside them.

For testing, tell us the request, what you expected, what happened and the output-folder path. Keep the original video and style snapshot. We can reproduce extraction/matting changes without spending another generation.

```text
/sprite resume /absolute/path/Sprite_Name
/sprite {"action":"finish","folder":"/absolute/path/Sprite_Name","processing":{"fps":12,"tolerance":0.08}}
/sprite {"action":"feedback","folder":"/absolute/path/Sprite_Name","feedback":"Expected ..., observed ..."}
```

Detailed setup, limits and developer tests: [`../tools/2DSpriteAnimation/README.md`](../tools/2DSpriteAnimation/README.md).
