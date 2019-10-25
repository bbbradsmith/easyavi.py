#!/usr/bin/env python3
#
# easyavi.py
# Version 1
# Brad Smith, 2019
# http://rainwarrior.ca
#
# Simple way to write an AVI from a series of PIL images.
# Public domain.

# Example usage:
#
# import easyavi
# a = easyavi.open("example.avi", 640, 480, 30) # filename, width, height, FPS
# a.write(frame0) # frame0 is a PIL.Image
# a.write(frame1)
# a.write(frame2)
# ...
# a.close()

# Notes:
#
#   AVI is uncompressed RGB. File size will be large.
#
#   File size is unlimited. Internal file segments will split at around 2GB.

import sys
assert sys.version_info[0] == 3, "Python 3 required."

import PIL.Image
import struct
import builtins

class EasyAvi:

    SEGMENT_MAX = (2 * (1<<30)) - (256 * (1<<20)) # 2GB - 256MB for overhead

    def write_fcc(self,name):
        assert(len(name)==4)
        self.f.write(name.encode("ASCII"))

    def make_fixup(self):
        pos = self.f.tell()
        self.f.write(struct.pack("<L",0)) # placeholder
        return pos

    def push_riff(self,fcc):
        self.write_fcc(fcc)
        self.riff_fixup.append(self.make_fixup())

    def pop_riff(self):
        fixup = self.riff_fixup.pop()
        chunk_size = self.f.tell() - (fixup + 4)
        self.f.seek(fixup,0)
        self.f.write(struct.pack("<L",chunk_size))
        self.f.seek(0,2) # return to end

    def write_prefix(self):
        assert(self.open)
        assert(len(self.riff_fixup) == 0) # top level
        frame_size = self.w * self.h * 3
        # open AVI RIFF list (or AVIX for continued segments)
        self.push_riff("RIFF")
        self.write_fcc("AVIX" if self.avix else "AVI ")
        # open hdrl list (header chunks)
        self.push_riff("LIST")
        self.write_fcc("hdrl")
        # avih chunk (main AVI header)
        self.push_riff("avih")
        self.f.write(struct.pack("<LLLL",
            1000000//self.fps, # us/frame
            frame_size * self.fps, # max bytes per second
            0, # padding
            0x10)) # flags (AVIF_HASINDEX)
        self.frames_fixup.append(self.make_fixup())
        self.f.write(struct.pack("<LLLLLLLLL",
            0, # initial frames (audio delay)
            1, # number of streams
            frame_size, # bytes per frame
            self.w,
            self.h,
            0,0,0,0)) # reserved
        self.pop_riff()
        # open strl list (stream description)
        self.push_riff("LIST")
        self.write_fcc("strl")
        # strh chunk (stream header)
        self.push_riff("strh")
        self.write_fcc("vids")
        self.write_fcc("MSVC") # RGB uncompressed
        self.f.write(struct.pack("<LHHLLLL",
            0, # flags
            0, # priority
            0, # language
            0, # initial frames (audio delay)
            1, # rate divisor
            self.fps, # rate divisor
            self.initial)) # start
        self.frames_fixup.append(self.make_fixup())
        self.f.write(struct.pack("<LlLHHHH",
            frame_size,
            -1, # quality
            0, # sample size
            0,0,self.w,self.h)) # rectangle
        self.pop_riff()
        # strf chunk (stream format, BITMAPINFOHEADER)
        self.push_riff("strf")
        self.f.write(struct.pack("<LllHHLLllLL",
            40, # size of structure
            self.w,
            self.h,
            1, # planes
            24, # bit depth
            0, # BI_RGB
            0, # size of image
            0,0, # pixels per metre in display
            0,0)) # palette colours used, important palette colours
        self.pop_riff()
        # close strl list
        self.pop_riff()
        # close hdrl list
        self.pop_riff()
        # open movi list (video chunks)
        self.push_riff("LIST")
        self.write_fcc("movi")
        # now ready to create frame chunks
        # RIFF AVI/AVIX list, and movi list are currently open
        self.index_pos = 4
        self.segment_size = 0

    def write_suffix(self):
        assert(self.open)
        # close movi list
        self.pop_riff()
        # idx1 chunk
        self.push_riff("idx1")
        assert(len(self.indices) == self.frames)
        for idx in self.indices:
            self.write_fcc(idx[0])
            self.f.write(struct.pack("<LLL",idx[1],idx[2],idx[3]))
        self.pop_riff()
        self.indices = []
        # close RIFF AVI/AVIX list
        self.pop_riff()
        assert(len(self.riff_fixup) == 0) # returned to top level
        # fix up frame count
        for pos in self.frames_fixup:
            self.f.seek(pos,0)
            self.f.write(struct.pack("<L",self.frames))
        self.f.seek(0,2) # return to end
        self.frames_fixup = []
        # reset for next segment
        self.initial += self.frames
        self.frames = 0
        self.avix = True

    def write_frame(self,bgr):
        assert(self.open)
        if (self.segment_size + len(bgr) + 8) >= EasyAvi.SEGMENT_MAX:
            self.write_suffix()
        fcc = "00db"
        self.write_fcc(fcc)
        self.f.write(struct.pack("<L",len(bgr)))
        self.f.write(bgr)
        self.indices.append(( \
            fcc,
            0x10, # flags (AVIIF_KEYFRAME)
            self.index_pos,
            len(bgr)))
        self.index_pos += len(bgr) + 8
        self.frames += 1       

    # constructor/destructor

    def __init__(self, file_handle, w, h, fps):
        self.f = file_handle
        self.w = w
        self.h = h
        self.fps = fps
        self.open = True
        self.avix = False
        self.frames = 0
        self.initial = 0
        self.riff_fixup = []
        self.frames_fixup = []
        self.indices = []
        self.index_pos = 0
        self.segment_size = 0
        self.write_prefix()

    def __del__(self):
        self.close()

    # public interface

    def write(self,img):
        """Writes a PIL.Image as the next frame."""
        assert(img.width==self.w)
        assert(img.height==self.h)
        bgr = img.convert("BGR;24").transpose(PIL.Image.FLIP_TOP_BOTTOM).tobytes()
        self.write_frame(bgr)

    def close(self):
        """Finishes writing to disk and closes AVI file."""
        if (self.open):
            self.write_suffix()
            self.f.close()
            self.open = False

def open(filename, w, h, fps):
    """Opens an AVI file for writing."""
    f = builtins.open(filename,"wb")
    return EasyAvi(f,w,h,fps)
