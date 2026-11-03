# App icon

`icon-master.png` is the original generated artwork, with a transparent outer
canvas. The regulated DC mark and two binding posts identify a bench supply
without borrowing the hardware manufacturer's logo.

Created with the built-in image-generation tool. Prompt brief:

> One centered desktop app icon: a bold charcoal bench-supply faceplate with
> rounded corners and an upper-right chamfer, a thick amber direct-current
> rail above two aligned dashes, and two circular binding posts below—one
> amber, one off-white, both with dark centers. Flat vector-like geometry,
> generous negative space, strong silhouette at 16–32 pixels. Near-black,
> instrument amber and off-white. Transparent outside the silhouette. No
> text, device model, manufacturer logo, gradients, glow, perspective,
> cables, mockup or multiple alternatives.

`python tools/prepare_icon.py` preserves the artwork and encodes PNG sizes
32–256 plus an ICO containing 16, 24, 32, 48, 64, 128 and 256 pixel images.
This step uses Pillow for resizing and format conversion; the app uses Tk to
load the checked-in PNGs and does not need Pillow at runtime.
