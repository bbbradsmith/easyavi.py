# easyavi.py

Simple way to write an AVI from a series of PIL images.

No sound or codecs or other complicated dependencies,
just a video-only AVI series of images, either uncompressed
or with simple RLE compression.

Example:

```Python
import easyavi
a = easyavi.open("example.avi", 640, 480, 30) # filename, width, height, FPS
a.write(frame0) # frame0 is a PIL.Image
a.write(frame1)
a.write(frame2)
# ...
a.close()
```

See comments at the top of the source file for more documentation.

Public domain.

[Patreon](https://www.patreon.com/rainwarrior)
